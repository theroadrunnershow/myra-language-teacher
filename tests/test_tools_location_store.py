"""Unit tests for :mod:`tools.location_store`."""

from __future__ import annotations

import json
from typing import Optional

import pytest

from tools.location_store import (
    DEFAULT_LOCATION,
    LOCATION_BUCKET_ENV_VAR,
    LocationStore,
    location_store_from_env,
)


# ---------------------------------------------------------------------------
# Fake GCS scaffolding
# ---------------------------------------------------------------------------


class _FakeBlob:
    def __init__(self, *, payload: Optional[str] = None) -> None:
        self.payload = payload
        self.uploaded: list[tuple[str, str]] = []
        self.read_raises: Optional[Exception] = None

    def exists(self) -> bool:
        return self.payload is not None

    def download_as_text(self) -> str:
        if self.read_raises is not None:
            raise self.read_raises
        assert self.payload is not None
        return self.payload

    def upload_from_string(self, data: str, *, content_type: str) -> None:
        self.uploaded.append((data, content_type))
        self.payload = data


class _FakeBucket:
    def __init__(self, blob: _FakeBlob, name: str) -> None:
        self._blob = blob
        self.name = name

    def blob(self, key: str) -> _FakeBlob:
        return self._blob


class _FakeClient:
    def __init__(self, blob: _FakeBlob) -> None:
        self._blob = blob
        self.bucket_calls: list[str] = []

    def bucket(self, name: str) -> _FakeBucket:
        self.bucket_calls.append(name)
        return _FakeBucket(self._blob, name)


def _store_with_fake_gcs(blob: _FakeBlob) -> tuple[LocationStore, _FakeClient]:
    client = _FakeClient(blob)
    store = LocationStore(
        bucket_name="kids-teacher-bucket",
        client_factory=lambda: client,
    )
    return store, client


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_location_is_seattle():
    assert DEFAULT_LOCATION == "Seattle, WA 98177"


def test_get_returns_default_before_load():
    store = LocationStore(bucket_name="b", client_factory=lambda: None)
    assert store.get() == DEFAULT_LOCATION


def test_no_bucket_means_not_gcs_configured():
    store = LocationStore(bucket_name=None)
    assert store.gcs_configured is False


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


async def test_load_with_no_bucket_keeps_default():
    store = LocationStore(bucket_name=None)
    await store.load()
    assert store.get() == DEFAULT_LOCATION


async def test_load_reads_value_from_existing_gcs_object():
    blob = _FakeBlob(payload=json.dumps({"location": "Bangalore, India"}))
    store, _ = _store_with_fake_gcs(blob)
    await store.load()
    assert store.get() == "Bangalore, India"


async def test_load_strips_whitespace_from_persisted_value():
    blob = _FakeBlob(payload=json.dumps({"location": "  San Francisco  "}))
    store, _ = _store_with_fake_gcs(blob)
    await store.load()
    assert store.get() == "San Francisco"


async def test_load_keeps_default_when_gcs_object_missing():
    blob = _FakeBlob(payload=None)  # exists() returns False
    store, _ = _store_with_fake_gcs(blob)
    await store.load()
    assert store.get() == DEFAULT_LOCATION


async def test_load_keeps_default_on_malformed_json():
    blob = _FakeBlob(payload="not json at all")
    store, _ = _store_with_fake_gcs(blob)
    await store.load()
    assert store.get() == DEFAULT_LOCATION


async def test_load_keeps_default_when_payload_lacks_location_key():
    blob = _FakeBlob(payload=json.dumps({"city": "Seattle"}))
    store, _ = _store_with_fake_gcs(blob)
    await store.load()
    assert store.get() == DEFAULT_LOCATION


async def test_load_keeps_default_when_payload_value_is_blank():
    blob = _FakeBlob(payload=json.dumps({"location": "   "}))
    store, _ = _store_with_fake_gcs(blob)
    await store.load()
    assert store.get() == DEFAULT_LOCATION


async def test_load_keeps_default_on_gcs_read_exception():
    blob = _FakeBlob(payload=json.dumps({"location": "Tokyo"}))
    blob.read_raises = RuntimeError("permission denied")
    store, _ = _store_with_fake_gcs(blob)
    await store.load()
    assert store.get() == DEFAULT_LOCATION


async def test_load_is_idempotent_no_second_gcs_read():
    blob = _FakeBlob(payload=json.dumps({"location": "Tokyo"}))
    store, client = _store_with_fake_gcs(blob)
    await store.load()
    await store.load()
    # bucket() called once on the first load — second load short-circuits.
    assert len(client.bucket_calls) == 1


# ---------------------------------------------------------------------------
# set()
# ---------------------------------------------------------------------------


async def test_set_updates_cache_and_writes_gcs():
    blob = _FakeBlob(payload=json.dumps({"location": "Seattle"}))
    store, _ = _store_with_fake_gcs(blob)
    await store.load()
    await store.set("Brooklyn, NY")
    assert store.get() == "Brooklyn, NY"
    assert blob.uploaded[-1][0] == json.dumps({"location": "Brooklyn, NY"})


async def test_set_strips_whitespace():
    blob = _FakeBlob(payload=None)
    store, _ = _store_with_fake_gcs(blob)
    await store.set("  Berlin  ")
    assert store.get() == "Berlin"
    assert json.loads(blob.uploaded[-1][0])["location"] == "Berlin"


async def test_set_rejects_empty_string():
    store = LocationStore(bucket_name=None)
    with pytest.raises(ValueError):
        await store.set("")


async def test_set_rejects_whitespace_only():
    store = LocationStore(bucket_name=None)
    with pytest.raises(ValueError):
        await store.set("   ")


async def test_set_updates_cache_even_when_gcs_write_raises():
    """Cache is best-effort: the model gets the new value for the rest
    of the session even if persistence fails. The exception still
    propagates so the caller can choose to surface ok=False to the
    model."""

    class _ExplodingBlob(_FakeBlob):
        def upload_from_string(self, data, *, content_type):
            raise RuntimeError("network down")

    blob = _ExplodingBlob(payload=json.dumps({"location": "Seattle"}))
    store, _ = _store_with_fake_gcs(blob)
    await store.load()
    with pytest.raises(RuntimeError):
        await store.set("Brooklyn")
    # Cache moved despite the error.
    assert store.get() == "Brooklyn"


async def test_set_no_op_for_gcs_when_not_configured():
    store = LocationStore(bucket_name=None)
    await store.set("Brooklyn")
    assert store.get() == "Brooklyn"


# ---------------------------------------------------------------------------
# location_store_from_env
# ---------------------------------------------------------------------------


def test_location_store_from_env_with_bucket(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(LOCATION_BUCKET_ENV_VAR, "my-bucket")
    store = location_store_from_env()
    assert store.gcs_configured is True


def test_location_store_from_env_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(LOCATION_BUCKET_ENV_VAR, raising=False)
    store = location_store_from_env()
    assert store.gcs_configured is False
    assert store.get() == DEFAULT_LOCATION


def test_location_store_from_env_blank(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(LOCATION_BUCKET_ENV_VAR, "   ")
    store = location_store_from_env()
    assert store.gcs_configured is False
