# Face Recognition Design: Reachy Mini Robot

> **STATUS — SUPERSEDED (2026-04-24)**
>
> Face recognition is now part of [`camera-object-recognition-design.md`](camera-object-recognition-design.md) §2.6. That doc folds vision (object + identity) into the Pollen-based camera pipeline used by kids-teacher, adds **voice-driven enrollment** (the user says *"this is Aunt Priya"* and the robot calls a `remember_face` tool), supports up to **30 enrolled people**, and links face identity to persistent memory at `~/.myra/memory.md` + `~/.myra/faces.pkl`.
>
> This file is kept for historical reference. The dlib-based pipeline, encoding format, distance tolerance, and `scripts/enroll_faces.py` CLI described below remain accurate building blocks — but their integration target is now §2.6 of the camera doc, not the older `robot_teacher.py` flow. In particular:
>
> - **Enrollment:** voice-driven is now the primary path; CLI is the fallback for photo-based bulk seeding (FR-KID-12 in the camera doc).
> - **Recognition cadence:** session-start sweep + on-demand re-check (FR-KID-15/16), not just session-start.
> - **Capacity:** target raised from "<10" to 30 (FR-KID-13).
> - **Storage location:** moved from `faces/encodings.pkl` (in-repo) to `~/.myra/faces.pkl` (mirrors `~/.myra/memory.md`, gitignored, survives reinstalls).
> - **Memory linkage:** names + relationships go in `~/.myra/memory.md` per `tasks/plan-persistent-memory.md`; `faces.pkl` is purely the biometric index.
> - **Integration target:** `src/robot_kids_teacher.py` (kids-teacher mode on Reachy), not the older `src/robot_teacher.py`.

## Context

The robot needs to know who is in front of it when a session starts so it can greet them by name and personalize the lesson. There are fewer than 10 known people. Reference photos will be provided per person for enrollment.

**Key hardware facts:**
- Platform: Raspberry Pi 5 (8 GB RAM) — no cold-start, always-on
- Camera: Reachy Mini built-in head camera (accessed via SDK)
- Environment: Pure Python, synchronous threading (same as rest of `robot_teacher.py`)

**Chosen approach: `face_recognition` (dlib) — Python on Pi 5**

Rationale:
- Pi 5 has ample RAM (8 GB) and ARM Cortex-A76 CPU — dlib runs comfortably
- `face_recognition` gives 99.38% accuracy (LFW benchmark) with a 3-line API — no fine-tuning needed
- Pre-computed encodings mean zero ML work at runtime (just Euclidean distance)
- One-time identification at session start = simple, no continuous inference overhead
- Graceful degradation: if library not installed or camera fails → falls back to `"friend"` silently

---

## Architecture Overview

```
faces/
  encodings.pkl        ← pre-computed face encodings (gitignored, generated locally)
  myra/                ← reference images per person (gitignored, never committed)
    photo1.jpg
    photo2.jpg ...
  mom/
    photo1.jpg ...

src/
  face_service.py      ← new: camera capture + face recognition logic
  robot_teacher.py     ← modified: adds _identify_and_greet() at session start

scripts/
  enroll_faces.py      ← new: enrollment CLI (add/remove/list people)

tests/
  test_face_service.py ← new: unit tests with mocked camera + encodings
```

---

## Recognition Pipeline (Session Start)

```
run_lesson_session() starts
  └─ with ReachyMini() as mini:
       └─ _identify_and_greet(mini, robot, default_name)
            1. Play "Let me see who's here!" via TTS + robot.speak() animation
            2. Capture 5 frames from mini.camera (or OpenCV fallback)
            3. For each frame: detect faces (HOG model) → compute 128-D encoding
            4. Compare encoding against stored encodings (Euclidean distance ≤ 0.50)
            5. Return most-seen match (≥2 of 5 frames) or None
            6. If matched: play "Hi Myra! Great to see you!" + robot.celebrate()
            7. Set child_name = recognized name (or keep default "friend")
  └─ run_lesson_word() loop starts with personalized child_name
```

---

## New File: `src/face_service.py`

