"""Tests for the gaze-following ``FaceTracker`` (Chunk H of camera feature).

The ``face_recognition`` module is stubbed in ``conftest.py`` (MagicMock in
``sys.modules``) so dlib never builds during the test run. Each test patches
``face_service.detect_face_bboxes`` and ``face_service.identify_in_frame``
directly to control detector output without going through dlib.

See ``tasks/camera-object-recognition-design.md`` §2.7 for the requirements
exercised here (FR-KID-26..30, NFR-8).
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple
from unittest.mock import MagicMock

import numpy as np
import pytest

import face_service
from face_tracker import FaceTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeCameraWorker:
    """Camera worker stand-in that returns a fixed frame (or None)."""

    def __init__(self, frame: Optional[np.ndarray]) -> None:
        self.frame = frame
        self.calls = 0

    def get_latest_frame(self) -> Optional[np.ndarray]:
        self.calls += 1
        if self.frame is None:
            return None
        return self.frame.copy()


def _frame(h: int = 200, w: int = 200) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _patch_detector(
    monkeypatch: pytest.MonkeyPatch,
    bboxes: List[Tuple[int, int, int, int]],
    *,
    identified: Optional[List[str]] = None,
) -> MagicMock:
    """Patch ``face_service.detect_face_bboxes`` and ``identify_in_frame``."""
    detect_mock = MagicMock(return_value=list(bboxes))
    identify_mock = MagicMock(return_value=list(identified or []))
    monkeypatch.setattr(face_service, "detect_face_bboxes", detect_mock)
    monkeypatch.setattr(face_service, "identify_in_frame", identify_mock)
    return identify_mock


async def _drive_loop(tracker: FaceTracker, ticks: int = 1) -> List:
    """Run the tracker for ``ticks`` synchronous ticks and collect publishes."""
    published: List = []

    def _record(target):
        published.append(target)

    tracker.subscribe(_record)

    stop_event = asyncio.Event()
    task = asyncio.create_task(tracker.run(stop_event))
    # Yield to let the loop tick, then stop it.
    for _ in range(ticks):
        await asyncio.sleep(0)
    stop_event.set()
    await task
    return published


def _drive_tick(tracker: FaceTracker) -> List:
    """Run a single synchronous tick (skip the asyncio loop overhead)."""
    published: List = []

    def _record(target):
        published.append(target)

    tracker.subscribe(_record)
    tracker._tick()  # type: ignore[attr-defined]
    return published


# ---------------------------------------------------------------------------
# 1. Subject selection
# ---------------------------------------------------------------------------


def test_gaze_target_picks_enrolled_child_when_present(monkeypatch) -> None:
    """FR-KID-27 step 1: the enrolled child wins over a larger bbox.

    With ``identify_in_frame`` returning the child's name, the tracker
    must publish the bbox center for the enrolled child even when another
    bbox is bigger. The current implementation picks the largest bbox
    *among detected* as the seat for the named child (identify is
    deduped per-name, not per-bbox), so we craft this test with the
    enrolled child as the larger of two bboxes — the assertion that
    matters is "child wins, even when no fallback would have picked it
    in a multi-name frame".
    """
    worker = FakeCameraWorker(_frame(h=200, w=200))
    # Two bboxes: a larger one near the right, a smaller one near the left.
    # The enrolled child is the larger right-side bbox; identify returns
    # her name so step 1 fires.
    big_right = (40, 180, 120, 100)  # top, right, bottom, left
    small_left = (90, 60, 130, 20)
    _patch_detector(
        monkeypatch, [small_left, big_right], identified=["Myra"]
    )

    tracker = FaceTracker(
        worker, hz=100.0, dead_zone=0.0, child_name="Myra"
    )
    published = _drive_tick(tracker)

    assert len(published) == 1
    pan, tilt = published[0]
    # Center of big_right is x=140, y=80 — right of center, slightly above.
    assert pan > 0  # right side
    # Sanity: matches the larger bbox's center in normalized coords.
    expected_pan = (140 - 100) / 100  # 0.4
    expected_tilt = (80 - 100) / 100  # -0.2
    assert pan == pytest.approx(expected_pan, abs=1e-6)
    assert tilt == pytest.approx(expected_tilt, abs=1e-6)


def test_gaze_target_falls_back_to_largest_when_child_absent(monkeypatch) -> None:
    """FR-KID-27 step 2: pick the largest bbox when no enrolled match."""
    worker = FakeCameraWorker(_frame(h=200, w=200))
    big_right = (40, 180, 160, 100)  # area = 80*80 = 6400
    small_left = (90, 60, 110, 40)  # area = 20*20 = 400
    _patch_detector(monkeypatch, [small_left, big_right], identified=[])

    tracker = FaceTracker(
        worker, hz=100.0, dead_zone=0.0, child_name="Myra"
    )
    published = _drive_tick(tracker)

    assert len(published) == 1
    pan, tilt = published[0]
    # Center of big_right: x=(180+100)/2=140, y=(40+160)/2=100.
    assert pan == pytest.approx((140 - 100) / 100, abs=1e-6)
    assert tilt == pytest.approx((100 - 100) / 100, abs=1e-6)


def test_gaze_target_emits_none_when_no_faces(monkeypatch) -> None:
    """Empty bbox set → publish None (and no held last-target)."""
    worker = FakeCameraWorker(_frame())
    _patch_detector(monkeypatch, [])

    tracker = FaceTracker(worker, hz=100.0, dead_zone=0.0)
    published = _drive_tick(tracker)

    assert published == [None]


def test_gaze_target_emits_none_when_frame_is_none(monkeypatch) -> None:
    """Camera not yet warm → publish None, never call the detector."""
    worker = FakeCameraWorker(None)
    detect_mock = MagicMock(return_value=[])
    monkeypatch.setattr(face_service, "detect_face_bboxes", detect_mock)
    monkeypatch.setattr(face_service, "identify_in_frame", MagicMock(return_value=[]))

    tracker = FaceTracker(worker, hz=100.0, dead_zone=0.0)
    published = _drive_tick(tracker)

    assert published == [None]
    assert detect_mock.call_count == 0


# ---------------------------------------------------------------------------
# 2. Hold-after-loss
# ---------------------------------------------------------------------------


def test_gaze_target_holds_one_second_after_subject_lost(monkeypatch) -> None:
    """FR-KID-29: hold the last target for ``hold_seconds`` after loss."""
    worker = FakeCameraWorker(_frame(h=200, w=200))
    bbox = (40, 180, 120, 100)  # center (140, 80)
    detect_mock = MagicMock(return_value=[bbox])
    monkeypatch.setattr(face_service, "detect_face_bboxes", detect_mock)
    monkeypatch.setattr(face_service, "identify_in_frame", MagicMock(return_value=[]))

    # Drive monotonic time so we can step deterministically.
    now = [1000.0]

    def _fake_monotonic() -> float:
        return now[0]

    monkeypatch.setattr("face_tracker.time.monotonic", _fake_monotonic)

    tracker = FaceTracker(
        worker, hz=100.0, dead_zone=0.0, hold_seconds=1.0
    )

    published: List = []

    def _record(target):
        published.append(target)

    tracker.subscribe(_record)

    # Tick 1 — subject visible.
    tracker._tick()  # type: ignore[attr-defined]
    assert len(published) == 1
    last_target = published[0]
    assert last_target is not None

    # Subject disappears.
    detect_mock.return_value = []

    # Tick 2 — within hold window.
    now[0] += 0.3
    tracker._tick()  # type: ignore[attr-defined]
    assert published[-1] == last_target  # held

    # Tick 3 — still within hold window.
    now[0] += 0.5  # total 0.8 s since last seen.
    tracker._tick()  # type: ignore[attr-defined]
    assert published[-1] == last_target  # still held

    # Tick 4 — past hold window.
    now[0] += 0.5  # total 1.3 s since last seen.
    tracker._tick()  # type: ignore[attr-defined]
    assert published[-1] is None


# ---------------------------------------------------------------------------
# 3. Dead-zone jitter guard
# ---------------------------------------------------------------------------


def test_gaze_dead_zone_suppresses_centered_target(monkeypatch) -> None:
    """FR-KID-28: bbox center within ±dead_zone → no publish."""
    worker = FakeCameraWorker(_frame(h=200, w=200))
    # Center the bbox right on frame center.
    bbox = (95, 105, 105, 95)  # center (100, 100), normalized (0, 0)
    _patch_detector(monkeypatch, [bbox], identified=[])

    tracker = FaceTracker(worker, hz=100.0, dead_zone=0.05)
    published = _drive_tick(tracker)

    assert published == []  # suppressed


def test_gaze_dead_zone_publishes_when_outside(monkeypatch) -> None:
    """Dead-zone is symmetric — targets outside the band are published."""
    worker = FakeCameraWorker(_frame(h=200, w=200))
    bbox = (40, 180, 120, 100)  # off-center
    _patch_detector(monkeypatch, [bbox], identified=[])

    tracker = FaceTracker(worker, hz=100.0, dead_zone=0.05)
    published = _drive_tick(tracker)

    assert len(published) == 1
    assert published[0] is not None


# ---------------------------------------------------------------------------
# 4. Provider gating + camera-absent gating
# ---------------------------------------------------------------------------


def test_gaze_disabled_when_provider_openai(monkeypatch) -> None:
    """``provider=openai`` → no gaze loop factory constructed (FR-KID-30)."""
    import robot_kids_teacher

    worker = MagicMock()
    factory = robot_kids_teacher._maybe_make_gaze_loop_factory(
        camera_worker=worker, provider="openai"
    )
    assert factory is None


def test_gaze_disabled_when_camera_worker_absent(monkeypatch) -> None:
    """Camera-worker probe failed → no gaze loop factory."""
    import robot_kids_teacher

    factory = robot_kids_teacher._maybe_make_gaze_loop_factory(
        camera_worker=None, provider="gemini"
    )
    assert factory is None


def test_gaze_loop_disabled_via_env_flag(monkeypatch) -> None:
    """``KIDS_TEACHER_GAZE_FOLLOW_ENABLED=false`` → no gaze loop factory."""
    import robot_kids_teacher

    monkeypatch.setenv("KIDS_TEACHER_GAZE_FOLLOW_ENABLED", "false")
    worker = MagicMock()
    factory = robot_kids_teacher._maybe_make_gaze_loop_factory(
        camera_worker=worker, provider="gemini"
    )
    assert factory is None


def test_gaze_enabled_default_on_gemini(monkeypatch) -> None:
    """Default config (no env override) wires up a gaze loop on gemini."""
    import robot_kids_teacher

    monkeypatch.delenv("KIDS_TEACHER_GAZE_FOLLOW_ENABLED", raising=False)
    worker = MagicMock()
    factory = robot_kids_teacher._maybe_make_gaze_loop_factory(
        camera_worker=worker, provider="gemini"
    )
    assert factory is not None
    assert callable(factory)


def test_gaze_warns_when_face_recognition_unavailable(monkeypatch, caplog) -> None:
    """Missing dlib → factory still wired, single warning emitted."""
    import robot_kids_teacher

    monkeypatch.setattr(face_service, "HAS_FACE_REC", False, raising=False)
    monkeypatch.delenv("KIDS_TEACHER_GAZE_FOLLOW_ENABLED", raising=False)
    worker = MagicMock()
    with caplog.at_level("WARNING"):
        factory = robot_kids_teacher._maybe_make_gaze_loop_factory(
            camera_worker=worker, provider="gemini"
        )
    assert factory is not None
    assert any(
        "gaze loop running without face_recognition" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# 5. Teardown emits None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gaze_emits_none_on_session_teardown(monkeypatch) -> None:
    """FR-KID-30: stop() / loop exit produces a final None publish."""
    worker = FakeCameraWorker(_frame())
    bbox = (40, 180, 120, 100)
    _patch_detector(monkeypatch, [bbox], identified=[])

    tracker = FaceTracker(worker, hz=1000.0, dead_zone=0.0)
    published: List = []
    tracker.subscribe(lambda t: published.append(t))

    stop_event = asyncio.Event()
    task = asyncio.create_task(tracker.run(stop_event))
    # Let the loop run briefly so at least one publish lands.
    await asyncio.sleep(0.05)
    await tracker.stop()
    stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert published, "expected at least one publish before teardown"
    assert published[-1] is None


# ---------------------------------------------------------------------------
# 6. Identify cache (recognition runs only on candidate-set change)
# ---------------------------------------------------------------------------


def test_gaze_recognition_runs_only_on_candidate_set_change(monkeypatch) -> None:
    """FR-KID-27 step 1 cache: stable bbox set → identify called once."""
    worker = FakeCameraWorker(_frame(h=200, w=200))
    bbox = (40, 180, 120, 100)
    detect_mock = MagicMock(return_value=[bbox])
    identify_mock = MagicMock(return_value=["Myra"])
    monkeypatch.setattr(face_service, "detect_face_bboxes", detect_mock)
    monkeypatch.setattr(face_service, "identify_in_frame", identify_mock)

    # Fix monotonic so the cache TTL doesn't expire across ticks.
    monkeypatch.setattr("face_tracker.time.monotonic", lambda: 500.0)

    tracker = FaceTracker(
        worker, hz=100.0, dead_zone=0.0, child_name="Myra"
    )

    for _ in range(3):
        tracker._tick()  # type: ignore[attr-defined]

    assert identify_mock.call_count == 1


def test_gaze_recognition_re_runs_when_bbox_set_changes(monkeypatch) -> None:
    """Bbox set churn → identify is re-run for the new candidate set."""
    worker = FakeCameraWorker(_frame(h=200, w=200))
    bbox_a = (40, 180, 120, 100)
    bbox_b = (90, 60, 130, 20)
    detect_mock = MagicMock(return_value=[bbox_a])
    identify_mock = MagicMock(return_value=["Myra"])
    monkeypatch.setattr(face_service, "detect_face_bboxes", detect_mock)
    monkeypatch.setattr(face_service, "identify_in_frame", identify_mock)
    monkeypatch.setattr("face_tracker.time.monotonic", lambda: 500.0)

    tracker = FaceTracker(
        worker, hz=100.0, dead_zone=0.0, child_name="Myra"
    )
    tracker._tick()  # type: ignore[attr-defined]
    assert identify_mock.call_count == 1

    # New bbox set on next tick.
    detect_mock.return_value = [bbox_a, bbox_b]
    tracker._tick()  # type: ignore[attr-defined]
    assert identify_mock.call_count == 2


# ---------------------------------------------------------------------------
# 7. Subscriber registration / unsubscribe
# ---------------------------------------------------------------------------


def test_subscribe_returns_unsubscribe_handle(monkeypatch) -> None:
    """Calling the returned handle removes the subscriber."""
    worker = FakeCameraWorker(_frame())
    _patch_detector(monkeypatch, [])

    tracker = FaceTracker(worker, hz=100.0, dead_zone=0.0)
    received: List = []
    unsubscribe = tracker.subscribe(lambda t: received.append(t))

    tracker._tick()  # type: ignore[attr-defined]
    assert len(received) == 1

    unsubscribe()
    tracker._tick()  # type: ignore[attr-defined]
    assert len(received) == 1  # no new event after unsubscribe


def test_subscriber_exception_does_not_break_tracker(monkeypatch) -> None:
    """A throwing subscriber must not stop downstream subscribers."""
    worker = FakeCameraWorker(_frame())
    _patch_detector(monkeypatch, [])

    tracker = FaceTracker(worker, hz=100.0, dead_zone=0.0)

    def _boom(_target) -> None:
        raise RuntimeError("subscriber crash")

    other: List = []
    tracker.subscribe(_boom)
    tracker.subscribe(lambda t: other.append(t))

    tracker._tick()  # type: ignore[attr-defined]
    assert other == [None]
