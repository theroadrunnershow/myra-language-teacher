"""Tests for ``scripts/enroll_faces.py`` (FR-KID-12).

The script is imported as a module via a sys.path tweak; tests invoke
``main(argv)`` directly. ``face_recognition`` is the conftest MagicMock stub;
``face_service`` is mocked at the seam where convenient so we never depend on
dlib being installed.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Make scripts/ importable so we can call main(argv) directly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import enroll_faces  # noqa: E402 — import after sys.path tweak
import face_service  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_faces_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MYRA_FACES_FILE", str(tmp_path / "faces.pkl"))
    # Most tests assume dlib is "available"; the library-missing test overrides.
    monkeypatch.setattr(face_service, "HAS_FACE_REC", True, raising=False)
    monkeypatch.setattr(enroll_faces.face_service, "HAS_FACE_REC", True, raising=False)
    yield


def _stub_load_image(monkeypatch) -> MagicMock:
    """Replace ``_load_image`` so tests don't need real photo bytes on disk."""
    fake_frame = np.zeros((10, 10, 3), dtype=np.uint8)
    loader = MagicMock(return_value=fake_frame)
    monkeypatch.setattr(enroll_faces, "_load_image", loader)
    return loader


def test_enroll_single_photo_calls_face_service(monkeypatch, tmp_path, capsys):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"")  # existence is what matters; loader is stubbed.
    _stub_load_image(monkeypatch)
    enroll_mock = MagicMock(return_value=face_service.EnrollResult.OK)
    monkeypatch.setattr(enroll_faces.face_service, "enroll_from_frame", enroll_mock)
    # Stub load_encodings so the summary read-back works without a real pkl.
    monkeypatch.setattr(
        enroll_faces.face_service,
        "load_encodings",
        MagicMock(return_value={"X": [np.zeros(128)]}),
    )

    rc = enroll_faces.main(["--name", "X", str(photo)])

    assert rc == 0
    enroll_mock.assert_called_once()
    args, kwargs = enroll_mock.call_args
    assert args[0] == "X"
    assert isinstance(args[1], np.ndarray)
    assert kwargs.get("relationship") is None
    out = capsys.readouterr().out
    assert f"{photo}: ok" in out
    assert "Enrolled 1 encoding for X; total now 1" in out


def test_enroll_dir_processes_all_images(monkeypatch, tmp_path, capsys):
    for fname in ("a.jpg", "b.png", "c.jpeg"):
        (tmp_path / fname).write_bytes(b"")
    # Drop a non-image to verify suffix filtering.
    (tmp_path / "notes.txt").write_text("ignore me")
    _stub_load_image(monkeypatch)
    enroll_mock = MagicMock(return_value=face_service.EnrollResult.OK)
    monkeypatch.setattr(enroll_faces.face_service, "enroll_from_frame", enroll_mock)
    monkeypatch.setattr(
        enroll_faces.face_service,
        "load_encodings",
        MagicMock(return_value={"X": [np.zeros(128), np.zeros(128), np.zeros(128)]}),
    )

    rc = enroll_faces.main(["--name", "X", "--dir", str(tmp_path)])

    assert rc == 0
    assert enroll_mock.call_count == 3


def test_enroll_no_face_does_not_abort_pipeline(monkeypatch, tmp_path, capsys):
    photos = []
    for fname in ("a.jpg", "b.jpg", "c.jpg"):
        p = tmp_path / fname
        p.write_bytes(b"")
        photos.append(p)
    _stub_load_image(monkeypatch)

    results = [
        face_service.EnrollResult.OK,
        face_service.EnrollResult.NO_FACE,
        face_service.EnrollResult.OK,
    ]
    enroll_mock = MagicMock(side_effect=results)
    monkeypatch.setattr(enroll_faces.face_service, "enroll_from_frame", enroll_mock)
    monkeypatch.setattr(
        enroll_faces.face_service,
        "load_encodings",
        MagicMock(return_value={"X": [np.zeros(128), np.zeros(128)]}),
    )

    rc = enroll_faces.main(["--name", "X", *map(str, photos)])

    assert rc == 0
    assert enroll_mock.call_count == 3
    out = capsys.readouterr().out
    assert f"{photos[0]}: ok" in out
    assert f"{photos[1]}: no face" in out
    assert f"{photos[2]}: ok" in out
    assert "Enrolled 2 encodings for X; total now 2" in out


def test_list_prints_known_names(monkeypatch, capsys):
    monkeypatch.setattr(
        enroll_faces.face_service,
        "load_encodings",
        MagicMock(
            return_value={
                "Zara": [np.zeros(128)],
                "Aunt Priya": [np.zeros(128), np.zeros(128)],
            }
        ),
    )

    rc = enroll_faces.main(["--list"])

    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line]
    assert lines == [
        "Aunt Priya: 2 encodings",
        "Zara: 1 encoding",
    ]


def test_forget_removes_known_name(monkeypatch, capsys):
    forget_mock = MagicMock(return_value=True)
    monkeypatch.setattr(enroll_faces.face_service, "forget", forget_mock)

    rc = enroll_faces.main(["--forget", "Aunt Priya"])

    assert rc == 0
    forget_mock.assert_called_once_with("Aunt Priya")
    assert "Removed all encodings for Aunt Priya" in capsys.readouterr().out

    forget_mock.return_value = False
    rc = enroll_faces.main(["--forget", "Nobody"])
    assert rc == 0
    assert "No encodings found for Nobody" in capsys.readouterr().out


def test_help_does_not_crash(capsys):
    with pytest.raises(SystemExit) as excinfo:
        enroll_faces.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "enroll_faces" in out


def test_library_missing_exits_with_code_2(monkeypatch, capsys):
    monkeypatch.setattr(enroll_faces.face_service, "HAS_FACE_REC", False, raising=False)

    rc = enroll_faces.main(["--list"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "face_recognition library not available" in err


def test_enroll_without_name_returns_user_error(capsys, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"")

    rc = enroll_faces.main([str(photo)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "--name is required" in err


def test_enroll_without_photos_returns_user_error(capsys):
    rc = enroll_faces.main(["--name", "X"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no photo paths supplied" in err


def test_enroll_missing_photo_path_does_not_abort(monkeypatch, tmp_path, capsys):
    real_photo = tmp_path / "real.jpg"
    real_photo.write_bytes(b"")
    missing = tmp_path / "missing.jpg"  # never written
    _stub_load_image(monkeypatch)
    enroll_mock = MagicMock(return_value=face_service.EnrollResult.OK)
    monkeypatch.setattr(enroll_faces.face_service, "enroll_from_frame", enroll_mock)
    monkeypatch.setattr(
        enroll_faces.face_service,
        "load_encodings",
        MagicMock(return_value={"X": [np.zeros(128)]}),
    )

    rc = enroll_faces.main(["--name", "X", str(real_photo), str(missing)])

    assert rc == 0
    out = capsys.readouterr().out
    assert f"{missing}: file not found" in out
    assert f"{real_photo}: ok" in out
    # Only the existing file produced an enroll call.
    assert enroll_mock.call_count == 1
