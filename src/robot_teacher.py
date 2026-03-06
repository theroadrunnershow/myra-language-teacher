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
import scipy.signal
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

# Option A: use existing GCP Cloud Run server
# Option B: SERVER_URL = "http://localhost:8765" (server runs on Pi)
SERVER_URL = "https://kiddos-telugu-teacher.com"
SERVER_PORT = 8765  # only used if starting a local server subprocess (Option B)

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

def _extract_first_channel(samples: np.ndarray) -> np.ndarray:
    """Return a mono time-series from SDK audio frames with variable layout."""
    frame = np.asarray(samples)
    if frame.ndim == 0:
        return frame.reshape(1)
    if frame.ndim == 1:
        return frame
    if frame.ndim != 2:
        frame = np.squeeze(frame)
        if frame.ndim != 2:
            raise ValueError(f"Unsupported audio frame shape: {samples.shape}")

    # Match the official app's layout handling: time axis first, then channels.
    if frame.shape[1] > frame.shape[0]:
        frame = frame.T

    if frame.shape[1] > 1:
        frame = frame[:, 0]
    else:
        frame = frame[:, 0]
    return np.asarray(frame).reshape(-1)


def _to_float32_audio(samples: np.ndarray) -> np.ndarray:
    """Normalize audio to float32 in [-1, 1] without destroying PCM-like input."""
    audio = np.asarray(samples)
    if np.issubdtype(audio.dtype, np.integer):
        info = np.iinfo(audio.dtype)
        scale = float(max(abs(info.min), info.max)) or 1.0
        return audio.astype(np.float32) / scale

    audio = audio.astype(np.float32, copy=False)
    if audio.size == 0:
        return audio

    peak = float(np.max(np.abs(audio)))
    if peak > 1.5:
        if peak <= 32768.0 * 1.1:
            logger.warning(
                f"Mic samples exceed [-1, 1] (peak={peak:.1f}); treating them as PCM-like float data"
            )
            audio = audio / 32768.0
        else:
            logger.warning(
                f"Mic samples exceed the expected range (peak={peak:.1f}); normalizing by peak"
            )
            audio = audio / peak
    return audio