```python
"""
Face recognition service for Reachy Mini robot.
Uses face_recognition (dlib) for reliable identification of known people.
Pre-computed encodings are loaded from faces/encodings.pkl at runtime.
"""
import logging
import pickle
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

ENCODINGS_FILE = Path(__file__).parent.parent / "faces" / "encodings.pkl"
DEFAULT_TOLERANCE = 0.50     # Euclidean distance; lower = stricter (0.4–0.6 good range)
NUM_CAPTURE_FRAMES = 5       # Frames to sample; name must appear in ≥2 to be confirmed
MIN_HIT_THRESHOLD = 2        # Minimum matching frames before confirming identity


def load_encodings() -> dict:
    """Return {name: [np.ndarray, ...]} or {} if no encodings file exists."""
    if not ENCODINGS_FILE.exists():
        return {}
    with open(ENCODINGS_FILE, "rb") as f:
        return pickle.load(f)


def capture_frame(mini=None):
    """
    Capture one RGB frame as a numpy array (H×W×3 uint8).
    Tries Reachy SDK camera first (multiple API shapes); falls back to OpenCV.
    Returns None if capture fails.
    """
    if mini is not None:
        for attr in ("camera", "head"):
            cam = getattr(mini, attr, None)
            if cam is None:
                continue
            for method in ("get_frame", "capture"):
                fn = getattr(cam, method, None)
                if callable(fn):
                    try:
                        frame = fn()
                        if frame is not None and hasattr(frame, "shape"):
                            return frame  # expected: RGB numpy array
                    except Exception as exc:
                        logger.debug("SDK camera %s.%s failed: %s", attr, method, exc)
    # Fallback: OpenCV VideoCapture
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        ret, frame = cap.read()
        cap.release()
        if ret:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    except Exception as exc:
        logger.debug("OpenCV fallback failed: %s", exc)
    return None


def identify_person(
    mini=None,
    num_frames: int = NUM_CAPTURE_FRAMES,
    tolerance: float = DEFAULT_TOLERANCE,
) -> str | None:
    """
    Identify the person in front of the camera.
    Captures num_frames, returns the most consistently matched name.
    Returns None if no known face found with sufficient confidence.
    """
    import face_recognition  # imported lazily so missing library → graceful ImportError

    encodings = load_encodings()
    if not encodings:
        logger.info("No face encodings loaded — skipping recognition.")
        return None

    known_names: list[str] = []
    known_encs: list[np.ndarray] = []
    for name, encs in encodings.items():
        for enc in encs:
            known_names.append(name)
            known_encs.append(enc)

    hits: dict[str, int] = {}

    for frame_num in range(num_frames):
        frame = capture_frame(mini)
        if frame is None:
            logger.debug("Frame %d: capture returned None", frame_num)
            continue

        locations = face_recognition.face_locations(frame, model="hog")
        face_encs = face_recognition.face_encodings(frame, locations)

        for enc in face_encs:
            distances = face_recognition.face_distance(known_encs, enc)
            best_idx = int(np.argmin(distances))
            if distances[best_idx] <= tolerance:
                name = known_names[best_idx]
                hits[name] = hits.get(name, 0) + 1

    if not hits:
        logger.info("No known faces detected in %d frames.", num_frames)
        return None

    best_name = max(hits, key=hits.get)
    if hits[best_name] < MIN_HIT_THRESHOLD:
        logger.info(
            "Best match '%s' only appeared in %d/%d frames — below threshold.",
            best_name, hits[best_name], num_frames,
        )
        return None

    logger.info("Identified '%s' (%d/%d frames).", best_name, hits[best_name], num_frames)
    return best_name
```

---

## New File: `scripts/enroll_faces.py`

CLI tool to manage face encodings. Run once on the Pi before starting sessions.

```
Usage:
  python scripts/enroll_faces.py enroll --name myra --images faces/myra/*.jpg
  python scripts/enroll_faces.py enroll --name mom   --images faces/mom/*.jpg
  python scripts/enroll_faces.py list
  python scripts/enroll_faces.py remove --name myra
  python scripts/enroll_faces.py verify --name myra --image test.jpg
```

The `enroll` command:
1. Loads each image with `face_recognition.load_image_file()`
2. Calls `face_recognition.face_encodings(img)` — expects exactly 1 face per photo (warns and skips if 0 or 2+)
3. Appends encodings to the named key in the dict
4. Saves updated dict back to `faces/encodings.pkl`

---

## Modified File: `src/robot_teacher.py`

### Change 1 — New `_identify_and_greet()` helper (add before `run_lesson_word`)

