"""Persistent store for the kid's currently-registered location.

A single JSON object — ``{"location": "Seattle, WA 98177"}`` — stored
in GCS at ``kids_teacher/location.json``. The cache loads once at
process startup; reads always hit the cache; writes update the cache
*and* the GCS object together. When GCS isn't configured (no bucket
env var, or the SDK is missing) the store still works as an
in-memory default holder — the location stays at whatever ``set()``
last wrote for the lifetime of the process.

Same general pattern as :mod:`dynamic_words_store`, but for a single
string value instead of a per-language word map.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# Pre-populated value used when the GCS object is missing on first
# load (or GCS isn't configured at all). Per the plan, V1 ships with
# Seattle hard-coded as Myra's home.
DEFAULT_LOCATION = "Seattle, WA 98177"
DEFAULT_OBJECT_KEY = "kids_teacher/location.json"
LOCATION_BUCKET_ENV_VAR = "KIDS_TEACHER_LOCATION_BUCKET"


class LocationStore:
    """In-memory cache of the kid's current location, persisted to GCS."""

    def __init__(
        self,
        *,
        bucket_name: Optional[str] = None,
        object_key: str = DEFAULT_OBJECT_KEY,
        default: str = DEFAULT_LOCATION,
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._bucket_name = (bucket_name or "").strip() or None
        self._object_key = object_key
        self._default = default
        self._client_factory = client_factory
        self._location = default
        self._loaded = False
        # Lazy-created on first async use. Constructing :class:`asyncio.Lock`
        # eagerly fails on Python 3.9 when no event loop is set in the
        # current thread (e.g. in tests that only exercise sync paths).
        self._lock: Optional[asyncio.Lock] = None

    @property
    def gcs_configured(self) -> bool:
        return self._bucket_name is not None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def get(self) -> str:
        return self._location

    async def load(self) -> None:
        """Read the GCS object once. Idempotent.

        On any error (missing object, malformed JSON, network) the
        default stays in place so the model can still answer
        "where do we live?" while the kid (or parent) re-registers.
        """
        async with self._get_lock():
            if self._loaded:
                return
            self._loaded = True
            if not self.gcs_configured:
                logger.info(
                    "[location_store] no GCS bucket configured — staying with default %r",
                    self._default,
                )
                return
            try:
                payload = await asyncio.get_event_loop().run_in_executor(
                    None, self._gcs_read
                )
            except Exception as exc:
                logger.warning(
                    "[location_store] GCS read failed: %s — using default %r",
                    exc,
                    self._default,
                )
                return
            if payload is None:
                logger.info(
                    "[location_store] GCS object %r missing — using default %r",
                    self._object_key,
                    self._default,
                )
                return
            value = self._parse(payload)
            if value is None:
                logger.warning(
                    "[location_store] GCS object %r malformed — using default %r",
                    self._object_key,
                    self._default,
                )
                return
            self._location = value
            logger.info(
                "[location_store] loaded %r from gs://%s/%s",
                self._location,
                self._bucket_name,
                self._object_key,
            )

    async def set(self, location: str) -> None:
        """Update the cache and persist to GCS.

        The cache is updated even if the GCS write fails — the model
        gets the new value for the rest of the session, and the kid
        avoids being asked twice in a row. The exception still
        propagates so the caller can surface it (the
        :class:`RegisterCurrentLocationTool` translates it into an
        ``ok=False`` payload for the model).
        """
        cleaned = (location or "").strip()
        if not cleaned:
            raise ValueError("location must be non-empty")
        async with self._get_lock():
            self._location = cleaned
            if not self.gcs_configured:
                return
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._gcs_write, cleaned
                )
            except Exception as exc:
                logger.warning("[location_store] GCS write failed: %s", exc)
                raise

    @staticmethod
    def _parse(payload: str) -> Optional[str]:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        value = data.get("location")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _gcs_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        from google.cloud import storage  # type: ignore  # lazy import
        return storage.Client()

    def _gcs_read(self) -> Optional[str]:
        client = self._gcs_client()
        bucket = client.bucket(self._bucket_name)
        blob = bucket.blob(self._object_key)
        if not blob.exists():
            return None
        return blob.download_as_text()

    def _gcs_write(self, location: str) -> None:
        client = self._gcs_client()
        bucket = client.bucket(self._bucket_name)
        blob = bucket.blob(self._object_key)
        blob.upload_from_string(
            json.dumps({"location": location}),
            content_type="application/json",
        )


def location_store_from_env() -> LocationStore:
    """Construct a :class:`LocationStore` from environment variables.

    Honors :data:`LOCATION_BUCKET_ENV_VAR`. Returns an in-memory-only
    store when the env var is unset or blank.
    """
    bucket = os.environ.get(LOCATION_BUCKET_ENV_VAR, "").strip()
    return LocationStore(bucket_name=bucket or None)


__all__ = [
    "DEFAULT_LOCATION",
    "DEFAULT_OBJECT_KEY",
    "LOCATION_BUCKET_ENV_VAR",
    "LocationStore",
    "location_store_from_env",
]
