"""Local-first review store for kids-teacher transcripts and raw child audio.

Mirrors the shape of ``dynamic_words_store.DynamicWordsStore``: in-memory
runtime state, a local JSON snapshot per session, dirty tracking, and an
optional GCS sync layer injected through a ``client_factory``. The two
retention toggles (``transcripts_enabled`` and ``audio_enabled``) are
enforced in code, not just by convention — when a toggle is off, the
corresponding artifact never reaches memory or disk.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from kids_teacher_types import KidsTranscriptEvent, Speaker

logger = logging.getLogger(__name__)

SYNC_POLICIES = {"never", "session_end", "shutdown"}
SCHEMA_VERSION = 1


def _iso_now(time_fn: Callable[[], float]) -> str:
    return datetime.fromtimestamp(time_fn(), tz=timezone.utc).isoformat()


def _safe_speaker_value(speaker: Speaker | str) -> str:
    if isinstance(speaker, Speaker):
        return speaker.value
    return str(speaker)


class KidsReviewStore:
    """Kids-teacher transcript + audio review storage with optional GCS sync."""

    def __init__(
        self,
        *,
        transcripts_enabled: bool,
        audio_enabled: bool,
        retention_days: int = 30,
        local_dir: str,
        bucket_name: str = "",
        object_prefix: str = "kids_review/v1",
        sync_to_gcs_policy: str = "never",
        client_factory: Optional[Callable[[], object]] = None,
        time_fn: Optional[Callable[[], float]] = None,
    ):
        if sync_to_gcs_policy not in SYNC_POLICIES:
            raise ValueError(
                f"Invalid sync_to_gcs_policy '{sync_to_gcs_policy}'. "
                f"Expected one of {sorted(SYNC_POLICIES)}."
            )
        if int(retention_days) < 0:
            raise ValueError("retention_days must be >= 0")

        self.transcripts_enabled = bool(transcripts_enabled)
        self.audio_enabled = bool(audio_enabled)
        self.retention_days = int(retention_days)
        self.local_dir = local_dir.strip()
        self.bucket_name = bucket_name.strip()
        self.object_prefix = object_prefix.strip().strip("/")
        self.sync_to_gcs_policy = sync_to_gcs_policy

        self._client_factory = client_factory
        self._time_fn = time_fn or time.time

        # In-memory session map. Each entry is the record about to be
        # written to <local_dir>/<session_id>/session.json.
        self._sessions: dict[str, dict] = {}
        # Session ids whose on-disk session.json is out of date.
        self._local_dirty: set[str] = set()
        # Session ids that have never been uploaded to GCS (or have new
        # audio files since the last upload).
        self._remote_dirty: set[str] = set()

        self._client: Optional[object] = None
        self._bucket: Optional[object] = None

    # ------------------------------------------------------------------
    # Capability flags
    # ------------------------------------------------------------------
    @property
    def is_enabled(self) -> bool:
        return self.transcripts_enabled or self.audio_enabled

    @property
    def gcs_configured(self) -> bool:
        return (
            self.is_enabled
            and bool(self.bucket_name)
            and self.sync_to_gcs_policy != "never"
        )

    @property
    def should_sync_on_shutdown(self) -> bool:
        return self.gcs_configured and self.sync_to_gcs_policy == "shutdown"

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def start_session(self, session_id: str, metadata: Optional[dict] = None) -> None:
        if not self.is_enabled or not session_id:
            return
        if session_id in self._sessions:
            return
        self._sessions[session_id] = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "started_at": _iso_now(self._time_fn),
            "ended_at": None,
            "metadata": self._sanitize_metadata(metadata),
            "transcripts_enabled": self.transcripts_enabled,
            "audio_enabled": self.audio_enabled,
            "transcripts": [],
            "audio_files": [],
            "_started_at_epoch": self._time_fn(),
            "_ended_at_epoch": None,
        }
        self._local_dirty.add(session_id)

    def record_transcript(self, event: KidsTranscriptEvent) -> None:
        # Hard guardrail: when transcripts are disabled, this method is a
        # complete no-op. No fields are copied into memory or logged.
        if not self.transcripts_enabled:
            return
        session_id = event.session_id
        if not session_id:
            return
        session = self._sessions.get(session_id)
        if session is None:
            # Auto-start so callers can stream events before an explicit
            # start_session (mirrors the flexibility of dynamic_words).
            self.start_session(session_id)
            session = self._sessions.get(session_id)
            if session is None:
                return
        session["transcripts"].append(event.to_dict())
        self._local_dirty.add(session_id)

    def record_audio(
        self,
        session_id: str,
        speaker: Speaker | str,
        audio_bytes: bytes,
        timestamp_ms: int,
        language: Optional[str] = None,
    ) -> Optional[str]:
        # Hard guardrail: when audio retention is off, do not touch disk.
        if not self.audio_enabled or not session_id:
            return None

        session = self._sessions.get(session_id)
        if session is None:
            # Create a minimal session record so the audio has a home,
            # even when transcripts are disabled.
            self.start_session(session_id)
            session = self._sessions.get(session_id)
            if session is None:
                return None

        speaker_value = _safe_speaker_value(speaker)
        filename = f"{int(timestamp_ms)}-{speaker_value}.webm"
        session_dir = os.path.join(self.local_dir, session_id)
        audio_dir = os.path.join(session_dir, "audio")
        audio_path = os.path.join(audio_dir, filename)
        try:
            os.makedirs(audio_dir, exist_ok=True)
            with open(audio_path, "wb") as handle:
                handle.write(audio_bytes)
        except Exception as exc:
            logger.warning("[kids_review] failed writing audio %s: %s", audio_path, exc)
            return None

        rel_path = os.path.join("audio", filename)
        audio_meta = {
            "path": rel_path,
            "speaker": speaker_value,
            "timestamp_ms": int(timestamp_ms),
            "language": language,
        }
        session["audio_files"].append(audio_meta)
        self._local_dirty.add(session_id)
        self._remote_dirty.add(session_id)
        return audio_path

    def end_session(self, session_id: str) -> None:
        if not self.is_enabled or not session_id:
            return
        session = self._sessions.get(session_id)
        if session is None:
            return
        session["ended_at"] = _iso_now(self._time_fn)
        session["_ended_at_epoch"] = self._time_fn()
        self._local_dirty.add(session_id)
        self._persist_session(session_id)
        self._remote_dirty.add(session_id)

    # ------------------------------------------------------------------
    # Flushing + sync
    # ------------------------------------------------------------------
    def flush_if_needed(self, force: bool = False) -> bool:
        if not self.is_enabled or not self.local_dir:
            return False
        if not self._local_dirty and not force:
            return False
        targets = list(self._sessions.keys()) if force else list(self._local_dirty)
        wrote_any = False
        for session_id in targets:
            if self._persist_session(session_id):
                wrote_any = True
        return wrote_any

    def sync_to_object_store(self, force: bool = False) -> bool:
        if not self.gcs_configured:
            return False
        if not self._remote_dirty and not force:
            return False

        self._ensure_bucket()
        if self._bucket is None:
            return False

        # Ensure on-disk snapshots are current before we upload them.
        self.flush_if_needed(force=True)

        targets = list(self._sessions.keys()) if force else list(self._remote_dirty)
        uploaded_any = False
        for session_id in targets:
            if self._upload_session(session_id):
                uploaded_any = True
                self._remote_dirty.discard(session_id)
        return uploaded_any

    # ------------------------------------------------------------------
    # Read helpers used by the admin review routes
    # ------------------------------------------------------------------
    def list_sessions(self) -> list[dict]:
        """Return a lightweight summary of each persisted session.

        Used by the kids-teacher review routes so route code never needs to
        parse the on-disk layout itself. Sorted newest-first by started_at.
        """
        if not self.local_dir or not os.path.isdir(self.local_dir):
            return []
        results: list[dict] = []
        for entry in sorted(os.listdir(self.local_dir)):
            session_dir = os.path.join(self.local_dir, entry)
            json_path = os.path.join(session_dir, "session.json")
            if not os.path.isfile(json_path):
                continue
            try:
                with open(json_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception as exc:
                logger.warning(
                    "[kids_review] failed reading %s: %s", json_path, exc
                )
                continue
            results.append(
                {
                    "session_id": data.get("session_id", entry),
                    "started_at": data.get("started_at"),
                    "ended_at": data.get("ended_at"),
                    "transcripts_enabled": data.get("transcripts_enabled"),
                    "audio_enabled": data.get("audio_enabled"),
                    "transcript_count": len(data.get("transcripts") or []),
                    "audio_file_count": len(data.get("audio_files") or []),
                }
            )
        results.sort(key=lambda row: row.get("started_at") or "", reverse=True)
        return results

    def read_session(self, session_id: str) -> Optional[dict]:
        """Return the full persisted session.json payload for ``session_id``."""
        if not session_id or not self.local_dir:
            return None
        json_path = os.path.join(self.local_dir, session_id, "session.json")
        if not os.path.isfile(json_path):
            return None
        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            logger.warning("[kids_review] failed reading %s: %s", json_path, exc)
            return None

    def prune_expired(self, now: Optional[float] = None) -> int:
        if self.retention_days <= 0 or not self.local_dir:
            return 0
        if not os.path.isdir(self.local_dir):
            return 0
        cutoff = (now if now is not None else self._time_fn()) - (
            self.retention_days * 86400
        )
        deleted = 0
        for entry in os.listdir(self.local_dir):
            session_dir = os.path.join(self.local_dir, entry)
            if not os.path.isdir(session_dir):
                continue
            ended_at_epoch = self._read_ended_at_epoch(session_dir)
            if ended_at_epoch is None or ended_at_epoch >= cutoff:
                continue
            try:
                shutil.rmtree(session_dir)
                deleted += 1
                self._sessions.pop(entry, None)
                self._local_dirty.discard(entry)
                self._remote_dirty.discard(entry)
            except Exception as exc:
                logger.warning("[kids_review] failed pruning %s: %s", session_dir, exc)
        return deleted

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _sanitize_metadata(metadata: Optional[dict]) -> dict:
        if not metadata:
            return {}
        # Shallow copy only — caller owns the original dict.
        return {str(k): v for k, v in metadata.items()}

    def _persist_session(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None or not self.local_dir:
            return False
        session_dir = os.path.join(self.local_dir, session_id)
        json_path = os.path.join(session_dir, "session.json")
        payload = self._serialize_session(session)
        try:
            os.makedirs(session_dir, exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
            self._local_dirty.discard(session_id)
            return True
        except Exception as exc:
            logger.warning("[kids_review] failed writing %s: %s", json_path, exc)
            return False

    @staticmethod
    def _serialize_session(session: dict) -> str:
        public = {
            "schema_version": session["schema_version"],
            "session_id": session["session_id"],
            "started_at": session["started_at"],
            "ended_at": session["ended_at"],
            "metadata": session["metadata"],
            "transcripts_enabled": session["transcripts_enabled"],
            "audio_enabled": session["audio_enabled"],
            "transcripts": session["transcripts"],
            "audio_files": session["audio_files"],
        }
        return json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True)

    def _ensure_bucket(self) -> None:
        if self._bucket is not None:
            return
        try:
            client_factory = self._client_factory
            if client_factory is None:
                from google.cloud import storage  # type: ignore

                client_factory = storage.Client
            self._client = client_factory()
            self._bucket = self._client.bucket(self.bucket_name)
        except Exception as exc:
            logger.warning("[kids_review] failed to init storage client: %s", exc)
            self._bucket = None

    def _upload_session(self, session_id: str) -> bool:
        if self._bucket is None:
            return False
        session_dir = os.path.join(self.local_dir, session_id)
        json_path = os.path.join(session_dir, "session.json")
        if not os.path.exists(json_path):
            return False

        key_prefix = f"{self.object_prefix}/{session_id}" if self.object_prefix else session_id
        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                session_payload = handle.read()
            session_blob = self._bucket.blob(f"{key_prefix}/session.json")
            session_blob.upload_from_string(
                session_payload, content_type="application/json"
            )
        except Exception as exc:
            logger.warning(
                "[kids_review] failed uploading session.json for %s: %s",
                session_id,
                exc,
            )
            return False

        audio_dir = os.path.join(session_dir, "audio")
        if os.path.isdir(audio_dir):
            for filename in sorted(os.listdir(audio_dir)):
                local_path = os.path.join(audio_dir, filename)
                if not os.path.isfile(local_path):
                    continue
                try:
                    audio_blob = self._bucket.blob(f"{key_prefix}/audio/{filename}")
                    audio_blob.upload_from_filename(local_path)
                except Exception as exc:
                    logger.warning(
                        "[kids_review] failed uploading audio %s: %s",
                        local_path,
                        exc,
                    )
                    return False
        return True

    @staticmethod
    def _read_ended_at_epoch(session_dir: str) -> Optional[float]:
        json_path = os.path.join(session_dir, "session.json")
        if not os.path.exists(json_path):
            return None
        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return None
        ended_at = data.get("ended_at")
        if not ended_at:
            return None
        try:
            return datetime.fromisoformat(ended_at).timestamp()
        except Exception:
            return None
