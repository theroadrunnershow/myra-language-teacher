from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class DynamicWordsStore:
    """In-memory dynamic word store backed by a single object in GCS."""

    def __init__(
        self,
        *,
        enabled: bool,
        bucket_name: str,
        object_key: str,
        flush_interval_sec: int = 21600,
        flush_max_new_words: int = 50,
        refresh_interval_sec: int = 3600,
        client_factory: Optional[Callable[[], object]] = None,
        time_fn: Optional[Callable[[], float]] = None,
    ):
        self.enabled = bool(enabled)
        self.bucket_name = bucket_name.strip()
        self.object_key = object_key.strip()
        self.flush_interval_sec = max(int(flush_interval_sec), 1)
        self.flush_max_new_words = max(int(flush_max_new_words), 1)
        self.refresh_interval_sec = max(int(refresh_interval_sec), 60)

        self._client_factory = client_factory
        self._time_fn = time_fn or time.time

        self._words: dict[str, dict[str, dict]] = {"telugu": {}, "assamese": {}}
        self.dirty_words: set[tuple[str, str]] = set()
        self.dirty_count = 0
        self._dirty_since: Optional[float] = None

        self.last_generation: Optional[int] = None
        self._last_refresh_at = 0.0
        self._last_flush_at = self._time_fn()

        self._client = None
        self._blob = None

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.bucket_name) and bool(self.object_key)

    def load_snapshot(self) -> None:
        """Load words object from GCS once during startup."""
        if not self.is_configured:
            logger.info("[dynamic_words] disabled or not configured; startup load skipped")
            return

        self._ensure_blob()
        if self._blob is None:
            return

        try:
            payload = self._blob.download_as_text()
            self._words = self._parse_payload(payload)
            self.last_generation = self._to_generation(getattr(self._blob, "generation", None))
            logger.info(
                "[dynamic_words] loaded startup snapshot count=%s generation=%s",
                self.total_count,
                self.last_generation,
            )
        except Exception as exc:
            if self._is_not_found_error(exc):
                # 0 means "only create if object does not exist" on first write.
                self.last_generation = 0
                self._words = {"telugu": {}, "assamese": {}}
                logger.info("[dynamic_words] object missing at startup; starting empty")
            else:
                logger.warning("[dynamic_words] startup load failed: %s", exc)

        self._last_refresh_at = self._time_fn()

    def refresh_from_object_store(self) -> bool:
        """Refresh local map from object storage (low-frequency background task)."""
        if not self.is_configured:
            return False

        now = self._time_fn()
        if (now - self._last_refresh_at) < self.refresh_interval_sec:
            return False

        self._ensure_blob()
        if self._blob is None:
            return False

        try:
            payload = self._blob.download_as_text()
            remote_words = self._parse_payload(payload)
            dirty_overrides = self._get_dirty_entries_snapshot()
            for language, entries in remote_words.items():
                self._words.setdefault(language, {})
                self._words[language].update(entries)
            for language, entries in dirty_overrides.items():
                self._words.setdefault(language, {})
                self._words[language].update(entries)
            self.last_generation = self._to_generation(getattr(self._blob, "generation", None))
            self._last_refresh_at = now
            logger.info(
                "[dynamic_words] refreshed count=%s generation=%s",
                self.total_count,
                self.last_generation,
            )
            return True
        except Exception as exc:
            if self._is_not_found_error(exc):
                self._last_refresh_at = now
                return False
            logger.warning("[dynamic_words] refresh failed: %s", exc)
            return False

    def lookup(self, english_word: str, language: str) -> Optional[dict]:
        english_lower = english_word.lower().strip()
        if not english_lower:
            return None
        return self._words.get(language, {}).get(english_lower)

    def upsert(self, word: dict) -> None:
        """Add/update a translated word and mark it dirty for batched flush."""
        if not self.is_configured:
            return

        language = (word.get("language") or "").strip()
        english = (word.get("english") or "").strip()
        english_lower = english.lower()
        if language not in {"telugu", "assamese"} or not english_lower:
            return

        entry = {
            "english": english,
            "translation": word.get("translation", english),
            "romanized": word.get("romanized", ""),
            "emoji": word.get("emoji", "✏️"),
            "language": language,
            "category": word.get("category", "custom"),
        }

        self._words.setdefault(language, {})[english_lower] = entry
        dirty_key = (language, english_lower)
        if dirty_key not in self.dirty_words:
            self.dirty_words.add(dirty_key)
            self.dirty_count = len(self.dirty_words)
            if self._dirty_since is None:
                self._dirty_since = self._time_fn()

    def flush_if_needed(self, force: bool = False) -> bool:
        """Persist dirty words to object storage using optimistic concurrency."""
        if not self.is_configured or not self.dirty_words:
            return False

        now = self._time_fn()
        if not force:
            age = 0 if self._dirty_since is None else (now - self._dirty_since)
            if self.dirty_count < self.flush_max_new_words and age < self.flush_interval_sec:
                return False

        self._ensure_blob()
        if self._blob is None:
            return False

        payload = self._serialize_payload(self._words)
        expected_generation = self.last_generation if self.last_generation is not None else 0

        try:
            self._blob.upload_from_string(
                payload,
                content_type="application/json",
                if_generation_match=expected_generation,
            )
        except Exception as exc:
            if not self._is_precondition_failed_error(exc):
                logger.warning("[dynamic_words] flush failed: %s", exc)
                return False

            logger.info("[dynamic_words] generation conflict; merging with latest object")
            remote_words, remote_generation = self._download_current_words()
            if remote_words is None:
                return False

            dirty_overrides = self._get_dirty_entries_snapshot()
            for language, entries in remote_words.items():
                self._words.setdefault(language, {})
                self._words[language].update(entries)
            for language, entries in dirty_overrides.items():
                self._words.setdefault(language, {})
                self._words[language].update(entries)

            retry_payload = self._serialize_payload(self._words)
            try:
                self._blob.upload_from_string(
                    retry_payload,
                    content_type="application/json",
                    if_generation_match=remote_generation,
                )
            except Exception as retry_exc:
                logger.warning("[dynamic_words] flush retry failed: %s", retry_exc)
                return False

        self.last_generation = self._to_generation(getattr(self._blob, "generation", None))
        self.dirty_words.clear()
        self.dirty_count = 0
        self._dirty_since = None
        self._last_flush_at = now
        logger.info(
            "[dynamic_words] flush success count=%s generation=%s",
            self.total_count,
            self.last_generation,
        )
        return True

    @property
    def total_count(self) -> int:
        return sum(len(entries) for entries in self._words.values())

    def _ensure_blob(self) -> None:
        if self._blob is not None:
            return
        try:
            client_factory = self._client_factory
            if client_factory is None:
                from google.cloud import storage  # type: ignore

                client_factory = storage.Client

            self._client = client_factory()
            bucket = self._client.bucket(self.bucket_name)
            self._blob = bucket.blob(self.object_key)
        except Exception as exc:
            logger.warning("[dynamic_words] failed to initialize storage client: %s", exc)
            self._blob = None

    def _download_current_words(self) -> tuple[Optional[dict[str, dict[str, dict]]], Optional[int]]:
        try:
            payload = self._blob.download_as_text()
            words = self._parse_payload(payload)
            generation = self._to_generation(getattr(self._blob, "generation", None))
            if generation is None:
                generation = 0
            return words, generation
        except Exception as exc:
            logger.warning("[dynamic_words] failed reading latest object for merge: %s", exc)
            return None, None

    def _get_dirty_entries_snapshot(self) -> dict[str, dict[str, dict]]:
        snapshot: dict[str, dict[str, dict]] = {}
        for language, english_lower in self.dirty_words:
            entry = self._words.get(language, {}).get(english_lower)
            if entry is None:
                continue
            snapshot.setdefault(language, {})[english_lower] = entry
        return snapshot

    @staticmethod
    def _to_generation(value: object) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        return exc.__class__.__name__ == "NotFound"

    @staticmethod
    def _is_precondition_failed_error(exc: Exception) -> bool:
        return exc.__class__.__name__ == "PreconditionFailed"

    @staticmethod
    def _parse_payload(payload: str) -> dict[str, dict[str, dict]]:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("[dynamic_words] invalid JSON payload; using empty state")
            return {"telugu": {}, "assamese": {}}

        words_obj = data.get("words", {}) if isinstance(data, dict) else {}
        if not isinstance(words_obj, dict):
            return {"telugu": {}, "assamese": {}}

        normalized: dict[str, dict[str, dict]] = {"telugu": {}, "assamese": {}}
        for language in ("telugu", "assamese"):
            lang_entries = words_obj.get(language, {})
            if not isinstance(lang_entries, dict):
                continue
            for english_lower, word in lang_entries.items():
                if not isinstance(word, dict):
                    continue
                if not isinstance(english_lower, str):
                    continue
                normalized[language][english_lower] = word
        return normalized

    @staticmethod
    def _serialize_payload(words: dict[str, dict[str, dict]]) -> str:
        payload = {
            "schema_version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "words": words,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