```python
def _identify_and_greet(mini, robot: RobotController, default_name: str) -> str:
    """
    Attempt to identify the person via camera. Returns recognized name
    (capitalized) or default_name if unrecognized / face recognition unavailable.
    """
    try:
        from face_service import identify_person
    except ImportError:
        logger.debug("face_recognition not installed — skipping face ID.")
        return default_name

    try:
        prompt_mp3 = api_get_dino_voice("Let me see who's here!")
        robot.speak()
        robot.play_audio(mp3_bytes_to_robot_samples(prompt_mp3, robot.output_sample_rate))

        recognized = identify_person(mini, num_frames=5)

        if recognized:
            name = recognized.capitalize()
            greeting_mp3 = api_get_dino_voice(f"Hi {name}! Great to see you!")
            robot.celebrate()
            robot.play_audio(mp3_bytes_to_robot_samples(greeting_mp3, robot.output_sample_rate))
            return name
        return default_name
    except Exception as exc:
        logger.warning("Face recognition error (non-fatal): %s", exc)
        return default_name
```

### Change 2 — Add to `run_lesson_session()` signature + body

```python
def run_lesson_session(
    ...,
    enable_face_recognition: bool = True,   # new parameter
):
    with ReachyMini() as mini:
        robot = RobotController(mini)

        # Face recognition: identify who is practicing
        if enable_face_recognition:
            child_name = _identify_and_greet(mini, robot, child_name)

        # ... existing lesson loop unchanged ...
```

### Change 3 — Add CLI flag to `main()`

```python
parser.add_argument(
    "--no-face-recognition",
    action="store_true",
    help="Skip face recognition at session start.",
)
# Pass: enable_face_recognition=not args.no_face_recognition
```

---

## Modified File: `requirements-robot.txt`

```
# Face recognition (dlib-based). Requires build deps on Pi:
#   sudo apt-get install -y cmake libopenblas-dev liblapack-dev
face_recognition>=1.3.0
opencv-python-headless>=4.8.0
```

---

## New File: `tests/test_face_service.py`

Key test cases (mock camera + face_recognition, no real hardware needed):

| Test | Verifies |
|------|---------|
| `test_identify_returns_none_when_no_encodings_file` | No pkl → None |
| `test_identify_returns_none_when_no_faces_detected` | Blank frame → None |
| `test_identify_returns_name_with_enough_hits` | 3/5 frames match "myra" → "myra" |
| `test_identify_returns_none_below_min_threshold` | 1/5 frames match → None |
| `test_identify_picks_most_frequent_name` | 2×myra + 3×mom → "mom" |
| `test_identify_gracefully_returns_none_on_exception` | Camera crash → None |
| `test_capture_frame_tries_sdk_first` | `mini.camera.get_frame()` called before cv2 |
| `test_capture_frame_falls_back_to_opencv` | Missing SDK attr → cv2 fallback |

---

## `.gitignore` additions

```
faces/encodings.pkl
faces/*/
```
Encodings contain biometric data — never committed to the repo.

---

## Enrollment Workflow

```bash
# On the Pi, once face_recognition is installed:
mkdir -p faces/myra faces/mom

python scripts/enroll_faces.py enroll --name myra --images faces/myra/*.jpg
python scripts/enroll_faces.py enroll --name mom  --images faces/mom/*.jpg
python scripts/enroll_faces.py list
# myra (8 encodings), mom (6 encodings)

python scripts/enroll_faces.py verify --name myra --image test.jpg
```

---

## Reliability Measures

| Measure | Detail |
|---------|--------|
| Multi-frame sampling | 5 frames; ≥2 must agree to confirm identity |
| HOG detection | Fast CPU-mode face detector; good for frontal faces at arm's reach |
| Strict tolerance 0.50 | Tighter than dlib default (0.60); fewer false positives |
| `face_distance()` | Uses actual distance, not just boolean match — picks closest known encoding |
| Graceful degradation | `ImportError`, camera failure, no faces → falls back to `"friend"` silently |
| Session-start only | Single 5-frame burst; no continuous inference overhead |

---

## Verification Steps

1. `pytest` — all existing + new `test_face_service.py` tests pass
2. On Pi: `pip install -r requirements-robot.txt` (dlib builds ~10 min first time)
3. `python scripts/enroll_faces.py enroll --name test --images *.jpg`
4. `python scripts/enroll_faces.py verify --name test --image new.jpg` → match confirmed
5. `python src/robot_teacher.py` → robot says "Let me see who's here!" → identifies → greets by name
6. `python src/robot_teacher.py --no-face-recognition` → skips camera entirely
