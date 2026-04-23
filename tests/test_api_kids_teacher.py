"""Tests for src/kids_teacher_routes.py.

Builds a minimal test app that mounts only the kids-teacher router plus
a static-files mount so the page template can link to /static/*. No real
network. The review store is a test-scoped instance writing into tmp_path.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kids_teacher_routes
from kids_review_store import KidsReviewStore
from kids_teacher_routes import _is_local_request, router as kids_teacher_router
from kids_teacher_types import KidsTranscriptEvent, Speaker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(review_store=None) -> FastAPI:
    app = FastAPI()
    app.include_router(kids_teacher_router)
    app.state.kids_review_store = review_store
    return app


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


class TestStatus:
    def test_default_shape(self):
        app = _build_app(review_store=None)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "kids_teacher"
        assert data["model"] in {"gpt-realtime", "gpt-realtime-mini"}
        assert isinstance(data["enabled_languages"], list)
        assert data["default_explanation_language"] in data["enabled_languages"]
        assert data["review"] == {"transcripts_enabled": False, "audio_enabled": False}
        assert data["profile"]["name"]
        # Instructions text is deliberately NOT included in the status payload.
        assert "instructions" not in data["profile"]

    def test_model_reflects_env_override(self, monkeypatch):
        monkeypatch.setenv("KIDS_TEACHER_REALTIME_MODEL", "gpt-realtime")
        app = _build_app(review_store=None)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/status")
        assert resp.status_code == 200
        assert resp.json()["model"] == "gpt-realtime"

    def test_review_flags_reflect_store(self, tmp_path):
        store = KidsReviewStore(
            transcripts_enabled=True,
            audio_enabled=False,
            retention_days=30,
            local_dir=str(tmp_path),
        )
        app = _build_app(review_store=store)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/status")
        data = resp.json()
        assert data["review"] == {"transcripts_enabled": True, "audio_enabled": False}


# ---------------------------------------------------------------------------
# Review listing / detail
# ---------------------------------------------------------------------------


class TestReviewList:
    def test_returns_404_when_store_is_none(self):
        app = _build_app(review_store=None)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/review/sessions")
        assert resp.status_code == 404

    def test_returns_404_when_both_toggles_off(self, tmp_path):
        store = KidsReviewStore(
            transcripts_enabled=False,
            audio_enabled=False,
            retention_days=30,
            local_dir=str(tmp_path),
        )
        app = _build_app(review_store=store)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/review/sessions")
        assert resp.status_code == 404

    def test_returns_empty_list_when_enabled_no_sessions(self, tmp_path):
        store = KidsReviewStore(
            transcripts_enabled=True,
            audio_enabled=False,
            retention_days=30,
            local_dir=str(tmp_path),
        )
        app = _build_app(review_store=store)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/review/sessions")
        assert resp.status_code == 200
        assert resp.json() == {"sessions": []}

    def test_returns_sessions_when_present(self, tmp_path):
        store = KidsReviewStore(
            transcripts_enabled=True,
            audio_enabled=False,
            retention_days=30,
            local_dir=str(tmp_path),
        )
        store.start_session("abc", metadata={"note": "hi"})
        store.record_transcript(
            KidsTranscriptEvent(
                speaker=Speaker.CHILD,
                text="why is the sky blue",
                is_partial=False,
                timestamp_ms=1,
                session_id="abc",
                language="english",
            )
        )
        store.end_session("abc")

        app = _build_app(review_store=store)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/review/sessions")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert any(s["session_id"] == "abc" for s in sessions)


class TestReviewDetail:
    def _primed_store(self, tmp_path) -> KidsReviewStore:
        store = KidsReviewStore(
            transcripts_enabled=True,
            audio_enabled=False,
            retention_days=30,
            local_dir=str(tmp_path),
        )
        store.start_session("s1")
        store.record_transcript(
            KidsTranscriptEvent(
                speaker=Speaker.CHILD,
                text="why",
                is_partial=False,
                timestamp_ms=1,
                session_id="s1",
                language="english",
            )
        )
        store.end_session("s1")
        return store

    def test_returns_full_session_when_transcripts_enabled(self, tmp_path):
        store = self._primed_store(tmp_path)
        app = _build_app(review_store=store)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/review/sessions/s1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "s1"
        assert data["transcripts"]
        assert data["transcripts"][0]["text"] == "why"

    def test_strips_transcripts_when_disabled_but_audio_enabled(self, tmp_path):
        # Audio-only retention: the session was recorded earlier with
        # transcripts_enabled=True, but the currently-configured store has
        # transcripts disabled so the response must strip them.
        primed = self._primed_store(tmp_path)
        assert primed.list_sessions()[0]["session_id"] == "s1"

        store = KidsReviewStore(
            transcripts_enabled=False,
            audio_enabled=True,
            retention_days=30,
            local_dir=str(tmp_path),
        )
        app = _build_app(review_store=store)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/review/sessions/s1")
        assert resp.status_code == 200
        data = resp.json()
        assert "transcripts" not in data
        assert data["audio_files"] == []

    def test_unknown_session_returns_404(self, tmp_path):
        store = self._primed_store(tmp_path)
        app = _build_app(review_store=store)
        with TestClient(app) as client:
            resp = client.get("/api/kids-teacher/review/sessions/missing")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Local-only enforcement
# ---------------------------------------------------------------------------


def test_is_local_request_rejects_non_local_clients():
    # Direct unit check of the heuristic since TestClient is treated as local.
    req_local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    req_remote = SimpleNamespace(client=SimpleNamespace(host="203.0.113.42"))
    assert _is_local_request(req_local) is True
    assert _is_local_request(req_remote) is False


def test_review_sessions_rejects_remote_via_patched_helper(tmp_path, monkeypatch):
    store = KidsReviewStore(
        transcripts_enabled=True,
        audio_enabled=False,
        retention_days=30,
        local_dir=str(tmp_path),
    )

    # Force the helper to treat every request as non-local so we exercise
    # the 403 branch end-to-end without re-writing TestClient internals.
    monkeypatch.setattr(kids_teacher_routes, "_is_local_request", lambda r: False)
    app = _build_app(review_store=store)
    with TestClient(app) as client:
        resp = client.get("/api/kids-teacher/review/sessions")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------


def test_page_route_renders_200(tmp_path):
    app = _build_app(review_store=None)
    with TestClient(app) as client:
        resp = client.get("/kids-teacher")
    assert resp.status_code == 200
    assert "Kids Teacher" in resp.text
    assert "/static/js/kids_teacher.js" in resp.text
