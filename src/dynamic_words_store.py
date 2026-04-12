from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from language_config import ROMANIZATION_KEYS, SUPPORTED_LESSON_LANGUAGES
from words_db import WORD_DATABASE

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = SUPPORTED_LESSON_LANGUAGES
SYNC_POLICIES = {"never", "session_end", "shutdown"}


def _empty_words_map() -> dict[str, dict[str, dict]]:
    return {language: {} for language in SUPPORTED_LANGUAGES}


def _copy_words(words: dict[str, dict[str, dict]] | None) -> dict[str, dict[str, dict]]:
    copied = _empty_words_map()
    if not words:
        return copied
    for language in SUPPORTED_LANGUAGES:
        copied[language].update(words.get(language, {}))
    return copied


def _merge_words(*parts: dict[str, dict[str, dict]] | None) -> dict[str, dict[str, dict]]:
    merged = _empty_words_map()
    for part in parts:
        if not part:
            continue
        for language in SUPPORTED_LANGUAGES:
            merged[language].update(part.get(language, {}))
    return merged


def _build_seed_words() -> dict[str, dict[str, dict]]:
    seed = _empty_words_map()
    for category, entries in WORD_DATABASE.items():
        for word in entries:
            english = (word.get("english") or "").strip()
            english_lower = english.lower()
            if not english_lower:
                continue
            for language in SUPPORTED_LANGUAGES:
                seed[language][english_lower] = {
                    "english": english,
                    "translation": word.get(language, english),
                    "romanized": word.get(ROMANIZATION_KEYS[language], ""),
                    "emoji": word.get("emoji", ""),
                    "language": language,
                    "category": category,
                }
    return seed


