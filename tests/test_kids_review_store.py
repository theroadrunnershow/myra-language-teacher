"""Tests for KidsReviewStore (KT-I3-01).

These tests exercise retention toggles, safety guardrails, and the
optional GCS sync path using injected fakes so the test suite never
touches the real network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kids_review_store import KidsReviewStore
from kids_teacher_types import KidsTranscriptEvent, Speaker


# ---------------------------------------------------------------------------
# Fake GCS client
# ---------------------------------------------------------------------------


class FakeBlob:
    def __init__(self, key: str, uploads: list[dict]):
        self.key = key
        self._uploads = uploads

    def upload_from_string(self, payload, content_type=None):
        self._uploads.append(
            {
                "key": self.key,
                "kind": "string",
                "content_type": content_type,
                "payload": payload,
            }
        )

    def upload_from_filename(self, filename):
        self._uploads.append(
            {
                "key": self.key,
                "kind": "file",
                "filename": filename,
            }
        )


class FakeBucket:
    def __init__(self, name: str, uploads: list[dict]):
        self.name = name
        self._uploads = uploads

    def blob(self, key: str) -> FakeBlob:
        return FakeBlob(key, self._uploads)


class FakeClient:
    def __init__(self, uploads: list[dict], bucket_names: list[str]):
        self._uploads = uploads
        self._bucket_names = bucket_names

    def bucket(self, name: str) -> FakeBucket:
        self._bucket_names.append(name)
        return FakeBucket(name, self._uploads)


def _client_factory(uploads, bucket_names, instantiations):
    def factory():
        instantiations.append(True)
        return FakeClient(uploads, bucket_names)

    return factory


class FakeClock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_event(
    session_id: str,
    text: str,
    *,
    speaker: Speaker = Speaker.CHILD,
    timestamp_ms: int = 1000,
    language: str = "english",
) -> KidsTranscriptEvent:
    return KidsTranscriptEvent(
        speaker=speaker,
        text=text,
        is_partial=False,
        timestamp_ms=timestamp_ms,
        session_id=session_id,
        language=language,
    )


# ---------------------------------------------------------------------------
# 1. Both flags False
# ---------------------------------------------------------------------------


def test_both_flags_false_is_complete_noop(tmp_path):
    local_dir = tmp_path / "kids_review"
    store = KidsReviewStore(
        transcripts_enabled=False,
        audio_enabled=False,
        local_dir=str(local_dir),
    )
    assert store.is_enabled is False

    store.start_session("sess-1", metadata={"child_age_band": "4-5"})
    store.record_transcript(_make_event("sess-1", "hello"))
    path = store.record_audio("sess-1", Speaker.CHILD, b"\x00\x01", timestamp_ms=5)
    store.end_session("sess-1")

    assert path is None
    assert not local_dir.exists()


# ---------------------------------------------------------------------------
# 2. Transcript-only retention (AC13)
# ---------------------------------------------------------------------------


def test_transcripts_only_retention(tmp_path):
    local_dir = tmp_path / "kids_review"
    store = KidsReviewStore(
        transcripts_enabled=True,
        audio_enabled=False,
        local_dir=str(local_dir),
    )
    store.start_session("sess-2", metadata={"default_explanation_language": "english"})
    store.record_transcript(_make_event("sess-2", "why is the sky blue"))
    store.end_session("sess-2")

    session_json = local_dir / "sess-2" / "session.json"
    assert session_json.exists()
    data = json.loads(session_json.read_text())
    assert data["transcripts_enabled"] is True
    assert data["audio_enabled"] is False
    assert data["audio_files"] == []
    assert len(data["transcripts"]) == 1
    assert data["transcripts"][0]["text"] == "why is the sky blue"


# ---------------------------------------------------------------------------
# 3. Audio-only retention (AC14)
# ---------------------------------------------------------------------------


def test_audio_only_retention(tmp_path):
    local_dir = tmp_path / "kids_review"
    store = KidsReviewStore(
        transcripts_enabled=False,
        audio_enabled=True,
        local_dir=str(local_dir),
    )
    store.start_session("sess-3", metadata={"default_explanation_language": "telugu"})
    audio_path = store.record_audio(
        "sess-3", Speaker.CHILD, b"RIFF-fake-bytes", timestamp_ms=1234
    )
    # record_transcript MUST be a no-op in this configuration.
    store.record_transcript(_make_event("sess-3", "should not be stored"))
    store.end_session("sess-3")

    assert audio_path is not None
    assert Path(audio_path).exists()
    assert Path(audio_path).read_bytes() == b"RIFF-fake-bytes"

    data = json.loads((local_dir / "sess-3" / "session.json").read_text())
    assert data["transcripts"] == []
    assert data["transcripts_enabled"] is False
    assert data["audio_enabled"] is True
    assert len(data["audio_files"]) == 1
    assert data["audio_files"][0]["path"].startswith("audio/")
    # Metadata should not contain any child transcript text.
    assert "should not be stored" not in json.dumps(data["metadata"])


# ---------------------------------------------------------------------------
# 4. Local-only mode (AC15a)
# ---------------------------------------------------------------------------


def test_local_only_mode_never_invokes_client(tmp_path):
    uploads: list[dict] = []
    bucket_names: list[str] = []
    instantiations: list[bool] = []
    store = KidsReviewStore(
        transcripts_enabled=True,
        audio_enabled=False,
        local_dir=str(tmp_path / "kids_review"),
        sync_to_gcs_policy="never",
        client_factory=_client_factory(uploads, bucket_names, instantiations),
    )
    store.start_session("sess-4")
    store.record_transcript(_make_event("sess-4", "hello"))
    store.end_session("sess-4")

    assert store.gcs_configured is False
    assert store.sync_to_object_store(force=True) is False
    assert instantiations == []
    assert uploads == []
    assert bucket_names == []


# ---------------------------------------------------------------------------
# 5. GCS sync (AC15b)
# ---------------------------------------------------------------------------


def test_gcs_sync_uploads_session_json_and_audio(tmp_path):
    uploads: list[dict] = []
    bucket_names: list[str] = []
    instantiations: list[bool] = []
    store = KidsReviewStore(
        transcripts_enabled=True,
        audio_enabled=True,
        local_dir=str(tmp_path / "kids_review"),
        bucket_name="my-bucket",
        object_prefix="kids_review/v1",
        sync_to_gcs_policy="session_end",
        client_factory=_client_factory(uploads, bucket_names, instantiations),
    )
    assert store.gcs_configured is True
    store.start_session("sess-5")
    store.record_transcript(_make_event("sess-5", "why"))
    store.record_audio("sess-5", Speaker.CHILD, b"abc", timestamp_ms=10)
    store.end_session("sess-5")

    assert store.sync_to_object_store(force=True) is True
    assert bucket_names == ["my-bucket"]
    keys = {u["key"] for u in uploads}
    assert "kids_review/v1/sess-5/session.json" in keys
    assert "kids_review/v1/sess-5/audio/10-child.webm" in keys


# ---------------------------------------------------------------------------
# 6. sync_to_gcs_policy validation
# ---------------------------------------------------------------------------


def test_invalid_sync_policy_raises(tmp_path):
    with pytest.raises(ValueError):
        KidsReviewStore(
            transcripts_enabled=True,
            audio_enabled=False,
            local_dir=str(tmp_path / "kids_review"),
            sync_to_gcs_policy="bogus",
        )


# ---------------------------------------------------------------------------
# 7. Transcript text appears in session.json when enabled
# ---------------------------------------------------------------------------


def test_transcript_text_ends_up_in_session_json(tmp_path):
    local_dir = tmp_path / "kids_review"
    store = KidsReviewStore(
        transcripts_enabled=True,
        audio_enabled=False,
        local_dir=str(local_dir),
    )
    store.start_session("sess-7")
    store.record_transcript(_make_event("sess-7", "hello sky"))
    store.end_session("sess-7")

    data = json.loads((local_dir / "sess-7" / "session.json").read_text())
    assert data["transcripts"][0]["text"] == "hello sky"


# ---------------------------------------------------------------------------
# 8. Transcript text never touches disk when disabled
# ---------------------------------------------------------------------------


def test_transcript_text_never_persisted_when_disabled(tmp_path):
    local_dir = tmp_path / "kids_review"
    store = KidsReviewStore(
        transcripts_enabled=False,
        audio_enabled=True,
        local_dir=str(local_dir),
    )
    secret_phrases = [
        "banana_moonbeam_quokka",
        "pineapple_cardinal_velvet",
        "meringue_tangerine_halcyon",
    ]
    store.start_session("sess-8")
    for i, phrase in enumerate(secret_phrases):
        store.record_transcript(_make_event("sess-8", phrase, timestamp_ms=i))
    # Force an audio write so the session actually has a folder on disk.
    store.record_audio("sess-8", Speaker.CHILD, b"\x00", timestamp_ms=99)
    store.end_session("sess-8")

    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        blob = path.read_bytes()
        for phrase in secret_phrases:
            assert phrase.encode("utf-8") not in blob, f"leaked {phrase} into {path}"


# ---------------------------------------------------------------------------
# 9. prune_expired removes old sessions, keeps fresh ones
# ---------------------------------------------------------------------------


def test_prune_expired_removes_old_sessions(tmp_path):
    local_dir = tmp_path / "kids_review"
    clock = FakeClock(now=1_700_000_000.0)
    store = KidsReviewStore(
        transcripts_enabled=True,
        audio_enabled=False,
        retention_days=30,
        local_dir=str(local_dir),
        time_fn=clock,
    )
    store.start_session("old")
    store.record_transcript(_make_event("old", "hi"))
    store.end_session("old")

    # 45 days later: "old" should now be expired.
    clock.advance(45 * 86400)
    store.start_session("fresh")
    store.record_transcript(_make_event("fresh", "hi"))
    store.end_session("fresh")

    deleted = store.prune_expired()
    assert deleted == 1
    assert not (local_dir / "old").exists()
    assert (local_dir / "fresh" / "session.json").exists()


def test_prune_expired_zero_days_is_noop(tmp_path):
    clock = FakeClock()
    store = KidsReviewStore(
        transcripts_enabled=True,
        audio_enabled=False,
        retention_days=0,
        local_dir=str(tmp_path / "kids_review"),
        time_fn=clock,
    )
    store.start_session("sess-z")
    store.record_transcript(_make_event("sess-z", "hi"))
    store.end_session("sess-z")
    clock.advance(365 * 86400)
    assert store.prune_expired() == 0


# ---------------------------------------------------------------------------
# 10. flush_if_needed(force=True) mid-session
# ---------------------------------------------------------------------------


def test_flush_if_needed_force_persists_mid_session(tmp_path):
    local_dir = tmp_path / "kids_review"
    store = KidsReviewStore(
        transcripts_enabled=True,
        audio_enabled=False,
        local_dir=str(local_dir),
    )
    store.start_session("sess-10")
    store.record_transcript(_make_event("sess-10", "hello"))
    # Note: no end_session call.
    assert store.flush_if_needed(force=True) is True

    session_json = local_dir / "sess-10" / "session.json"
    assert session_json.exists()
    data = json.loads(session_json.read_text())
    assert data["ended_at"] is None
    assert data["transcripts"][0]["text"] == "hello"
