"""Tests for Chunk F's face-rec session lifecycle.

Covers:
* Session-start sweep — ≥2-of-5 threshold, present-names note injection,
  graceful no-op when face_recognition / encodings are absent.
* On-demand recheck loop — bbox-count growth detection, known/unknown
  arrival announcements, 5 s throttle.
* Provider gate (FR-KID-23) — ``provider=openai`` builds no factory and
  emits the disabled log line.
* Graceful degradation (FR-KID-24 / NFR-7) — missing dlib stub.
* Faces.pkl persistence across sessions (cross-cuts Chunk D).
"""

from __future__ import annotations

import asyncio
import pickle
import sys
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

import face_service
import robot_kids_teacher
from kids_teacher_profile import load_profile


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_faces_file(tmp_path, monkeypatch):
    """Every test gets a clean faces.pkl override under tmp_path."""
    monkeypatch.setenv("MYRA_FACES_FILE", str(tmp_path / "faces.pkl"))
    yield


@pytest.fixture(autouse=True)
def _isolate_memory_file(tmp_path, monkeypatch):
    """Keep load_profile() away from the real ~/.myra/memory.md."""
    monkeypatch.setenv("MYRA_MEMORY_FILE", str(tmp_path / "memory.md"))
    yield


def _frame() -> np.ndarray:
    return np.zeros((4, 4, 3), dtype=np.uint8)


class _FrameSourceWorker:
    """Minimal CameraWorker stand-in: returns the next frame on each call."""

    def __init__(self, frames: list[Any] | None = None) -> None:
        # Default: always serve a fresh frame.
        self._frames = list(frames or [])
        self._default = _frame()
        self.calls = 0

    def get_latest_frame(self) -> Any:
        self.calls += 1
        if self._frames:
            return self._frames.pop(0)
        return self._default


