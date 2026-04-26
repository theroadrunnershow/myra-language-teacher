"""Tests for the local face-recognition service.

The ``face_recognition`` module is stubbed in ``conftest.py`` (MagicMock in
sys.modules) so dlib never builds during the test run. Each test patches the
stub's ``face_locations`` / ``face_encodings`` / ``face_distance`` callables to
control detector output.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

import face_service
from face_service import EnrollResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _frame(h: int = 720, w: int = 1280) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _patch_detector(
    monkeypatch: pytest.MonkeyPatch,
    *,
    locations: list[tuple[int, int, int, int]] | None = None,
    encodings: list[np.ndarray] | None = None,
    distances: np.ndarray | None = None,
) -> None:
    """Wire up controllable face_locations / face_encodings / face_distance."""
    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(return_value=list(locations or []))
    fr.face_encodings = MagicMock(return_value=list(encodings or []))
    if distances is not None:
        fr.face_distance = MagicMock(return_value=distances)
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)


@pytest.fixture(autouse=True)
def _isolate_faces_file(tmp_path, monkeypatch):
    """Every test gets its own ``faces.pkl`` under tmp_path."""
    monkeypatch.setenv("MYRA_FACES_FILE", str(tmp_path / "faces.pkl"))
    yield


# ---------------------------------------------------------------------------
# enroll_from_frame
# ---------------------------------------------------------------------------


def test_enroll_persists_encoding_with_one_face(monkeypatch, tmp_path) -> None:
    enc = np.arange(128, dtype=np.float64)
    _patch_detector(
        monkeypatch,
        locations=[(0, 100, 100, 0)],
        encodings=[enc],
    )

    result = face_service.enroll_from_frame("Aunt Priya", _frame())

    assert result is EnrollResult.OK
    stored = face_service.load_encodings()
    assert "Aunt Priya" in stored
    assert len(stored["Aunt Priya"]) == 1
    np.testing.assert_array_equal(stored["Aunt Priya"][0], enc)
    assert (tmp_path / "faces.pkl").exists()


def test_enroll_refuses_when_zero_faces(monkeypatch) -> None:
    _patch_detector(monkeypatch, locations=[], encodings=[])

    result = face_service.enroll_from_frame("Aunt Priya", _frame())

    assert result is EnrollResult.NO_FACE
    assert face_service.load_encodings() == {}


def test_enroll_refuses_when_multiple_faces(monkeypatch) -> None:
    _patch_detector(
        monkeypatch,
        locations=[(0, 100, 100, 0), (0, 200, 100, 100)],
        encodings=[np.zeros(128), np.ones(128)],
    )

    result = face_service.enroll_from_frame("Aunt Priya", _frame())

    assert result is EnrollResult.MULTIPLE_FACES
    assert face_service.load_encodings() == {}


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def test_forget_removes_encoding(monkeypatch) -> None:
    _patch_detector(
        monkeypatch,
        locations=[(0, 100, 100, 0)],
        encodings=[np.zeros(128)],
    )
    face_service.enroll_from_frame("Uncle Sam", _frame())
    assert "Uncle Sam" in face_service.load_encodings()

    removed = face_service.forget("Uncle Sam")

    assert removed is True
    assert "Uncle Sam" not in face_service.load_encodings()


def test_forget_returns_false_when_name_unknown(monkeypatch) -> None:
    _patch_detector(monkeypatch)
    assert face_service.forget("Nobody") is False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persist_across_sessions(monkeypatch) -> None:
    enc = np.linspace(0, 1, 128, dtype=np.float64)
    _patch_detector(
        monkeypatch,
        locations=[(0, 100, 100, 0)],
        encodings=[enc],
    )

    face_service.enroll_from_frame("Myra", _frame())

    # Simulate a fresh process: load from disk again, no in-memory state.
    reloaded = face_service.load_encodings()
    assert "Myra" in reloaded
    np.testing.assert_array_equal(reloaded["Myra"][0], enc)


# ---------------------------------------------------------------------------
# Capacity (FR-KID-13)
# ---------------------------------------------------------------------------


def test_capacity_cap_at_30_names(monkeypatch) -> None:
    enc = np.zeros(128)
    _patch_detector(
        monkeypatch,
        locations=[(0, 100, 100, 0)],
        encodings=[enc],
    )

    for i in range(face_service.MAX_NAMES):
        result = face_service.enroll_from_frame(f"person_{i:02d}", _frame())
        assert result is EnrollResult.OK

    stored_before = face_service.load_encodings()
    assert len(stored_before) == face_service.MAX_NAMES

    overflow = face_service.enroll_from_frame("person_30", _frame())

    assert overflow is EnrollResult.CAPACITY_EXCEEDED
    stored_after = face_service.load_encodings()
    assert len(stored_after) == face_service.MAX_NAMES
    assert "person_30" not in stored_after
    # Existing encodings untouched.
    assert set(stored_after.keys()) == set(stored_before.keys())


def test_capacity_allows_additional_encoding_for_existing_name(monkeypatch) -> None:
    enc = np.zeros(128)
    _patch_detector(
        monkeypatch,
        locations=[(0, 100, 100, 0)],
        encodings=[enc],
    )
    # Fill to cap.
    for i in range(face_service.MAX_NAMES):
        face_service.enroll_from_frame(f"person_{i:02d}", _frame())

    # Adding another encoding to an *existing* name is allowed.
    again = face_service.enroll_from_frame("person_00", _frame())
    assert again is EnrollResult.OK
    assert len(face_service.load_encodings()["person_00"]) == 2


def test_capacity_drops_oldest_encoding_when_per_name_cap_exceeded(monkeypatch) -> None:
    distinct = [np.full(128, fill_value=i, dtype=np.float64) for i in range(10)]
    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(return_value=[(0, 100, 100, 0)])
    # Return one encoding per call, in order.
    fr.face_encodings = MagicMock(side_effect=[[e] for e in distinct])
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    for _ in range(10):
        face_service.enroll_from_frame("Myra", _frame())

    bucket = face_service.load_encodings()["Myra"]
    assert len(bucket) == face_service.MAX_ENCODINGS_PER_NAME
    # FIFO: oldest two (0, 1) dropped; bucket should start at value 2.
    np.testing.assert_array_equal(bucket[0], distinct[2])
    np.testing.assert_array_equal(bucket[-1], distinct[9])


# ---------------------------------------------------------------------------
# Graceful degradation (FR-KID-24 / NFR-7)
# ---------------------------------------------------------------------------


def test_dlib_missing_graceful_degradation(monkeypatch) -> None:
    monkeypatch.setattr(face_service, "HAS_FACE_REC", False, raising=False)

    assert face_service.enroll_from_frame("Anyone", _frame()) is EnrollResult.LIBRARY_MISSING
    assert face_service.identify_in_frame(_frame()) == []
    assert face_service.detect_face_bboxes(_frame()) == []
    assert face_service.forget("Anyone") is False


# ---------------------------------------------------------------------------
# identify_in_frame
# ---------------------------------------------------------------------------


def test_identify_returns_known_names(monkeypatch) -> None:
    priya_enc = np.zeros(128, dtype=np.float64)
    sam_enc = np.ones(128, dtype=np.float64)

    # Pre-seed two encodings via save_encodings.
    face_service.save_encodings({"Aunt Priya": [priya_enc], "Uncle Sam": [sam_enc]})

    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(return_value=[(0, 100, 100, 0)])
    fr.face_encodings = MagicMock(return_value=[priya_enc])
    # face_distance: zero distance to Priya, far from Sam.
    fr.face_distance = MagicMock(return_value=np.array([0.0, 1.0]))
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    names = face_service.identify_in_frame(_frame())

    assert names == ["Aunt Priya"]


def test_identify_returns_empty_when_distance_above_tolerance(monkeypatch) -> None:
    face_service.save_encodings({"Aunt Priya": [np.zeros(128)]})
    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(return_value=[(0, 100, 100, 0)])
    fr.face_encodings = MagicMock(return_value=[np.ones(128)])
    fr.face_distance = MagicMock(return_value=np.array([0.99]))
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    assert face_service.identify_in_frame(_frame()) == []


# ---------------------------------------------------------------------------
# detect_face_bboxes
# ---------------------------------------------------------------------------


def test_detect_face_bboxes_downscale_rescales_coords(monkeypatch) -> None:
    # 960p frame → expect downscale to ~480p (factor 2.0).
    frame = _frame(h=960, w=1280)
    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(return_value=[(10, 20, 30, 40)])
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    bboxes = face_service.detect_face_bboxes(frame, downscale=True)

    assert len(bboxes) == 1
    top, right, bottom, left = bboxes[0]
    # Each coord should be multiplied by ~2 (960/480) when rescaled back.
    assert (top, right, bottom, left) == (20, 40, 60, 80)


def test_detect_face_bboxes_no_downscale_passes_through(monkeypatch) -> None:
    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(return_value=[(5, 6, 7, 8)])
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    bboxes = face_service.detect_face_bboxes(_frame(h=400, w=600), downscale=False)
    assert bboxes == [(5, 6, 7, 8)]


# ---------------------------------------------------------------------------
# bug_001 regression: BGR frames must be swapped to RGB before reaching dlib
# ---------------------------------------------------------------------------


def _bgr_frame_with_marker() -> np.ndarray:
    """Make a frame whose first pixel has distinguishable B and R channels.

    BGR pixel (10, 20, 30) means B=10, G=20, R=30. After channel-swap to RGB
    the first pixel must read (30, 20, 10). The regression tests use this
    to assert face_service is passing RGB to dlib, not raw BGR.
    """
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[0, 0] = (10, 20, 30)  # BGR
    return frame


def test_enroll_from_frame_passes_rgb_to_face_recognition(monkeypatch) -> None:
    enc = np.arange(128, dtype=np.float64)
    _patch_detector(
        monkeypatch,
        locations=[(0, 100, 100, 0)],
        encodings=[enc],
    )
    fr = sys.modules["face_recognition"]

    face_service.enroll_from_frame("X", _bgr_frame_with_marker())

    # Both face_locations and face_encodings should receive the RGB frame.
    for call in (fr.face_locations.call_args, fr.face_encodings.call_args):
        passed = call.args[0]
        assert tuple(passed[0, 0]) == (30, 20, 10), (
            "face_service should swap BGR→RGB before calling dlib (bug_001)"
        )


def test_identify_in_frame_passes_rgb_to_face_recognition(monkeypatch) -> None:
    _patch_detector(
        monkeypatch,
        locations=[(0, 100, 100, 0)],
        encodings=[np.zeros(128, dtype=np.float64)],
        distances=np.array([0.1]),
    )
    fr = sys.modules["face_recognition"]
    # Pre-seed encodings so identify actually proceeds to face_locations.
    face_service.save_encodings({"Known": [np.zeros(128, dtype=np.float64)]})

    face_service.identify_in_frame(_bgr_frame_with_marker())

    passed = fr.face_locations.call_args.args[0]
    assert tuple(passed[0, 0]) == (30, 20, 10)


def test_detect_face_bboxes_passes_rgb_to_face_recognition(monkeypatch) -> None:
    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(return_value=[(5, 6, 7, 8)])
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    face_service.detect_face_bboxes(_bgr_frame_with_marker(), downscale=False)

    passed = fr.face_locations.call_args.args[0]
    assert tuple(passed[0, 0]) == (30, 20, 10)


# ---------------------------------------------------------------------------
# 2026-04-26 regression: dlib HOG / CNN are NOT thread-safe. Concurrent calls
# from the gaze tracker (asyncio loop, 3 Hz) and `remember_face` (asyncio
# to_thread worker) corrupted the glibc freelist and aborted the process.
# `_DLIB_LOCK` must serialize every dlib entry point in the module.
# ---------------------------------------------------------------------------


def test_dlib_lock_serializes_concurrent_callers(monkeypatch) -> None:
    """Two threads calling face_service must never sit inside dlib at once."""
    import threading
    import time

    inside = 0
    max_inside = 0
    counter_lock = threading.Lock()
    barrier = threading.Barrier(3)  # 2 workers + main thread

    def fake_face_locations(_rgb, model="hog"):
        nonlocal inside, max_inside
        with counter_lock:
            inside += 1
            max_inside = max(max_inside, inside)
        # Hold long enough that, without _DLIB_LOCK, the other thread would
        # observe inside == 2 before the first call returned.
        time.sleep(0.05)
        with counter_lock:
            inside -= 1
        return [(0, 100, 100, 0)]

    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(side_effect=fake_face_locations)
    fr.face_encodings = MagicMock(return_value=[np.zeros(128)])
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    def worker():
        barrier.wait()
        face_service.detect_face_bboxes(_frame())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    barrier.wait()  # release both workers simultaneously
    for t in threads:
        t.join(timeout=2.0)
        assert not t.is_alive(), "worker thread did not finish in time"

    assert max_inside == 1, (
        f"dlib was entered concurrently (max_inside={max_inside}); "
        "_DLIB_LOCK is not serializing callers"
    )


def test_dlib_lock_serializes_enroll_against_detect(monkeypatch) -> None:
    """The crash trace had enroll_from_frame (worker) + detect_face_bboxes
    (loop) overlapping. Both entry points must share the same lock."""
    import threading
    import time

    inside = 0
    max_inside = 0
    counter_lock = threading.Lock()
    barrier = threading.Barrier(3)

    def fake_face_locations(_rgb, model="hog"):
        nonlocal inside, max_inside
        with counter_lock:
            inside += 1
            max_inside = max(max_inside, inside)
        time.sleep(0.05)
        with counter_lock:
            inside -= 1
        return [(0, 100, 100, 0)]

    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(side_effect=fake_face_locations)
    fr.face_encodings = MagicMock(return_value=[np.zeros(128)])
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    def enroll_worker():
        barrier.wait()
        face_service.enroll_from_frame("X", _frame())

    def detect_worker():
        barrier.wait()
        face_service.detect_face_bboxes(_frame())

    t1 = threading.Thread(target=enroll_worker)
    t2 = threading.Thread(target=detect_worker)
    t1.start()
    t2.start()
    barrier.wait()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert max_inside == 1, (
        f"enroll + detect overlapped inside dlib (max_inside={max_inside})"
    )


# ---------------------------------------------------------------------------
# prewarm: load dlib's CNN encoder before the contended window opens
# ---------------------------------------------------------------------------


def test_prewarm_invokes_face_encodings(monkeypatch) -> None:
    fr = sys.modules["face_recognition"]
    fr.face_encodings = MagicMock(return_value=[])
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    face_service.prewarm()

    assert fr.face_encodings.call_count == 1
    passed_frame, passed_locations = fr.face_encodings.call_args.args
    assert passed_frame.shape == (128, 128, 3)
    assert passed_frame.dtype == np.uint8
    assert passed_locations == [(0, 128, 128, 0)]


def test_prewarm_no_op_when_dlib_missing(monkeypatch) -> None:
    monkeypatch.setattr(face_service, "HAS_FACE_REC", False, raising=False)
    fr = sys.modules["face_recognition"]
    fr.face_encodings = MagicMock(return_value=[])

    face_service.prewarm()  # must not raise

    assert fr.face_encodings.call_count == 0


def test_prewarm_swallows_exceptions(monkeypatch) -> None:
    fr = sys.modules["face_recognition"]
    fr.face_encodings = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    face_service.prewarm()  # must not propagate


# ---------------------------------------------------------------------------
# enroll_from_frame downscales for HOG (defense-in-depth: shorter lock-hold)
# ---------------------------------------------------------------------------


def test_enroll_from_frame_downscales_for_hog(monkeypatch) -> None:
    """HOG runs on a ≤480p frame; encoder runs on the full-res frame with
    rescaled bboxes. Keeps lock-hold time at ~50 ms instead of ~500 ms."""
    fr = sys.modules["face_recognition"]
    fr.face_locations = MagicMock(return_value=[(0, 100, 100, 0)])
    fr.face_encodings = MagicMock(return_value=[np.zeros(128)])
    monkeypatch.setattr(face_service, "face_recognition", fr, raising=False)
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)

    face_service.enroll_from_frame("X", _frame(h=960, w=1280))

    # face_locations should receive a frame downsized to ~480p.
    located_frame = fr.face_locations.call_args.args[0]
    assert located_frame.shape[0] == face_service.DOWNSCALE_HEIGHT

    # face_encodings should receive the full-res frame plus rescaled bboxes.
    encoded_frame, encoded_locations = (
        fr.face_encodings.call_args.args[0],
        fr.face_encodings.call_args.args[1],
    )
    assert encoded_frame.shape[0] == 960
    assert encoded_locations == [(0, 200, 200, 0)]  # scale = 960/480 = 2


def test_no_frames_persisted_during_face_rec(monkeypatch, tmp_path) -> None:
    """FR-KID-21: enrollment + recognition must not leave images on disk.

    The ``~/.myra/`` directory should contain only ``faces.pkl`` after a
    full enroll-then-identify cycle. No JPEG/PNG/raw frame artifacts.
    """
    myra_dir = tmp_path / ".myra"
    myra_dir.mkdir()
    monkeypatch.setenv("MYRA_FACES_FILE", str(myra_dir / "faces.pkl"))

    enc = np.arange(128, dtype=np.float64)
    _patch_detector(
        monkeypatch,
        locations=[(0, 100, 100, 0)],
        encodings=[enc],
        distances=np.array([0.1]),
    )

    assert face_service.enroll_from_frame("Aunt Priya", _frame()) is EnrollResult.OK
    assert face_service.identify_in_frame(_frame()) == ["Aunt Priya"]

    image_suffixes = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".raw"}
    leaked = [
        p for p in myra_dir.rglob("*") if p.is_file() and p.suffix.lower() in image_suffixes
    ]
    assert leaked == [], f"frames leaked to disk: {leaked}"
    assert {p.name for p in myra_dir.iterdir()} == {"faces.pkl"}
