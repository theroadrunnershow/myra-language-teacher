#!/usr/bin/env python3
"""CLI to bulk-enroll face encodings from reference photos on disk.

Bootstraps ``~/.myra/faces.pkl`` (override via ``MYRA_FACES_FILE``) before the
voice-driven enrollment path is available. See FR-KID-12 in
``tasks/camera-object-recognition-design.md`` §2.6.1.

Usage::

    python scripts/enroll_faces.py --name "Aunt Priya" photo1.jpg [photo2.jpg ...]
    python scripts/enroll_faces.py --name "Aunt Priya" --dir path/to/priya_photos/
    python scripts/enroll_faces.py --list
    python scripts/enroll_faces.py --forget "Aunt Priya"

Requires ``face_recognition`` (dlib). On a fresh Pi 5::

    sudo apt-get install -y cmake libopenblas-dev liblapack-dev
    pip install -r requirements-robot.txt   # builds dlib (~10 min one-time)

Exit codes: 0 success, 1 user error, 2 library missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure ``src/`` is importable when running the script directly from a checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import face_service  # noqa: E402 — sys.path tweak above

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}

_RESULT_MESSAGES = {
    face_service.EnrollResult.OK: "ok",
    face_service.EnrollResult.NO_FACE: "no face",
    face_service.EnrollResult.MULTIPLE_FACES: "multiple faces — skipping",
    face_service.EnrollResult.CAPACITY_EXCEEDED: "capacity exceeded",
    face_service.EnrollResult.LIBRARY_MISSING: "face_recognition library missing",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enroll_faces",
        description="Bulk-enroll face encodings from reference photos.",
    )
    parser.add_argument("--name", help="Person's name (required for enrollment).")
    parser.add_argument(
        "--dir",
        dest="directory",
        help="Directory of photos to enroll (alternative to listing paths).",
    )
    parser.add_argument(
        "--list",
        dest="list_names",
        action="store_true",
        help="Print enrolled names and encoding counts.",
    )
    parser.add_argument(
        "--forget",
        dest="forget_name",
        help="Remove all encodings for this name.",
    )
    parser.add_argument(
        "photos",
        nargs="*",
        help="Photo paths to enroll for --name.",
    )
    return parser


def _gather_photo_paths(
    photos: list[str],
    directory: str | None,
) -> tuple[list[Path], str | None]:
    """Return ``(paths, error)``. ``error`` is None on success."""
    paths: list[Path] = []
    if directory:
        dir_path = Path(directory).expanduser()
        if not dir_path.is_dir():
            return [], f"--dir not found: {directory}"
        for child in sorted(dir_path.iterdir()):
            if child.is_file() and child.suffix.lower() in _IMAGE_SUFFIXES:
                paths.append(child)
    for raw in photos:
        paths.append(Path(raw).expanduser())
    return paths, None


def _load_image(path: Path):
    """Load an image as an RGB numpy array via ``face_recognition``."""
    import face_recognition  # type: ignore

    return face_recognition.load_image_file(str(path))


def _do_list() -> int:
    encodings = face_service.load_encodings()
    for name in sorted(encodings):
        count = len(encodings[name])
        suffix = "encoding" if count == 1 else "encodings"
        print(f"{name}: {count} {suffix}")
    return 0


def _do_forget(name: str) -> int:
    if face_service.forget(name):
        print(f"Removed all encodings for {name}")
    else:
        print(f"No encodings found for {name}")
    return 0


def _do_enroll(name: str, paths: list[Path]) -> int:
    enrolled = 0
    for path in paths:
        if not path.exists():
            print(f"{path}: file not found")
            continue
        try:
            frame = _load_image(path)
        except Exception as exc:  # noqa: BLE001 — surface any loader failure to user
            print(f"{path}: failed to load image ({exc})")
            continue
        result = face_service.enroll_from_frame(name, frame, relationship=None)
        message = _RESULT_MESSAGES.get(result, str(result))
        print(f"{path}: {message}")
        if result is face_service.EnrollResult.OK:
            enrolled += 1
    total = len(face_service.load_encodings().get(name, []))
    plural = "encoding" if enrolled == 1 else "encodings"
    print(f"Enrolled {enrolled} {plural} for {name}; total now {total}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not face_service.HAS_FACE_REC:
        print(
            "face_recognition library not available — install dlib + face_recognition "
            "(see scripts/enroll_faces.py docstring).",
            file=sys.stderr,
        )
        return 2

    if args.list_names:
        return _do_list()

    if args.forget_name:
        return _do_forget(args.forget_name)

    if not args.name:
        parser.print_usage(sys.stderr)
        print("error: --name is required for enrollment", file=sys.stderr)
        return 1

    paths, err = _gather_photo_paths(args.photos, args.directory)
    if err is not None:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if not paths:
        print("error: no photo paths supplied (use positional args or --dir)", file=sys.stderr)
        return 1

    return _do_enroll(args.name, paths)


if __name__ == "__main__":  # pragma: no cover — exercised via tests as main(argv).
    sys.exit(main())
