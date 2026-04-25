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
