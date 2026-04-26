"""Local face-recognition service for kids-teacher mode.

Pure functions over numpy frames. No camera coupling, no Gemini coupling.
Encodings persist at ``~/.myra/faces.pkl`` (override via ``MYRA_FACES_FILE``).
Frames are processed in-memory and discarded; only 128-D encodings are written
to disk. See `tasks/camera-object-recognition-design.md` §2.6.
"""

from __future__ import annotations

import logging
import os
import pickle
import tempfile
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

try:  # FR-KID-24 / NFR-7 — graceful degradation when dlib is unavailable.
    import face_recognition  # type: ignore

    HAS_FACE_REC = True
except ImportError:  # pragma: no cover — covered via monkeypatch in tests.
    face_recognition = None  # type: ignore[assignment]
    HAS_FACE_REC = False
    logger.warning("face_recognition not available — face-rec disabled")

FACES_FILE_ENV_VAR = "MYRA_FACES_FILE"
DEFAULT_FACES_FILE = Path("~/.myra/faces.pkl")
TOLERANCE_ENV_VAR = "KIDS_TEACHER_FACE_TOLERANCE"
DEFAULT_TOLERANCE = 0.50

MAX_NAMES = 30
MAX_ENCODINGS_PER_NAME = 8
DOWNSCALE_HEIGHT = 480

# dlib's HOG detector and CNN encoder share global model state and are NOT
# thread-safe. Two concurrent callers (e.g. the gaze tracker on the asyncio
# loop at 3 Hz and a `remember_face` enrollment dispatched via
# `asyncio.to_thread`) can corrupt the glibc freelist and abort the process
# with `corrupted double-linked list` (faulthandler trace 2026-04-26).
# Every dlib entry point in this module must run under this lock. Callers
# can stay synchronous; the lock is held for one HOG (~50 ms on a 480p
# frame) or one CNN encoding (~50 ms on a single face chip).
_DLIB_LOCK = threading.Lock()


class EnrollResult(Enum):
    OK = "ok"
    NO_FACE = "no_face"
    MULTIPLE_FACES = "multiple_faces"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    LIBRARY_MISSING = "library_missing"


@dataclass(frozen=True)
class _ResolvedTolerance:
    value: float


