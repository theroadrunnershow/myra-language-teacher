"""
robot_teacher.py — Reachy Mini × Myra Language Teacher (Option B)

The Myra FastAPI server runs locally on the Pi at port 8765.
This script starts it as a subprocess, then drives a toddler-friendly
lesson loop using the robot's microphones, speaker, and animations.

Usage:
  python robot_teacher.py [options]

  --language   telugu | assamese | both    (default: both)
  --categories animals,colors,food,...     (default: animals,colors,food,numbers)
  --words      10                          (default: 10 words per session)
  --threshold  50                          (default: 50 similarity threshold)
  --max-attempts 3                         (default: 3 tries per word)
  --no-server                              (skip auto-launching the Myra server)
  --server-dir /home/pollen/myra-...      (path to the app directory on Pi)

Port 8765 is used to avoid conflict with the Reachy Mini daemon (port 8000).
DISABLE_PASS1=true is set in the server subprocess to skip the slow
native-language Whisper pass — cuts recognition from ~6s to ~200ms on Pi CPU.
"""

import argparse
import atexit
import io
import logging
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time

import numpy as np
import requests
import scipy.io.wavfile as wavfile
from pydub import AudioSegment

# Deferred robot SDK import — allows audio bridge and HTTP functions to be
# tested without the robot attached (Tests 1–3 in the plan).
try:
    from reachy_mini import ReachyMini
    from reachy_mini.utils import create_head_pose
    _ROBOT_SDK_AVAILABLE = True
except ImportError:
    _ROBOT_SDK_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

# Option B: server runs on Pi — port 8765 avoids conflict with Reachy daemon
SERVER_URL = "http://localhost:8765"
SERVER_PORT = 8765

RECORD_DURATION = 5.0   # seconds to record per attempt
SAMPLE_RATE = 16000      # robot mic/speaker rate (fixed by SDK hardware)
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_THRESHOLD = 50   # permissive for a 4-year-old

DEFAULT_LANGUAGES = ["telugu", "assamese"]
DEFAULT_CATEGORIES = ["animals", "colors", "food", "numbers"]

# Directory containing main.py — used to start the server subprocess.
# Defaults to the same directory as this script.
DEFAULT_APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Strip emoji from TTS text (gTTS chokes on Unicode emoji characters)
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U00010000-\U0010FFFF"
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text).strip()


# ── Audio bridge ───────────────────────────────────────────────────────────────

def mic_samples_to_wav_bytes(samples: np.ndarray) -> bytes:
    """Convert robot mic numpy array → WAV bytes for POST /api/recognize.

    The SDK returns shape (N,) mono or (N, channels) multi-channel float32
    at 16 kHz. We mix to mono, clip, convert to int16, and write a standard
    WAV blob that speech_service.py already knows how to handle
    (audio_format="audio/wav" maps to MIME_TO_EXT["audio/wav"] = "wav").
    """
    if samples.ndim > 1:
        mono = samples.mean(axis=1)
    else:
        mono = samples.copy()
    mono = np.clip(mono, -1.0, 1.0)
    pcm16 = (mono * 32767).astype(np.int16)
    buf = io.BytesIO()
    wavfile.write(buf, SAMPLE_RATE, pcm16)
    return buf.getvalue()


def mp3_bytes_to_robot_samples(mp3_bytes: bytes) -> np.ndarray:
    """Convert MP3 bytes from GET /api/tts → numpy array for push_audio_sample().

    pydub is already in requirements.txt so no new dependency is needed.
    Returns shape (N, 1) float32 at SAMPLE_RATE Hz — the format the SDK expects.
    """
    seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    seg = seg.set_frame_rate(SAMPLE_RATE).set_channels(1)
    raw = np.array(seg.get_array_of_samples(), dtype=np.int16)
    samples = raw.astype(np.float32) / 32767.0
    return samples.reshape(-1, 1)


def _audio_duration(samples: np.ndarray) -> float:
    """Return duration in seconds for a (N, 1) float32 sample array."""
    return len(samples) / SAMPLE_RATE


# ── HTTP client wrappers ───────────────────────────────────────────────────────