class _RecordingHandler:
    """Captures push_text calls. session_active stays True for these tests."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.session_active = True

    async def push_text(self, text: str) -> None:
        self.texts.append(text)


# ---------------------------------------------------------------------------
# 1. Session-start sweep
# ---------------------------------------------------------------------------


def test_session_start_sweep_injects_present_names(monkeypatch) -> None:
    """≥2/5 threshold met for both names → present-names note appears."""
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    # 5 sweep calls return: [Myra, Aunt Priya], [], [Myra, Aunt Priya],
    # [Myra], [Aunt Priya]. Myra=3/5, Aunt Priya=3/5.
    sequence = [
        ["Myra", "Aunt Priya"],
        [],
        ["Myra", "Aunt Priya"],
        ["Myra"],
        ["Aunt Priya"],
    ]
    calls = {"i": 0}

    def fake_identify(frame, tolerance=None):
        i = calls["i"]
        calls["i"] += 1
        return list(sequence[i]) if i < len(sequence) else []

    monkeypatch.setattr(face_service, "identify_in_frame", fake_identify)
    # Speed up the sleep so the test is fast.
    monkeypatch.setattr(robot_kids_teacher, "_FACE_SWEEP_INTERVAL_SEC", 0.0)

    worker = _FrameSourceWorker()
    names = asyncio.run(robot_kids_teacher.run_session_start_face_sweep(worker))

    assert names == ["Aunt Priya", "Myra"]  # alphabetized

    profile = load_profile(present_names=names)
    assert "# People you can currently see" in profile.instructions
    assert "You can currently see: Aunt Priya, Myra." in profile.instructions


def test_session_start_sweep_two_of_five_threshold(monkeypatch) -> None:
    """A name seen only once must NOT make it into the present list."""
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    sequence = [["Bob"], [], [], [], []]
    calls = {"i": 0}

    def fake_identify(frame, tolerance=None):
        i = calls["i"]
        calls["i"] += 1
        return list(sequence[i]) if i < len(sequence) else []

    monkeypatch.setattr(face_service, "identify_in_frame", fake_identify)
    monkeypatch.setattr(robot_kids_teacher, "_FACE_SWEEP_INTERVAL_SEC", 0.0)

    names = asyncio.run(
        robot_kids_teacher.run_session_start_face_sweep(_FrameSourceWorker())
    )
    assert "Bob" not in names
    assert names == []


def test_present_names_note_omitted_when_no_encodings(monkeypatch) -> None:
    """Empty faces.pkl → sweep returns [] → no present-names section."""
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    # No encodings on disk: real identify_in_frame returns []. Stub it
    # explicitly so the test is independent of the dlib stub's behavior.
    monkeypatch.setattr(face_service, "load_encodings", lambda: {})
    monkeypatch.setattr(face_service, "identify_in_frame", lambda *a, **kw: [])
    monkeypatch.setattr(robot_kids_teacher, "_FACE_SWEEP_INTERVAL_SEC", 0.0)

    names = asyncio.run(
        robot_kids_teacher.run_session_start_face_sweep(_FrameSourceWorker())
    )
    assert names == []

    profile = load_profile(present_names=names)
    assert "You can currently see" not in profile.instructions


def test_present_names_note_omitted_when_present_names_none() -> None:
    """``present_names=None`` always omits the section."""
    profile = load_profile(present_names=None)
    assert "You can currently see" not in profile.instructions


# ---------------------------------------------------------------------------
# 2. On-demand recheck loop
# ---------------------------------------------------------------------------


def test_on_demand_recheck_announces_new_arrival(monkeypatch) -> None:
    """First bbox-count growth + identify=['Daddy'] → 'Daddy just joined.'"""
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    # First tick: 1 bbox (prev=0 → grew). identify returns Daddy.
    monkeypatch.setattr(
        face_service,
        "detect_face_bboxes",
        lambda frame, downscale=True: [(0, 0, 0, 0)],
    )
    monkeypatch.setattr(
        face_service,
        "identify_in_frame",
        lambda frame, tolerance=None: ["Daddy"],
    )

    factory = robot_kids_teacher._make_face_rec_loop_factory(
        _FrameSourceWorker(),
        initial_names=[],
        interval_sec=0.0,
    )
    handler = _RecordingHandler()
    stop = asyncio.Event()

    async def driver() -> None:
        task = asyncio.create_task(factory(handler, stop))
        for _ in range(20):
            await asyncio.sleep(0)
            if "Daddy just joined." in handler.texts:
                break
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())
    assert "Daddy just joined." in handler.texts


def test_on_demand_recheck_announces_unknown_when_no_match(monkeypatch) -> None:
    """Bbox grows but identify returns [] → unknown-arrival prompt."""
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    bbox_iter = iter([[(0, 0, 0, 0)], [(0, 0, 0, 0), (1, 1, 1, 1)]])
    monkeypatch.setattr(
        face_service,
        "detect_face_bboxes",
        lambda frame, downscale=True: next(bbox_iter, []),
    )
    monkeypatch.setattr(
        face_service, "identify_in_frame", lambda frame, tolerance=None: []
    )

    factory = robot_kids_teacher._make_face_rec_loop_factory(
        _FrameSourceWorker(),
        initial_names=[],
        interval_sec=0.0,
    )
    handler = _RecordingHandler()
    stop = asyncio.Event()

    async def driver() -> None:
        task = asyncio.create_task(factory(handler, stop))
        for _ in range(20):
            await asyncio.sleep(0)
            if handler.texts:
                break
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())
    assert robot_kids_teacher._FACE_UNKNOWN_ARRIVAL_NOTE in handler.texts


def test_on_demand_recheck_throttled_to_5s(monkeypatch) -> None:
    """Two growth events inside the 5 s cooldown → only one announcement.

    Once the monotonic clock has advanced past the 5 s window, a third
    growth fires a second announcement.
    """
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    # The bbox source is a controllable list — the test mutates it
    # between announcement gates so we know exactly what the loop sees.
    bbox_state = {"count": 0}

    def fake_bboxes(frame, downscale=True):
        return [(0, 0, 0, 0)] * bbox_state["count"]

    monkeypatch.setattr(face_service, "detect_face_bboxes", fake_bboxes)
    monkeypatch.setattr(
        face_service, "identify_in_frame", lambda frame, tolerance=None: []
    )

    clock = {"t": 1.0}

    def fake_monotonic() -> float:
        return clock["t"]

    factory = robot_kids_teacher._make_face_rec_loop_factory(
        _FrameSourceWorker(),
        initial_names=[],
        interval_sec=0.0,
        monotonic=fake_monotonic,
    )
    handler = _RecordingHandler()
    stop = asyncio.Event()

    async def yield_a_few():
        for _ in range(20):
            await asyncio.sleep(0)

    async def driver() -> None:
        task = asyncio.create_task(factory(handler, stop))
        # Growth #1 at t=1.0 → first announcement fires.
        bbox_state["count"] = 1
        await yield_a_few()
        # Growth #2 at t=1.5 (within 5 s cooldown) → throttled, no fire.
        clock["t"] = 1.5
        bbox_state["count"] = 2
        await yield_a_few()
        first_count = len(handler.texts)
        # Growth #3 at t=10.0 (past cooldown) → second announcement fires.
        clock["t"] = 10.0
        bbox_state["count"] = 3
        await yield_a_few()
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return first_count

    first_count = asyncio.run(driver())
    assert first_count == 1, "throttle let a second announcement through"
    assert len(handler.texts) == 2, (
        f"expected exactly two announcements; got {handler.texts}"
    )


# ---------------------------------------------------------------------------
# 3. Provider gate + graceful degradation
# ---------------------------------------------------------------------------


def test_face_rec_disabled_when_provider_openai(caplog) -> None:
    """provider=openai → factory is None and the disabled log fires."""
    with caplog.at_level("INFO"):
        factory = robot_kids_teacher._maybe_build_face_rec_loop_factory(
            camera_worker=object(),
            provider="openai",
            initial_names=[],
        )
    assert factory is None
    assert any(
        "face-rec disabled: provider=openai" in r.message for r in caplog.records
    )


def test_face_rec_disabled_when_camera_worker_missing() -> None:
    """No camera worker → no factory regardless of provider."""
    factory = robot_kids_teacher._maybe_build_face_rec_loop_factory(
        camera_worker=None,
        provider="gemini",
        initial_names=[],
    )
    assert factory is None


def test_face_rec_degrades_gracefully_when_dlib_missing(monkeypatch, caplog) -> None:
    """face_recognition unavailable → factory is None, sweep is no-op, no exception."""
    monkeypatch.setattr(face_service, "HAS_FACE_REC", False, raising=False)

    with caplog.at_level("WARNING"):
        factory = robot_kids_teacher._maybe_build_face_rec_loop_factory(
            camera_worker=_FrameSourceWorker(),
            provider="gemini",
            initial_names=[],
        )
    assert factory is None
    assert any(
        "face_recognition unavailable" in r.message for r in caplog.records
    )

    # Sweep is a silent no-op too.
    names = asyncio.run(
        robot_kids_teacher.run_session_start_face_sweep(_FrameSourceWorker())
    )
    assert names == []


def test_face_rec_loop_no_op_when_dlib_missing(monkeypatch) -> None:
    """If a factory is somehow built then HAS_FACE_REC flips off, the loop
    body returns immediately rather than calling into face_service.
    """
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)
    factory = robot_kids_teacher._make_face_rec_loop_factory(
        _FrameSourceWorker(),
        initial_names=[],
        interval_sec=0.0,
    )
    monkeypatch.setattr(face_service, "HAS_FACE_REC", False, raising=False)

    handler = _RecordingHandler()
    stop = asyncio.Event()

    async def driver() -> None:
        task = asyncio.create_task(factory(handler, stop))
        await asyncio.sleep(0)  # let the loop bail out
        stop.set()
        await task

    asyncio.run(driver())
    assert handler.texts == []


# ---------------------------------------------------------------------------
# 4. Faces.pkl persistence across sessions
# ---------------------------------------------------------------------------


def test_faces_pkl_persists_across_sessions(monkeypatch, tmp_path) -> None:
    """Encoding written via face_service.save_encodings survives a teardown.

    Cross-cuts Chunk D's persistence guarantee — re-verified here so any
    regression in the lifecycle layer is caught at the integration seam.
    """
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    enc = np.arange(128, dtype=np.float64)
    face_service.save_encodings({"Myra": [enc]})

    # New "session": fresh load_encodings call must read the same pickle.
    loaded = face_service.load_encodings()
    assert "Myra" in loaded
    assert len(loaded["Myra"]) == 1
    assert np.array_equal(loaded["Myra"][0], enc)

    # Re-confirm the file lives at our test override, not the user's home.
    expected_path = tmp_path / "faces.pkl"
    assert expected_path.exists()
    with expected_path.open("rb") as handle:
        on_disk = pickle.load(handle)
    assert "Myra" in on_disk