def _resample_audio(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample mono float32 audio to the target sample rate."""
    audio = np.asarray(samples, dtype=np.float32)
    if not len(audio) or src_rate <= 0 or dst_rate <= 0 or src_rate == dst_rate:
        return audio

    target_len = max(1, int(round(len(audio) * dst_rate / src_rate)))
    if target_len == len(audio):
        return audio
    return scipy.signal.resample(audio, target_len).astype(np.float32)


def mic_samples_to_wav_bytes(samples: np.ndarray, actual_rate: int = SAMPLE_RATE) -> bytes:
    """Convert robot mic numpy array → WAV bytes for POST /api/recognize.

    Mirrors the approach used by the official Pollen Robotics conversation app:
    - Extract first channel only (channel 0) — avoids phase cancellation from
      mixing spatially-separated mic array elements
    - Resample with scipy.signal.resample (FFT-based) to reach 16 kHz
    """
    mono = _to_float32_audio(_extract_first_channel(samples))

    logger.info(
        f"mic raw: shape={samples.shape} dtype={samples.dtype} "
        f"min={mono.min():.4f} max={mono.max():.4f} rms={float(np.sqrt(np.mean(mono**2))):.4f}"
    )

    # FFT-based resampling — same approach as official Pollen Robotics app
    mono = _resample_audio(mono, actual_rate, SAMPLE_RATE)

    mono = np.clip(mono, -1.0, 1.0)
    pcm16 = np.round(mono * 32767).astype(np.int16)
    buf = io.BytesIO()
    wavfile.write(buf, SAMPLE_RATE, pcm16)
    return buf.getvalue()


def wav_bytes_to_robot_samples(wav_bytes: bytes, output_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Convert WAV bytes → float32 (N, 1) at the robot's speaker sample rate."""
    input_rate, pcm = wavfile.read(io.BytesIO(wav_bytes))
    samples = _to_float32_audio(_extract_first_channel(pcm))
    samples = _resample_audio(samples, input_rate, output_rate)
    return samples.reshape(-1, 1)


def mp3_bytes_to_robot_samples(mp3_bytes: bytes, output_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Convert MP3 bytes from GET /api/tts → numpy array for push_audio_sample().

    pydub is already in requirements.txt so no new dependency is needed.
    Returns shape (N, 1) float32 at the robot's output sample rate.
    """
    seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    seg = seg.set_channels(1).set_frame_rate(output_rate)
    raw = np.array(seg.get_array_of_samples())
    samples = _to_float32_audio(raw)
    return samples.reshape(-1, 1)


def _audio_duration(samples: np.ndarray, sample_rate: int) -> float:
    """Return duration in seconds for a (N, 1) float32 sample array."""
    return len(samples) / max(sample_rate, 1)


def _drain_input_audio_queue(
    mini,
    sample_rate: int,
    poll_interval: float = 0.01,
    max_duration: float = 0.75,
) -> None:
    """Drop buffered mic frames so the next capture window starts fresh."""
    drained_chunks = 0
    drained_samples = 0
    empty_reads = 0
    deadline = time.time() + max_duration

    while empty_reads < 3 and time.time() < deadline:
        sample = mini.media.get_audio_sample()
        if sample is None:
            empty_reads += 1
            time.sleep(poll_interval)
            continue

        empty_reads = 0
        drained_chunks += 1
        drained_samples += len(_extract_first_channel(sample))

    if drained_chunks:
        suffix = "" if empty_reads >= 3 else " (flush timed out before queue went idle)"
        logger.info(
            f"Flushed mic backlog: chunks={drained_chunks} "
            f"duration={drained_samples / max(sample_rate, 1):.2f}s{suffix}"
        )


# ── HTTP session with retry (handles Cloud Run cold-start SSL drops) ───────────

def _make_session() -> requests.Session:
    """Session with retry (handles Cloud Run cold-start connection drops)."""
    from urllib3.util.retry import Retry
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    retry = Retry(total=3, backoff_factor=2,
                  status_forcelist=[502, 503, 504],
                  allowed_methods=["GET", "POST"])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

_session = _make_session()


# ── HTTP client wrappers ───────────────────────────────────────────────────────

def api_get_word(languages: list, categories: list) -> dict:
    """GET /api/word → {english, translation, romanized, emoji, language, category}"""
    r = _session.get(
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
    r = _session.get(
        f"{SERVER_URL}/api/tts",
        params={"text": clean, "language": language, "slow": str(slow).lower()},
        timeout=20,
    )
    r.raise_for_status()
    return r.content


def api_get_dino_voice(text: str) -> bytes:
    """GET /api/dino-voice → English TTS MP3 bytes for robot prompts."""
    r = _session.get(
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
    r = _session.post(
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
            r = _session.get(f"{SERVER_URL}/health", timeout=2)
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
        get_output_rate = getattr(self._mini.media, "get_output_audio_samplerate", None)
        self.output_sample_rate = (
            get_output_rate() if callable(get_output_rate) else SAMPLE_RATE
        ) or SAMPLE_RATE
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
        duration = _audio_duration(samples, self.output_sample_rate)
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        logger.info(
            f"Speaker: pushing {duration:.2f}s audio @ {self.output_sample_rate} Hz, "
            f"shape={samples.shape}, RMS={rms:.4f}"
        )
        self.speak()
        self._mini.media.push_audio_sample(samples)
        logger.info(f"Speaker: push_audio_sample() returned, sleeping {duration + 0.15:.2f}s")
        time.sleep(duration + 0.15)  # small buffer for audio tail
        logger.info("Speaker: done")
        self._stop_background()


# ── Lesson helpers ─────────────────────────────────────────────────────────────

def _play(robot: RobotController, mp3_bytes: bytes):
    """Decode MP3 bytes and play through the robot speaker with speak animation."""
    if not mp3_bytes:
        return
    samples = mp3_bytes_to_robot_samples(mp3_bytes, output_rate=robot.output_sample_rate)
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
    mic_rate: int = SAMPLE_RATE,
    debug_audio: bool = False,
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
        _drain_input_audio_queue(mini, mic_rate)

        try:
            chunks = []
            t_start = time.time()
            deadline = t_start + RECORD_DURATION
            while time.time() < deadline:
                sample = mini.media.get_audio_sample()
                if sample is not None:
                    mono = _extract_first_channel(sample)
                    if mono.size:
                        chunks.append(mono)
                time.sleep(0.02)
            elapsed = time.time() - t_start

            if chunks:
                raw = np.concatenate(chunks)
                n_samples = len(raw)
                actual_rate = mic_rate
                logger.info(
                    f"Chunk[0] shape={chunks[0].shape} dtype={chunks[0].dtype}  "
                    f"total_samples={n_samples}  elapsed={elapsed:.2f}s"
                )

                # Follow the official app and trust the robot-reported sample rate.
                detected = n_samples / elapsed if elapsed > 0 else 0.0
                if detected and abs(detected - mic_rate) / max(mic_rate, 1) > 0.2:
                    logger.warning(
                        f"Observed {detected:.0f} samples/s while robot reports {mic_rate} Hz; "
                        f"keeping the SDK rate to match the official app"
                    )

                raw_float = _to_float32_audio(raw)
                rms = float(np.sqrt(np.mean(raw_float ** 2)))
                logger.info(
                    f"Recorded {n_samples/actual_rate:.1f}s @ {actual_rate} Hz — "
                    f"RMS={rms:.4f} "
                    f"({'silence' if rms < 0.002 else 'has audio'}), "
                    f"chunks={len(chunks)}"
                )
            else:
                actual_rate = mic_rate
                logger.warning("No audio chunks received — mic pipeline not live")
                raw = np.zeros(int(mic_rate * RECORD_DURATION), dtype=np.float32)
        except Exception as e:
            logger.error(f"Recording failed: {e}")
            return "error"

        wav_bytes = mic_samples_to_wav_bytes(raw, actual_rate=actual_rate)

        if debug_audio:
            debug_path = "/tmp/myra_debug.wav"
            with open(debug_path, "wb") as f:
                f.write(wav_bytes)
            logger.info(f"Debug WAV saved → {debug_path}")
            _say(robot, "I heard you say...")
            try:
                playback = wav_bytes_to_robot_samples(
                    wav_bytes,
                    output_rate=robot.output_sample_rate,
                )
                robot.play_audio(playback)
            except Exception as e:
                logger.warning(f"Debug playback failed: {e}")
            _say(robot, "Let me check if that is correct!")

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
    debug_audio: bool = False,
):
    """Manage the ReachyMini context and run a full lesson session."""
    if not _ROBOT_SDK_AVAILABLE:
        raise SystemExit(
            "reachy-mini SDK not found. Install with:\n"
            "  pip install -r requirements-robot.txt\n"
            "Then re-run this script."
        )

    score = 0

    with ReachyMini(media_backend="default") as mini:
        robot = RobotController(mini)

        # Open audio pipelines once for the whole session.
        # default backend (Sounddevice) needs both active simultaneously:
        # start_playing keeps the output pipeline open for push_audio_sample,
        # start_recording keeps the input pipeline open for get_audio_sample.
        mic_rate = mini.media.get_input_audio_samplerate() or SAMPLE_RATE
        logger.info(f"Mic sample rate: {mic_rate} Hz")
        logger.info(f"Speaker sample rate: {robot.output_sample_rate} Hz")
        mini.media.start_playing()
        mini.media.start_recording()

        print("\n" + "=" * 44)
        print("   🦕  Myra's Language Lesson  🦕")
        print("=" * 44 + "\n")

        robot.idle()
        _say(robot, "Hi Myra! Let's learn some words today! Are you ready?")

        for i in range(num_words):
            print(f"\n=== Word {i + 1} of {num_words} ===")
            outcome = run_lesson_word(
                mini, robot, languages, categories, threshold, max_attempts,
                mic_rate=mic_rate, debug_audio=debug_audio,
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

        mini.media.stop_recording()
        mini.media.stop_playing()


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
    parser.add_argument(
        "--debug-audio",
        action="store_true",
        help="Play back each recording through the speaker before recognizing; save to /tmp/myra_debug.wav",
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
            debug_audio=args.debug_audio,
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