def api_get_word(languages: list, categories: list) -> dict:
    """GET /api/word → {english, translation, romanized, emoji, language, category}"""
    r = requests.get(
        f"{SERVER_URL}/api/word",
        params={
            "languages": ",".join(languages),
            "categories": ",".join(categories),
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def api_get_tts(text: str, language: str, slow: bool = True) -> bytes:
    """GET /api/tts → raw MP3 bytes. Strips emoji before sending (gTTS requirement)."""
    clean = _strip_emoji(text)
    if not clean:
        return b""
    r = requests.get(
        f"{SERVER_URL}/api/tts",
        params={"text": clean, "language": language, "slow": str(slow).lower()},
        timeout=20,
    )
    r.raise_for_status()
    return r.content


def api_get_dino_voice(text: str) -> bytes:
    """GET /api/dino-voice → English TTS MP3 bytes for robot prompts."""
    r = requests.get(
        f"{SERVER_URL}/api/dino-voice",
        params={"text": text},
        timeout=20,
    )
    r.raise_for_status()
    return r.content


def api_recognize(
    wav_bytes: bytes,
    language: str,
    expected_word: str,
    romanized: str,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict:
    """POST /api/recognize → {transcribed, expected, similarity, is_correct, error}

    audio/wav is already in speech_service.MIME_TO_EXT so no server changes needed.
    Timeout is 30s to cover Whisper cold-start on the first call after boot.
    """
    r = requests.post(
        f"{SERVER_URL}/api/recognize",
        data={
            "language": language,
            "expected_word": expected_word,
            "romanized": romanized,
            "audio_format": "audio/wav",
            "similarity_threshold": str(threshold),
        },
        files={"audio": ("audio.wav", wav_bytes, "audio/wav")},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── Server management ──────────────────────────────────────────────────────────

def start_myra_server(app_dir: str = DEFAULT_APP_DIR) -> subprocess.Popen:
    """Launch the Myra FastAPI server as a subprocess on port 8765.

    Key environment overrides:
      DISABLE_PASS1=true  — skips the slow native-language Whisper pass
                            (~6 s saved per recognition on Pi CPU)
      PYTHONUNBUFFERED=1  — ensures server logs flush immediately
    """
    env = os.environ.copy()
    env["DISABLE_PASS1"] = "true"
    env["PYTHONUNBUFFERED"] = "1"

    logger.info(f"Starting Myra server at {app_dir} on port {SERVER_PORT}…")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1",
            "--port", str(SERVER_PORT),
            "--workers", "1",
        ],
        cwd=app_dir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def wait_for_server(timeout: float = 90.0) -> bool:
    """Poll GET /health until the server responds or timeout elapses.

    90 s covers Whisper model load (~30 s) + uvicorn startup on Pi.
    """
    deadline = time.time() + timeout
    dots = 0
    while time.time() < deadline:
        try:
            r = requests.get(f"{SERVER_URL}/health", timeout=2)
            if r.status_code == 200:
                print()  # newline after progress dots
                logger.info("Myra server is ready.")
                return True
        except requests.exceptions.RequestException:
            pass
        print(".", end="", flush=True)
        dots += 1
        time.sleep(1.0)
    print()
    return False


def warm_up_server() -> None:
    """Send a silent dummy recording to pre-load the Whisper model.

    Without this, the first real /api/recognize call triggers model load
    (~30 s on Pi 5), causing a long silence during Myra's first attempt.
    """
    logger.info("Pre-loading Whisper model (this takes ~30 s on first run)…")
    silence = np.zeros((SAMPLE_RATE * 2, 1), dtype=np.float32)
    wav_bytes = mic_samples_to_wav_bytes(silence)
    try:
        api_recognize(wav_bytes, "telugu", "పిల్లి", "pilli", threshold=0)
        logger.info("Whisper model ready.")
    except Exception as e:
        logger.warning(f"Warm-up request failed (non-fatal): {e}")


# ── Robot animation controller ─────────────────────────────────────────────────

class RobotController:
    """Manages Reachy Mini head and antenna animations for each lesson state.

    Background animations (idle, speak) loop in daemon threads.
    Foreground animations (celebrate, express_wrong) block the calling thread
    for their sequence duration, then restart idle automatically.

    All movement uses goto_target with method="minjerk" for smooth motion.
    Antenna values are in radians (use np.deg2rad() for degrees).
    """

    def __init__(self, mini):
        self._mini = mini
        self._stop_event = threading.Event()
        self._bg_thread: threading.Thread | None = None

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _stop_background(self):
        """Signal any running background loop to stop and wait for it."""
        self._stop_event.set()
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=4.0)
        self._stop_event.clear()
        self._bg_thread = None

    def _start_background(self, target, *args):
        self._stop_background()
        self._bg_thread = threading.Thread(target=target, args=args, daemon=True)
        self._bg_thread.start()

    # ── Idle: slow head sway, relaxed antennas ─────────────────────────────────

    def idle(self):
        """Start looping idle sway animation in the background."""
        self._start_background(self._idle_loop)

    def _idle_loop(self):
        while not self._stop_event.is_set():
            self._mini.goto_target(
                head=create_head_pose(roll=8, degrees=True),
                antennas=np.deg2rad([30, 30]),
                duration=2.0,
                method="minjerk",
            )
            if self._stop_event.is_set():
                break
            self._mini.goto_target(
                head=create_head_pose(roll=-8, degrees=True),
                antennas=np.deg2rad([30, 30]),
                duration=2.0,
                method="minjerk",
            )

    # ── Listen: curious head tilt, antennas perked up ─────────────────────────

    def listen(self):
        """Hold a curious head tilt while Myra is speaking into the mic."""
        self._stop_background()
        self._mini.goto_target(
            head=create_head_pose(roll=15, degrees=True),
            antennas=np.deg2rad([60, 60]),
            duration=0.8,
            method="minjerk",
        )

    # ── Speak: nodding head + alternating antenna wiggle during TTS ───────────

    def speak(self):
        """Start looping speak animation in the background."""
        self._start_background(self._speak_loop)

    def _speak_loop(self):
        while not self._stop_event.is_set():
            self._mini.goto_target(
                head=create_head_pose(z=6, mm=True),
                antennas=np.deg2rad([45, 20]),
                duration=0.3,
            )
            if self._stop_event.is_set():
                break
            self._mini.goto_target(
                head=create_head_pose(z=-2, mm=True),
                antennas=np.deg2rad([20, 45]),
                duration=0.3,
            )

    # ── Celebrate: enthusiastic head bobs + happy antenna wiggle ──────────────

    def celebrate(self):
        """Play 3-cycle celebration sequence, then return to idle."""
        self._stop_background()
        for _ in range(3):
            self._mini.goto_target(
                head=create_head_pose(z=15, mm=True),
                antennas=[0.8, -0.8],
                duration=0.3,
            )
            self._mini.goto_target(
                head=create_head_pose(z=-5, mm=True),
                antennas=[-0.8, 0.8],
                duration=0.3,
            )
        self.idle()

    # ── Wrong: head shake + drooping antennas ─────────────────────────────────

    def express_wrong(self):
        """Play a gentle head-shake sequence, then return to idle."""
        self._stop_background()
        self._mini.goto_target(
            head=create_head_pose(roll=12, degrees=True),
            antennas=np.deg2rad([-20, -20]),
            duration=0.35,
        )
        self._mini.goto_target(
            head=create_head_pose(roll=-12, degrees=True),
            duration=0.35,
        )
        self._mini.goto_target(
            head=create_head_pose(roll=0, degrees=True),
            antennas=np.deg2rad([0, 0]),
            duration=0.4,
        )
        self.idle()

    # ── Synchronized audio playback ───────────────────────────────────────────

    def play_audio(self, samples: np.ndarray):
        """Push audio to the speaker, run speak animation, block until done."""
        duration = _audio_duration(samples)
        self.speak()
        self._mini.media.push_audio_sample(samples)
        time.sleep(duration + 0.15)  # small buffer for audio tail
        self._stop_background()


# ── Lesson helpers ─────────────────────────────────────────────────────────────

def _play(robot: RobotController, mp3_bytes: bytes):
    """Decode MP3 bytes and play through the robot speaker with speak animation."""
    if not mp3_bytes:
        return
    samples = mp3_bytes_to_robot_samples(mp3_bytes)
    robot.play_audio(samples)


def _say(robot: RobotController, text: str):
    """Speak an English voice line via /api/dino-voice, silently fail if TTS errors."""
    try:
        _play(robot, api_get_dino_voice(text))
    except Exception as e:
        logger.warning(f"Voice line failed '{text[:40]}': {e}")
        time.sleep(1.5)  # pause so the lesson flow still feels natural


# ── Single-word lesson cycle ───────────────────────────────────────────────────

def run_lesson_word(
    mini,
    robot: RobotController,
    languages: list,
    categories: list,
    threshold: int,
    max_attempts: int,
) -> str:
    """Run one complete word lesson. Returns 'correct' | 'revealed' | 'error'."""

    # ── Fetch word ─────────────────────────────────────────────────────────────
    try:
        word = api_get_word(languages, categories)
    except Exception as e:
        logger.error(f"Failed to fetch word: {e}")
        return "error"

    language = word["language"]
    translation = word["translation"]
    romanized = word["romanized"]
    english = word["english"].upper()
    emoji = word.get("emoji", "")

    print(f"\n{'─' * 44}")
    print(f"  {emoji}  {english}  →  {translation}  ({romanized})")
    print(f"  Language: {language}   Category: {word['category']}")
    print(f"{'─' * 44}")

    # ── Pronounce the word twice so Myra hears it clearly ─────────────────────
    robot.idle()
    tts_mp3 = b""
    try:
        tts_mp3 = api_get_tts(translation, language, slow=True)
        _play(robot, tts_mp3)
        time.sleep(0.4)
        _play(robot, tts_mp3)
        time.sleep(0.3)
    except Exception as e:
        logger.warning(f"Word TTS failed: {e}")

    # ── Prompt Myra to repeat ──────────────────────────────────────────────────
    _say(robot, "Now you say it! Go ahead!")

    # ── Attempt loop ──────────────────────────────────────────────────────────
    for attempt in range(1, max_attempts + 1):
        print(f"\n  🎤  Listening… (attempt {attempt}/{max_attempts})")
        robot.listen()

        # Gap after TTS so the speaker echo doesn't bleed into the recording
        time.sleep(0.5)

        try:
            raw = mini.microphones.record(duration=RECORD_DURATION)
        except Exception as e:
            logger.error(f"Recording failed: {e}")
            return "error"

        wav_bytes = mic_samples_to_wav_bytes(raw)

        print("  🧠  Recognizing…")
        try:
            result = api_recognize(wav_bytes, language, translation, romanized, threshold)
        except Exception as e:
            logger.error(f"Recognize API failed: {e}")
            return "error"

        similarity = result.get("similarity", 0.0)
        is_correct = result.get("is_correct", False)
        transcribed = result.get("transcribed", "")

        mark = "✓" if is_correct else "✗"
        print(f"  Heard: '{transcribed}'  |  Score: {similarity:.0f}%  |  {mark}")

        if is_correct:
            print("  ✓ Correct!\n")
            robot.celebrate()
            praise = random.choice([
                "Amazing! You got it!",
                "Wonderful! You said it perfectly!",
                "Great job Myra! You are so smart!",
                "Yes! That is exactly right!",
            ])
            _say(robot, praise)
            return "correct"

        # Wrong answer
        robot.express_wrong()
        if attempt < max_attempts:
            retry = random.choice([
                "Try again! You can do it!",
                "Almost! One more time!",
                "Keep trying Myra! You've got this!",
            ])
            _say(robot, retry)
            # Replay the word so Myra can hear it again before retrying
            if tts_mp3:
                try:
                    _play(robot, tts_mp3)
                    time.sleep(0.3)
                except Exception:
                    pass

    # ── Out of attempts — reveal the word ─────────────────────────────────────
    print(f"\n  ℹ️  The word was: {translation} ({romanized})\n")
    robot.idle()
    _say(robot, "The word is…")
    if tts_mp3:
        try:
            _play(robot, tts_mp3)
        except Exception:
            pass
    _say(robot, "Let's try the next one!")
    return "revealed"


# ── Session loop ───────────────────────────────────────────────────────────────

def run_lesson_session(
    languages: list,
    categories: list,
    num_words: int,
    threshold: int,
    max_attempts: int,
):
    """Manage the ReachyMini context and run a full lesson session."""
    if not _ROBOT_SDK_AVAILABLE:
        raise SystemExit(
            "reachy-mini SDK not found. Install with:\n"
            "  pip install -r requirements-robot.txt\n"
            "Then re-run this script."
        )

    score = 0

    with ReachyMini() as mini:
        robot = RobotController(mini)

        print("\n" + "=" * 44)
        print("   🦕  Myra's Language Lesson  🦕")
        print("=" * 44 + "\n")

        robot.idle()
        _say(robot, "Hi Myra! Let's learn some words today! Are you ready?")

        for i in range(num_words):
            print(f"\n=== Word {i + 1} of {num_words} ===")
            outcome = run_lesson_word(
                mini, robot, languages, categories, threshold, max_attempts
            )
            if outcome == "correct":
                score += 1
            elif outcome == "error":
                logger.warning("Skipping word due to error.")

        # ── End of session ─────────────────────────────────────────────────────
        print("\n" + "=" * 44)
        print(f"  Session complete!  Score: {score} / {num_words}")
        print("=" * 44 + "\n")

        robot.celebrate()
        end_line = random.choice([
            f"Great job Myra! We learned {num_words} words today!",
            f"You got {score} out of {num_words}! You are amazing!",
        ])
        _say(robot, end_line)

        robot.idle()
        time.sleep(1.0)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Reachy Mini × Myra Language Teacher — Option B (fully on Pi)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--language",
        default="both",
        metavar="LANG",
        help="telugu | assamese | both",
    )
    parser.add_argument(
        "--categories",
        default="animals,colors,food,numbers",
        help="Comma-separated list of word categories",
    )
    parser.add_argument(
        "--words",
        type=int,
        default=10,
        metavar="N",
        help="Number of words in this session",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        metavar="0-100",
        help="Similarity score required to count as correct",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        metavar="N",
        help="Recording attempts per word before revealing the answer",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Skip auto-launching the Myra server (assume it is already running)",
    )
    parser.add_argument(
        "--server-dir",
        default=DEFAULT_APP_DIR,
        metavar="PATH",
        help="Path to the myra-language-teacher directory on the Pi",
    )
    args = parser.parse_args()

    # Resolve languages
    if args.language == "both":
        languages = ["telugu", "assamese"]
    elif args.language in ("telugu", "assamese"):
        languages = [args.language]
    else:
        parser.error(f"Unknown --language '{args.language}'. Use telugu, assamese, or both.")

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    if not categories:
        parser.error("--categories produced an empty list. Check the value.")

    # ── Server subprocess ──────────────────────────────────────────────────────
    server_proc = None
    if not args.no_server:
        server_proc = start_myra_server(app_dir=args.server_dir)

        def _shutdown():
            if server_proc and server_proc.poll() is None:
                logger.info("Shutting down Myra server…")
                server_proc.terminate()
                try:
                    server_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server_proc.kill()

        atexit.register(_shutdown)
        signal.signal(signal.SIGINT, lambda _s, _f: sys.exit(0))
        signal.signal(signal.SIGTERM, lambda _s, _f: sys.exit(0))

        print("Waiting for Myra server to start", end="", flush=True)
        if not wait_for_server(timeout=90.0):
            logger.error("Myra server did not start within 90 s. Check server-dir and try again.")
            sys.exit(1)

        warm_up_server()

    # ── Run the lesson ─────────────────────────────────────────────────────────
    try:
        run_lesson_session(
            languages=languages,
            categories=categories,
            num_words=args.words,
            threshold=args.threshold,
            max_attempts=args.max_attempts,
        )
    except KeyboardInterrupt:
        print("\nLesson stopped.")
    finally:
        if server_proc and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()


if __name__ == "__main__":
    main()