class DynamicWordsStore:
    """Local-file runtime store with optional GCS sync for custom words."""

    def __init__(
        self,
        *,
        enabled: bool,
        local_path: str,
        bucket_name: str,
        object_key: str,
        sync_to_gcs_policy: str = "never",
        flush_interval_sec: int = 21600,
        flush_max_new_words: int = 50,
        refresh_interval_sec: int = 3600,
        client_factory: Optional[Callable[[], object]] = None,
        time_fn: Optional[Callable[[], float]] = None,
    ):
        if sync_to_gcs_policy not in SYNC_POLICIES:
            raise ValueError(
                f"Invalid sync_to_gcs_policy '{sync_to_gcs_policy}'. "
                f"Expected one of {sorted(SYNC_POLICIES)}."
            )

        self.enabled = bool(enabled)
        self.local_path = local_path.strip()
        self.bucket_name = bucket_name.strip()
        self.object_key = object_key.strip()
        self.sync_to_gcs_policy = sync_to_gcs_policy
        self.flush_interval_sec = max(int(flush_interval_sec), 1)
        self.flush_max_new_words = max(int(flush_max_new_words), 1)
        self.refresh_interval_sec = max(int(refresh_interval_sec), 60)

        self._client_factory = client_factory
        self._time_fn = time_fn or time.time

        self._seed_words = _build_seed_words()
        self._words = _copy_words(self._seed_words)
        self._dynamic_words = _empty_words_map()
        self.dirty_words: set[tuple[str, str]] = set()
        self.dirty_count = 0
        self._dirty_since: Optional[float] = None
        self._local_dirty = False

        self.last_generation: Optional[int] = None
        self._last_refresh_at = 0.0
        self._last_flush_at = self._time_fn()

        self._client = None
        self._blob = None

    @property
    def is_configured(self) -> bool:
        return self.enabled

    @property
    def gcs_configured(self) -> bool:
        return self.enabled and bool(self.bucket_name) and bool(self.object_key)

    @property
    def should_sync_on_shutdown(self) -> bool:
        return self.sync_to_gcs_policy == "shutdown"

    def load_snapshot(self) -> None:
        """Hydrate the local runtime file from built-in words, local cache, and GCS."""
        if not self.enabled:
            logger.info("[dynamic_words] disabled; startup load skipped")
            return

        local_dynamic, local_generation, local_dirty_words = self._read_local_snapshot()
        remote_words = _empty_words_map()
        remote_loaded = False

        if self.gcs_configured:
            self._ensure_blob()
            if self._blob is not None:
                try:
                    payload = self._blob.download_as_text()
                    remote_words = self._parse_remote_payload(payload)
                    self.last_generation = self._to_generation(getattr(self._blob, "generation", None))
                    if self.last_generation is None:
                        self.last_generation = 0
                    remote_loaded = True
                    logger.info(
                        "[dynamic_words] loaded remote snapshot dynamic_count=%s generation=%s",
                        self._count_words(remote_words),
                        self.last_generation,
                    )
                except Exception as exc:
                    if self._is_not_found_error(exc):
                        self.last_generation = 0
                        logger.info("[dynamic_words] remote object missing at startup; starting from local cache")
                    else:
                        logger.warning("[dynamic_words] startup remote load failed: %s", exc)
        else:
            logger.info("[dynamic_words] GCS sync disabled or not configured; using local cache only")

        if self.last_generation is None:
            self.last_generation = local_generation

        if remote_loaded:
            local_dirty_overrides = self._dirty_entries_from(local_dynamic, local_dirty_words)
            self._dynamic_words = _merge_words(remote_words, local_dirty_overrides)
        else:
            self._dynamic_words = _copy_words(local_dynamic)

        self._words = _merge_words(self._seed_words, self._dynamic_words)
        self.dirty_words = set(local_dirty_words)
        self.dirty_count = len(self.dirty_words)
        self._dirty_since = self._time_fn() if self.dirty_words else None
        self._local_dirty = True
        self._persist_local_snapshot()
        self._last_refresh_at = self._time_fn()
        logger.info(
            "[dynamic_words] runtime ready dynamic_count=%s total_count=%s local_path=%s",
            self.total_count,
            self.runtime_count,
            self.local_path,
        )

    def refresh_from_object_store(self) -> bool:
        """Merge the current GCS object into the local runtime cache."""
        if not self.gcs_configured:
            return False

        now = self._time_fn()
        if (now - self._last_refresh_at) < self.refresh_interval_sec:
            return False

        self._ensure_blob()
        if self._blob is None:
            return False

        try:
            payload = self._blob.download_as_text()
            remote_words = self._parse_remote_payload(payload)
            dirty_overrides = self._dirty_entries_from(self._dynamic_words, self.dirty_words)
            self._dynamic_words = _merge_words(remote_words, dirty_overrides)
            self._words = _merge_words(self._seed_words, self._dynamic_words)
            self.last_generation = self._to_generation(getattr(self._blob, "generation", None))
            if self.last_generation is None:
                self.last_generation = 0
            self._local_dirty = True
            self._persist_local_snapshot()
            self._last_refresh_at = now
            logger.info(
                "[dynamic_words] refreshed remote snapshot dynamic_count=%s generation=%s",
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
        """Add/update a translated word and mark it dirty for local save and optional GCS sync."""
        if not self.enabled:
            return

        language = (word.get("language") or "").strip()
        english = (word.get("english") or "").strip()
        english_lower = english.lower()
        if language not in SUPPORTED_LANGUAGES or not english_lower:
            return

        entry = {
            "english": english,
            "translation": word.get("translation", english),
            "romanized": word.get("romanized", ""),
            "emoji": word.get("emoji", "✏️"),
            "language": language,
            "category": word.get("category", "custom"),
        }

        self._dynamic_words.setdefault(language, {})[english_lower] = entry
        self._words.setdefault(language, {})[english_lower] = entry
        dirty_key = (language, english_lower)
        if dirty_key not in self.dirty_words:
            self.dirty_words.add(dirty_key)
            self.dirty_count = len(self.dirty_words)
            if self._dirty_since is None:
                self._dirty_since = self._time_fn()
        self._local_dirty = True

    def flush_if_needed(self, force: bool = False) -> bool:
        """Persist the runtime store to the local snapshot file."""
        if not self.enabled or not self.local_path:
            return False
        if not self._local_dirty and not force:
            return False

        now = self._time_fn()
        if not force:
            age = 0 if self._dirty_since is None else (now - self._dirty_since)
            if self.dirty_count < self.flush_max_new_words and age < self.flush_interval_sec:
                return False

        if not self._persist_local_snapshot():
            return False

        self._last_flush_at = now
        return True

    def sync_to_object_store(self, force: bool = False) -> bool:
        """Upload custom words to GCS, keeping built-in words local-only."""
        if not self.gcs_configured:
            return False
        if not self.dirty_words:
            return False

        self._ensure_blob()
        if self._blob is None:
            return False

        payload = self._serialize_remote_payload(self._dynamic_words)
        expected_generation = self.last_generation if self.last_generation is not None else 0

        try:
            self._blob.upload_from_string(
                payload,
                content_type="application/json",
                if_generation_match=expected_generation,
            )
        except Exception as exc:
            if not self._is_precondition_failed_error(exc):
                logger.warning("[dynamic_words] GCS sync failed: %s", exc)
                return False

            logger.info("[dynamic_words] generation conflict; merging dirty words with latest GCS object")
            remote_words, remote_generation = self._download_current_words()
            if remote_words is None:
                return False

            dirty_overrides = self._dirty_entries_from(self._dynamic_words, self.dirty_words)
            self._dynamic_words = _merge_words(remote_words, dirty_overrides)
            self._words = _merge_words(self._seed_words, self._dynamic_words)

            retry_payload = self._serialize_remote_payload(self._dynamic_words)
            try:
                self._blob.upload_from_string(
                    retry_payload,
                    content_type="application/json",
                    if_generation_match=remote_generation,
                )
            except Exception as retry_exc:
                logger.warning("[dynamic_words] GCS sync retry failed: %s", retry_exc)
                return False

        self.last_generation = self._to_generation(getattr(self._blob, "generation", None))
        self.dirty_words.clear()
        self.dirty_count = 0
        self._dirty_since = None
        self._local_dirty = True
        self._persist_local_snapshot()
        logger.info(
            "[dynamic_words] GCS sync success dynamic_count=%s generation=%s",
            self.total_count,
            self.last_generation,
        )
        return True

    @property
    def total_count(self) -> int:
        return self._count_words(self._dynamic_words)

    @property
    def runtime_count(self) -> int:
        return self._count_words(self._words)

    def _persist_local_snapshot(self) -> bool:
        if not self.local_path:
            return False
        payload = self._serialize_local_payload(
            self._words,
            self._dynamic_words,
            self.dirty_words,
            self.last_generation,
        )
        try:
            directory = os.path.dirname(self.local_path) or "."
            os.makedirs(directory, exist_ok=True)
            with open(self.local_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
            self._local_dirty = False
            return True
        except Exception as exc:
            logger.warning("[dynamic_words] failed writing local snapshot %s: %s", self.local_path, exc)
            return False

    def _read_local_snapshot(
        self,
    ) -> tuple[dict[str, dict[str, dict]], Optional[int], set[tuple[str, str]]]:
        if not self.local_path or not os.path.exists(self.local_path):
            return _empty_words_map(), None, set()

        try:
            with open(self.local_path, "r", encoding="utf-8") as handle:
                payload = handle.read()
        except Exception as exc:
            logger.warning("[dynamic_words] failed reading local snapshot %s: %s", self.local_path, exc)
            return _empty_words_map(), None, set()

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("[dynamic_words] invalid local snapshot JSON; ignoring %s", self.local_path)
            return _empty_words_map(), None, set()

        dynamic_obj = data.get("dynamic_words")
        if dynamic_obj is None:
            dynamic_obj = data.get("words", {})

        dirty_words = self._parse_dirty_words(data.get("dirty_words", []))
        last_generation = self._to_generation(data.get("last_generation"))
        return self._normalize_words_obj(dynamic_obj), last_generation, dirty_words

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
            words = self._parse_remote_payload(payload)
            generation = self._to_generation(getattr(self._blob, "generation", None))
            if generation is None:
                generation = 0
            return words, generation
        except Exception as exc:
            logger.warning("[dynamic_words] failed reading latest object for merge: %s", exc)
            return None, None

    @staticmethod
    def _dirty_entries_from(
        words: dict[str, dict[str, dict]],
        dirty_words: set[tuple[str, str]],
    ) -> dict[str, dict[str, dict]]:
        snapshot = _empty_words_map()
        for language, english_lower in dirty_words:
            entry = words.get(language, {}).get(english_lower)
            if entry is None:
                continue
            snapshot.setdefault(language, {})[english_lower] = entry
        return snapshot

    @staticmethod
    def _count_words(words: dict[str, dict[str, dict]]) -> int:
        return sum(len(entries) for entries in words.values())

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
    def _normalize_words_obj(words_obj: object) -> dict[str, dict[str, dict]]:
        if not isinstance(words_obj, dict):
            return _empty_words_map()

        normalized = _empty_words_map()
        for language in SUPPORTED_LANGUAGES:
            lang_entries = words_obj.get(language, {})
            if not isinstance(lang_entries, dict):
                continue
            for english_lower, word in lang_entries.items():
                if not isinstance(word, dict) or not isinstance(english_lower, str):
                    continue
                normalized[language][english_lower] = word
        return normalized

    @classmethod
    def _parse_remote_payload(cls, payload: str) -> dict[str, dict[str, dict]]:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("[dynamic_words] invalid remote payload; using empty state")
            return _empty_words_map()

        words_obj = data.get("words", {}) if isinstance(data, dict) else {}
        return cls._normalize_words_obj(words_obj)

    @staticmethod
    def _parse_dirty_words(raw_dirty_words: object) -> set[tuple[str, str]]:
        if not isinstance(raw_dirty_words, list):
            return set()

        parsed: set[tuple[str, str]] = set()
        for item in raw_dirty_words:
            if not isinstance(item, str) or ":" not in item:
                continue
            language, english_lower = item.split(":", 1)
            language = language.strip()
            english_lower = english_lower.strip()
            if language in SUPPORTED_LANGUAGES and english_lower:
                parsed.add((language, english_lower))
        return parsed

    @staticmethod
    def _serialize_remote_payload(words: dict[str, dict[str, dict]]) -> str:
        payload = {
            "schema_version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "words": words,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _serialize_local_payload(
        words: dict[str, dict[str, dict]],
        dynamic_words: dict[str, dict[str, dict]],
        dirty_words: set[tuple[str, str]],
        last_generation: Optional[int],
    ) -> str:
        payload = {
            "schema_version": 2,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_generation": last_generation,
            "dirty_words": sorted(f"{language}:{english_lower}" for language, english_lower in dirty_words),
            "words": words,
            "dynamic_words": dynamic_words,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