def _resolve_faces_path() -> Path:
    override = os.environ.get(FACES_FILE_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_FACES_FILE.expanduser()


def _resolve_tolerance(tolerance: float | None) -> float:
    if tolerance is not None:
        return tolerance
    raw = os.environ.get(TOLERANCE_ENV_VAR, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning("Invalid %s=%r; falling back to default", TOLERANCE_ENV_VAR, raw)
    return DEFAULT_TOLERANCE


def load_encodings() -> dict[str, list[np.ndarray]]:
    """Read ``faces.pkl`` from disk; return ``{}`` when missing."""
    target = _resolve_faces_path()
    if not target.exists():
        return {}
    with target.open("rb") as handle:
        data = pickle.load(handle)
    if not isinstance(data, dict):
        logger.warning("faces.pkl has unexpected shape (%s); ignoring", type(data).__name__)
        return {}
    return data


def save_encodings(encodings: dict[str, list[np.ndarray]]) -> None:
    """Atomically persist encodings (mirrors ``memory_file._atomic_write``)."""
    target = _resolve_faces_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=target.parent,
        prefix=f"{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        pickle.dump(encodings, handle)
        temp_path = Path(handle.name)
    os.replace(temp_path, target)


def _locate_in_rgb(
    rgb: np.ndarray, *, downscale: bool
) -> list[tuple[int, int, int, int]]:
    """Run dlib HOG face detection under :data:`_DLIB_LOCK`; return full-res coords.

    When ``downscale`` is True and ``rgb`` is taller than
    :data:`DOWNSCALE_HEIGHT`, run detection on a ~480p downscale and
    rescale bboxes back to the original-frame coordinate system. The
    downscale path keeps lock-hold time at ~50 ms instead of the
    ~200–500 ms a full-res HOG takes on a 720p+ frame.
    """
    if downscale and rgb.shape[0] > DOWNSCALE_HEIGHT:
        scale = rgb.shape[0] / DOWNSCALE_HEIGHT
        new_h = DOWNSCALE_HEIGHT
        new_w = max(1, int(round(rgb.shape[1] / scale)))
        # Cheap nearest-neighbour resize via numpy slicing — keeps the module
        # free of an OpenCV dep (face_recognition itself uses Pillow internally).
        ys = (np.linspace(0, rgb.shape[0] - 1, new_h)).astype(np.int64)
        xs = (np.linspace(0, rgb.shape[1] - 1, new_w)).astype(np.int64)
        small = rgb[ys][:, xs]
        with _DLIB_LOCK:
            locations = face_recognition.face_locations(small, model="hog")
        return [
            (
                int(round(top * scale)),
                int(round(right * scale)),
                int(round(bottom * scale)),
                int(round(left * scale)),
            )
            for (top, right, bottom, left) in locations
        ]
    with _DLIB_LOCK:
        locations = face_recognition.face_locations(rgb, model="hog")
    return [(int(t), int(r), int(b), int(l)) for (t, r, b, l) in locations]


def prewarm() -> None:
    """Force dlib's HOG + CNN models to load before the concurrent window opens.

    The first ``face_encodings`` call lazy-loads
    ``dlib_face_recognition_resnet_model_v1`` (~50 MB), which adds a one-off
    ~200 ms allocator spike. Doing it cold at startup, before
    `CameraWorker` and the gaze loop start, keeps that spike out of the
    window where it could overlap another native call. No-op when dlib is
    unavailable. Safe to call repeatedly.
    """
    if not HAS_FACE_REC:
        return
    dummy = np.zeros((128, 128, 3), dtype=np.uint8)
    try:
        with _DLIB_LOCK:
            # locations=[(top, right, bottom, left)] covers the whole dummy.
            face_recognition.face_encodings(dummy, [(0, 128, 128, 0)])
    except Exception:
        # Pre-warm is best-effort — never fatal.
        logger.debug("face_service.prewarm raised", exc_info=True)


def enroll_from_frame(
    name: str,
    frame: np.ndarray,
    relationship: str | None = None,
) -> EnrollResult:
    """Detect faces in ``frame`` and append the encoding under ``name``.

    ``frame`` is the BGR numpy array produced by ``CameraWorker``. dlib's
    HOG detector and CNN encoder were trained on RGB, so we swap channels
    before calling ``face_recognition`` — same idiom as
    :func:`encode_bgr_frame_as_jpeg`. Caller (Chunk G) decides what verbal
    response to produce and owns any ``memory.md`` writes; this module
    only persists the biometric encoding.

    Detection runs on a downscaled frame (≤480p) under :data:`_DLIB_LOCK`
    so the lock-hold time stays short under concurrent callers; encoding
    runs on the full-res frame with rescaled bboxes for full quality.
    """
    del relationship  # accepted for API parity with the tool-call layer; unused here.
    if not HAS_FACE_REC:
        return EnrollResult.LIBRARY_MISSING

    rgb = np.ascontiguousarray(frame[..., ::-1])
    locations = _locate_in_rgb(rgb, downscale=True)
    if len(locations) == 0:
        return EnrollResult.NO_FACE
    if len(locations) > 1:
        return EnrollResult.MULTIPLE_FACES

    encodings = load_encodings()
    if name not in encodings and len(encodings) >= MAX_NAMES:
        return EnrollResult.CAPACITY_EXCEEDED

    with _DLIB_LOCK:
        face_encs = face_recognition.face_encodings(rgb, locations)
    if not face_encs:
        # Detector saw a face but encoder couldn't compute it (rare blur/angle case).
        return EnrollResult.NO_FACE

    bucket = encodings.setdefault(name, [])
    bucket.append(face_encs[0])
    if len(bucket) > MAX_ENCODINGS_PER_NAME:
        # FIFO: drop the oldest encoding to keep ≤8 per name (FR-KID-13).
        del bucket[: len(bucket) - MAX_ENCODINGS_PER_NAME]

    save_encodings(encodings)
    return EnrollResult.OK


def identify_in_frame(
    frame: np.ndarray,
    tolerance: float | None = None,
) -> list[str]:
    """Return deduped names of recognized faces in ``frame`` (distance ≤ tolerance).

    ``frame`` is the BGR numpy array from ``CameraWorker``; we swap to RGB
    before calling ``face_recognition`` (see :func:`enroll_from_frame`).
    Detection + encoding both run under :data:`_DLIB_LOCK`.
    """
    if not HAS_FACE_REC:
        return []

    encodings = load_encodings()
    if not encodings:
        return []

    known_names: list[str] = []
    known_encs: list[np.ndarray] = []
    for name, encs in encodings.items():
        for enc in encs:
            known_names.append(name)
            known_encs.append(enc)

    rgb = np.ascontiguousarray(frame[..., ::-1])
    locations = _locate_in_rgb(rgb, downscale=False)
    if not locations:
        return []
    with _DLIB_LOCK:
        face_encs = face_recognition.face_encodings(rgb, locations)

    threshold = _resolve_tolerance(tolerance)
    seen: list[str] = []
    for enc in face_encs:
        distances = face_recognition.face_distance(known_encs, enc)
        if len(distances) == 0:
            continue
        best_idx = int(np.argmin(distances))
        if distances[best_idx] <= threshold:
            name = known_names[best_idx]
            if name not in seen:
                seen.append(name)
    return seen


def forget(name: str) -> bool:
    """Remove all encodings for ``name``. Returns True if anything was removed."""
    if not HAS_FACE_REC:
        return False
    encodings = load_encodings()
    if name not in encodings:
        return False
    del encodings[name]
    save_encodings(encodings)
    return True


def detect_face_bboxes(
    frame: np.ndarray,
    downscale: bool = True,
) -> list[tuple[int, int, int, int]]:
    """HOG bbox detection only (no encoding). Used by the gaze tracker (Chunk H).

    ``frame`` is the BGR numpy array from ``CameraWorker``; we swap to RGB
    before downscaling and detection (see :func:`enroll_from_frame`). When
    ``downscale`` is True, run detection on a ~480p downscale and rescale
    bboxes back to the original-frame coordinate system. Detection runs
    under :data:`_DLIB_LOCK`.
    """
    if not HAS_FACE_REC:
        return []
    rgb = np.ascontiguousarray(frame[..., ::-1])
    return _locate_in_rgb(rgb, downscale=downscale)
