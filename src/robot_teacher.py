"""
robot_teacher.py — Reachy Mini × Myra Language Teacher

This script can either talk to the hosted Myra server (`--runtime-mode cloud`)
or start and use a Pi-local FastAPI server (`--runtime-mode reachy_local`).
It then drives a toddler-friendly lesson loop using the robot's microphones,
speaker, and animations.

Usage:
  python robot_teacher.py [options]

  --language   telugu | assamese | tamil | malayalam | both | all    (default: both)
  --categories animals,colors,food,...     (default: animals,colors,food,numbers)
  --words      10                          (default: 10 words per session)
  --threshold  50                          (default: 50 similarity threshold)
  --max-attempts 3                         (default: 3 tries per word)
  --runtime-mode cloud|reachy_local
  --no-server                              (only for reachy_local)
  --server-dir /home/pollen/myra-...      (path to the app directory on Pi)
  --words-sync-to-gcs never|session_end|shutdown

Port 8765 is used to avoid conflict with the Reachy Mini daemon (port 8000).
DISABLE_PASS1=true is set in the server subprocess to skip the slow
native-language Whisper pass — cuts recognition from ~6s to ~200ms on Pi CPU.
"""

from __future__ import annotations

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
from concurrent.futures import ThreadPoolExecutor

from env_loader import load_project_dotenv
from language_config import SUPPORTED_LESSON_LANGUAGES

import numpy as np
import requests
import scipy.signal
import scipy.io.wavfile as wavfile
from pydub import AudioSegment

load_project_dotenv()

# Deferred robot SDK import — allows audio bridge and HTTP functions to be
# tested without the robot attached (Tests 1–3 in the plan).
try:
    from reachy_mini import ReachyMini
    from reachy_mini.utils import create_head_pose
    _ROBOT_SDK_AVAILABLE = True
except ImportError:
    _ROBOT_SDK_AVAILABLE = False

    def create_head_pose(**kwargs):
        """Fallback used in tests when the Reachy SDK is unavailable."""
        return kwargs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

RUNTIME_MODES = {"cloud", "reachy_local"}
CLOUD_SERVER_URL = "https://kiddos-telugu-teacher.com"
SERVER_PORT = 8765  # only used if starting a local server subprocess (Option B)
LOCAL_SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"
SERVER_URL = CLOUD_SERVER_URL

RECORD_DURATION = 5.0   # seconds to record per attempt
SAMPLE_RATE = 16000      # robot mic/speaker rate (fixed by SDK hardware)
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_THRESHOLD = 50   # permissive for a 4-year-old

DEFAULT_LANGUAGES = list(SUPPORTED_LESSON_LANGUAGES)
DEFAULT_CATEGORIES = ["animals", "colors", "food", "numbers"]
REPLAY_WORD_BATCH = 5
PLAY_AGAIN_RECORD_DURATION_SEC = 4.0

# Directory containing main.py — used to start the server subprocess.
# Auto-detects: if main.py is next to this script use that dir, otherwise
# fall back to a src/ subdirectory (handles Pi deployments where
# robot_teacher.py lives at project root but main.py is in src/).
_script_dir = os.path.dirname(os.path.abspath(__file__))
DEFAULT_APP_DIR = (
    _script_dir
    if os.path.isfile(os.path.join(_script_dir, "main.py"))
    else os.path.join(_script_dir, "src")
)

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
    max_duration: float = 30.0,
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
            time.sleep(poll_interval)  # only sleep when queue is empty
            continue

        # Got a chunk — drain as fast as possible, no sleep
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


def resolve_server_url(runtime_mode: str, server_url: str = "") -> str:
    if server_url:
        return server_url
    if runtime_mode == "cloud":
        return CLOUD_SERVER_URL
    if runtime_mode == "reachy_local":
        return LOCAL_SERVER_URL
    raise ValueError(f"Unknown runtime_mode '{runtime_mode}'")


def configure_server_url(runtime_mode: str, server_url: str = "") -> str:
    global SERVER_URL
    SERVER_URL = resolve_server_url(runtime_mode, server_url)
    return SERVER_URL


def should_start_local_server(runtime_mode: str, no_server: bool, server_url: str = "") -> bool:
    return runtime_mode == "reachy_local" and not no_server and not server_url


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


def api_sync_words_to_gcs() -> bool:
    """POST /api/internal/words/sync → upload local custom words snapshot to GCS."""
    r = _session.post(
        f"{SERVER_URL}/api/internal/words/sync",
        timeout=30,
    )
    r.raise_for_status()
    return bool(r.json().get("synced"))


# ── Server management ──────────────────────────────────────────────────────────

def start_myra_server(
    app_dir: str = DEFAULT_APP_DIR,
    *,
    words_sync_to_gcs: str = "never",
    gcs_bucket: str = "",
) -> subprocess.Popen:
    """Launch the Myra FastAPI server as a subprocess on port 8765.

    Key environment overrides:
      DISABLE_PASS1=true       — skips the slow native-language Whisper pass
                                 (~6 s saved per recognition on Pi CPU)
      PYTHONUNBUFFERED=1       — ensures server logs flush immediately
      WORDS_OBJECT_BUCKET      — GCS bucket for dynamic-words sync/load
    """
    env = os.environ.copy()
    env["DISABLE_PASS1"] = "true"
    env["PYTHONUNBUFFERED"] = "1"
    env["WORDS_SYNC_TO_GCS"] = words_sync_to_gcs
    if gcs_bucket:
        env["WORDS_OBJECT_BUCKET"] = gcs_bucket

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
        stdout=None,   # inherit — server logs visible in terminal
        stderr=None,
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
        self._speaker_primed = False
        self._stop_event = threading.Event()
        self._bg_thread: threading.Thread | None = None
        self._bg_generation = 0
        self._motion_lock = threading.Lock()
        self._motion_failure_count = 0
        self._motion_cooldown_until = 0.0
        self._last_motion_error: str | None = None

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _stop_background(self):
        """Signal any running background loop to stop and wait for it."""
        self._bg_generation += 1
        self._stop_event.set()
        bg_thread = self._bg_thread
        if bg_thread and bg_thread.is_alive():
            # Skip join when called from within the background thread itself
            # (e.g. _celebrate_loop calling idle() at its end) — joining the
            # current thread raises RuntimeError("cannot join current thread").
            if bg_thread is not threading.current_thread():
                bg_thread.join(timeout=4.0)
                if bg_thread.is_alive():
                    logger.warning(
                        "Previous robot animation thread did not stop within 4.0s; "
                        "suppressing stale motions until it exits."
                    )
        self._bg_thread = None

    def _start_background(self, target, *args):
        self._stop_background()
        self._stop_event.clear()
        token = self._bg_generation
        self._bg_thread = threading.Thread(target=target, args=(token, *args), daemon=True)
        self._bg_thread.start()

    def _background_should_stop(self, token: int | None = None) -> bool:
        if token is None:
            return False
        if self._stop_event.is_set():
            return True
        return token != self._bg_generation

    def _sleep_interruptibly(self, duration: float, token: int | None = None):
        """Sleep in small increments so stop requests interrupt animation cooldowns."""
        remaining = max(float(duration), 0.0)
        while remaining > 0 and not self._background_should_stop(token):
            step = min(remaining, 0.05)
            time.sleep(step)
            remaining -= step

    def _record_motion_failure(self, context: str, error: Exception, duration: float) -> bool:
        self._motion_failure_count += 1
        backoff = min(5.0, max(duration, 0.2) * min(self._motion_failure_count, 5))
        self._motion_cooldown_until = time.monotonic() + backoff
        error_text = f"{type(error).__name__}: {error}"
        if error_text != self._last_motion_error:
            logger.warning(
                "Robot motion failed during %s: %s. Continuing without this animation; "
                "check motor connections and power if this persists.",
                context,
                error_text,
            )
            self._last_motion_error = error_text
        return False

    def _acquire_motion_slot(
        self, duration: float, token: int | None = None, timeout_slack: float = 0.35
    ) -> bool:
        deadline = time.monotonic() + max(duration + timeout_slack, 0.5)
        while time.monotonic() < deadline and not self._background_should_stop(token):
            remaining = deadline - time.monotonic()
            if self._motion_lock.acquire(timeout=min(0.05, max(remaining, 0.0))):
                return True
        return False

    def _goto_target_safe(
        self,
        context: str,
        preserve_timing: bool = False,
        token: int | None = None,
        **kwargs,
    ) -> bool:
        """Run a robot motion without letting hardware faults kill the lesson flow."""
        duration = max(float(kwargs.get("duration", 0.0) or 0.0), 0.0)
        if self._background_should_stop(token):
            return False
        now = time.monotonic()
        if now < self._motion_cooldown_until:
            if preserve_timing and duration:
                self._sleep_interruptibly(duration, token=token)
            return False

        motion_kwargs = dict(kwargs)
        for attempt in range(2):
            attempt_duration = max(float(motion_kwargs.get("duration", 0.0) or 0.0), 0.0)
            if not self._acquire_motion_slot(attempt_duration, token=token):
                if self._background_should_stop(token):
                    return False
                return self._record_motion_failure(
                    context,
                    TimeoutError("Previous motion is still in progress."),
                    attempt_duration,
                )

            try:
                self._mini.goto_target(**motion_kwargs)
            except TimeoutError as e:
                if attempt == 0 and not self._background_should_stop(token):
                    retry_duration = max(attempt_duration * 2.0, attempt_duration + 0.25, 0.5)
                    motion_kwargs = {**motion_kwargs, "duration": retry_duration}
                    logger.info(
                        "Robot motion timed out during %s; retrying once with %.2fs duration.",
                        context,
                        retry_duration,
                    )
                    continue
                return self._record_motion_failure(context, e, attempt_duration)
            except Exception as e:
                return self._record_motion_failure(context, e, attempt_duration)
            else:
                if self._motion_failure_count:
                    logger.info("Robot motion recovered.")
                self._motion_failure_count = 0
                self._motion_cooldown_until = 0.0
                self._last_motion_error = None
                return True
            finally:
                self._motion_lock.release()

        return False

    # ── Idle: slow head sway, relaxed antennas ─────────────────────────────────

    def idle(self):
        """Start looping idle sway animation in the background."""
        self._start_background(self._idle_loop)

    def _idle_loop(self, token: int):
        while not self._background_should_stop(token):
            self._goto_target_safe(
                "idle sway left",
                preserve_timing=True,
                token=token,
                head=create_head_pose(roll=8, degrees=True),
                antennas=np.deg2rad([30, 30]),
                duration=2.0,
                method="minjerk",
            )
            if self._background_should_stop(token):
                break
            self._goto_target_safe(
                "idle sway right",
                preserve_timing=True,
                token=token,
                head=create_head_pose(roll=-8, degrees=True),
                antennas=np.deg2rad([30, 30]),
                duration=2.0,
                method="minjerk",
            )

    # ── Listen: curious head tilt, antennas perked up ─────────────────────────

    def listen(self):
        """Hold a curious head tilt while Myra is speaking into the mic."""
        self._stop_background()
        self._goto_target_safe(
            "listen pose",
            head=create_head_pose(roll=15, degrees=True),
            antennas=np.deg2rad([60, 60]),
            duration=0.8,
            method="minjerk",
        )

    # ── Speak: nodding head + alternating antenna wiggle during TTS ───────────

    def speak(self):
        """Start looping speak animation in the background."""
        self._start_background(self._speak_loop)

    def _speak_loop(self, token: int):
        while not self._background_should_stop(token):
            self._goto_target_safe(
                "speak nod up",
                preserve_timing=True,
                token=token,
                head=create_head_pose(z=6, mm=True),
                antennas=np.deg2rad([45, 20]),
                duration=0.3,
            )
            if self._background_should_stop(token):
                break
            self._goto_target_safe(
                "speak nod down",
                preserve_timing=True,
                token=token,
                head=create_head_pose(z=-2, mm=True),
                antennas=np.deg2rad([20, 45]),
                duration=0.3,
            )

    # ── Celebrate: enthusiastic head bobs + happy antenna wiggle ──────────────

    def celebrate(self):
        """Start 3-cycle celebration sequence in background (non-blocking).

        Returns immediately so audio can play in parallel.
        """
        self._start_background(self._celebrate_loop)

    def _celebrate_loop(self, token: int):
        for _ in range(3):
            if self._background_should_stop(token):
                return
            self._goto_target_safe(
                "celebrate bob up",
                preserve_timing=True,
                token=token,
                head=create_head_pose(z=15, mm=True),
                antennas=[0.8, -0.8],
                duration=0.3,
            )
            if self._background_should_stop(token):
                return
            self._goto_target_safe(
                "celebrate bob down",
                preserve_timing=True,
                token=token,
                head=create_head_pose(z=-5, mm=True),
                antennas=[-0.8, 0.8],
                duration=0.3,
            )
        if not self._background_should_stop(token):
            self.idle()

    # ── Wrong: head shake + drooping antennas ─────────────────────────────────

    def express_wrong(self):
        """Start 3-cycle yaw head-shake ("no") in background (non-blocking).

        Returns immediately so the uh-oh jingle can play in parallel.
        """
        self._start_background(self._express_wrong_loop)

    def _express_wrong_loop(self, token: int):
        # Droop antennas first so they're set before the shaking starts
        self._goto_target_safe(
            "wrong pose antennas droop",
            token=token,
            antennas=np.deg2rad([-30, -30]),
            duration=0.2,
        )
        for _ in range(3):
            if self._background_should_stop(token):
                return
            self._goto_target_safe(
                "wrong shake left",
                preserve_timing=True,
                token=token,
                head=create_head_pose(yaw=18, degrees=True),
                duration=0.22,
            )
            if self._background_should_stop(token):
                return
            self._goto_target_safe(
                "wrong shake right",
                preserve_timing=True,
                token=token,
                head=create_head_pose(yaw=-18, degrees=True),
                duration=0.22,
            )
        if not self._background_should_stop(token):
            self._goto_target_safe(
                "wrong reset pose",
                token=token,
                head=create_head_pose(yaw=0, degrees=True),
                antennas=np.deg2rad([0, 0]),
                duration=0.35,
            )
        if not self._background_should_stop(token):
            self.idle()

    # ── Synchronized audio playback ───────────────────────────────────────────

    def prime_speaker(self, warmup_duration: float = 0.25):
        """Send a short silent frame once so the first real line is not clipped."""
        if self._speaker_primed:
            return

        n_samples = max(1, int(round(self.output_sample_rate * warmup_duration)))
        silence = np.zeros((n_samples, 1), dtype=np.float32)
        logger.info(
            f"Speaker: priming output with {warmup_duration:.2f}s silence @ "
            f"{self.output_sample_rate} Hz"
        )
        self._mini.media.push_audio_sample(silence)
        time.sleep(warmup_duration + 0.05)
        self._speaker_primed = True

    def play_audio(self, samples: np.ndarray, suppress_speak_anim: bool = False):
        """Push audio to the speaker and block until done.

        Pass suppress_speak_anim=True when celebrate() or express_wrong() is
        already running in the background and should not be replaced by the
        speak loop — this lets animation and audio play simultaneously.
        """
        self.prime_speaker()
        duration = _audio_duration(samples, self.output_sample_rate)
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        logger.info(
            f"Speaker: pushing {duration:.2f}s audio @ {self.output_sample_rate} Hz, "
            f"shape={samples.shape}, RMS={rms:.4f}"
        )
        if not suppress_speak_anim:
            self.speak()
        self._mini.media.push_audio_sample(samples)
        logger.info(f"Speaker: push_audio_sample() returned, sleeping {duration + 0.15:.2f}s")
        time.sleep(duration + 0.15)  # small buffer for audio tail
        logger.info("Speaker: done")
        if not suppress_speak_anim:
            self._stop_background()

    def play_audio_streaming(self, samples: np.ndarray) -> None:
        """Push one streamed audio chunk without any per-chunk wait.

        Intended for the Realtime (Gemini/OpenAI) path. Unlike play_audio():
        no tail sleep (push_audio_sample is non-blocking on GStreamer —
        appsrc queues, sink clock paces), no speak() kick (bridge handles
        it once per turn), no _stop_background() (bridge manages animation
        lifecycle via speak/listen transitions). Priming stays idempotent.
        """
        self.prime_speaker()
        self._mini.media.push_audio_sample(samples)

    def flush_output_audio(self) -> None:
        """Drop any audio already queued in the speaker pipeline.

        Used by barge-in. Clearing the bridge's software deque is not
        enough: push_audio_sample() is non-blocking and the appsrc may
        already hold several seconds of audio that have been pushed but not
        yet played. Probe clear_player (GStreamer: pause → flush → play)
        first, falling back to the base clear_output_buffer if the backend
        exposes it under that name.
        """
        media = getattr(self._mini, "media", None)
        if media is None:
            return
        flush = (
            getattr(media, "clear_player", None)
            or getattr(getattr(media, "audio", None), "clear_player", None)
            or getattr(media, "clear_output_buffer", None)
        )
        if not callable(flush):
            logger.debug("flush_output_audio: no flush primitive available; skipping")
            return
        try:
            flush()
        except Exception as exc:
            logger.warning("flush_output_audio: flush raised: %s", exc)


# ── Celebration jingle ─────────────────────────────────────────────────────────

def _generate_celebration_jingle(sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Synthesize a short ascending arpeggio (C5–E5–G5–C6) using numpy sine waves.

    No external audio files required — pure numpy synthesis.
    Returns shape (N, 1) float32 at the given sample rate.
    """
    # Note frequencies (Hz) and durations (s)
    notes = [
        (523.25, 0.12),   # C5
        (659.25, 0.12),   # E5
        (783.99, 0.12),   # G5
        (1046.50, 0.50),  # C6 (held)
    ]
    segments = []
    for freq, dur in notes:
        n = int(sample_rate * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        # Fundamental + first harmonic for a warmer tone
        wave = 0.65 * np.sin(2 * np.pi * freq * t) + 0.20 * np.sin(2 * np.pi * freq * 2 * t)
        # Simple ADSR-lite envelope: 10 ms attack, 40 ms release
        env = np.ones(n, dtype=np.float32)
        attack = min(int(0.010 * sample_rate), n)
        release = min(int(0.040 * sample_rate), n)
        env[:attack] = np.linspace(0.0, 1.0, attack)
        env[n - release:] = np.linspace(1.0, 0.0, release)
        segments.append((wave * env).astype(np.float32))

    jingle = np.concatenate(segments)
    peak = float(np.max(np.abs(jingle)))
    if peak > 0:
        jingle *= 0.70 / peak
    return jingle.reshape(-1, 1)


def _generate_uhoh_jingle(sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Synthesize a descending two-note 'uh oh' stinger (G4 → Eb4).

    No external audio files required — pure numpy synthesis.
    Returns shape (N, 1) float32 at the given sample rate.
    """
    notes = [
        (392.00, 0.20),   # G4
        (311.13, 0.35),   # Eb4 (minor third down — classic wrong-answer interval)
    ]
    segments = []
    for freq, dur in notes:
        n = int(sample_rate * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        wave = 0.6 * np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2 * t)
        env = np.ones(n, dtype=np.float32)
        attack = min(int(0.008 * sample_rate), n)
        release = min(int(0.06 * sample_rate), n)
        env[:attack] = np.linspace(0.0, 1.0, attack)
        env[n - release:] = np.linspace(1.0, 0.0, release)
        segments.append((wave * env).astype(np.float32))
        # Short gap between notes
        segments.append(np.zeros(int(sample_rate * 0.04), dtype=np.float32))

    jingle = np.concatenate(segments)
    peak = float(np.max(np.abs(jingle)))
    if peak > 0:
        jingle *= 0.65 / peak
    return jingle.reshape(-1, 1)


# ── Lesson helpers ─────────────────────────────────────────────────────────────

def _play(robot: RobotController, mp3_bytes: bytes):
    """Decode MP3 bytes and play through the robot speaker with speak animation."""
    if not mp3_bytes:
        return
    samples = mp3_bytes_to_robot_samples(mp3_bytes, output_rate=robot.output_sample_rate)
    robot.play_audio(samples)


def _say(robot: RobotController, text: str, language: str = "english"):
    """Speak a voice line via /api/tts. Defaults to English; pass lesson language
    to keep a single consistent voice throughout a word lesson."""
    try:
        _play(robot, api_get_tts(text, language, slow=False))
    except Exception as e:
        logger.warning(f"Voice line failed '{text[:40]}': {e}")
        time.sleep(1.5)  # pause so the lesson flow still feels natural


def _fetch_tts_safe(text: str, language: str, slow: bool = False) -> bytes:
    """Fetch TTS audio bytes, returning b'' on any failure.

    Safe to call from background threads — never raises.
    """
    try:
        return api_get_tts(text, language, slow=slow)
    except Exception as e:
        logger.warning(f"TTS prefetch failed for '{text[:40]}': {e}")
        return b""


def _collect_prefetch(
    thread: threading.Thread | None,
    data: dict,
    out: dict | None,
    timeout: float = 5.0,
) -> None:
    """Join the next-word prefetch thread and write its result into *out*.

    Called at the end of run_lesson_word so the session loop can hand the
    pre-fetched word/TTS to the next call without an extra network round-trip.
    """
    if out is None:
        return
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    if data:
        out["next"] = data


def _interpret_play_again_transcript(transcribed: str) -> bool | None:
    """Map recognized speech to yes/no, returning None when unclear."""
    normalized = re.sub(r"[^a-z\s]", " ", (transcribed or "").strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None

    yes_tokens = {"yes", "yeah", "yep", "yup", "sure", "okay", "ok", "again", "more"}
    no_tokens = {"no", "nope", "nah", "done", "stop", "finished"}
    tokens = set(normalized.split())

    if "not" in tokens and "again" in tokens:
        return False
    if tokens & no_tokens:
        return False
    if tokens & yes_tokens:
        return True
    return None


def _capture_play_again_response(
    mini,
    mic_rate: int,
    duration: float = PLAY_AGAIN_RECORD_DURATION_SEC,
) -> str:
    """Capture a short spoken yes/no answer and return the recognized transcript."""
    mini.media.start_recording()
    time.sleep(0.3)
    _drain_input_audio_queue(mini, mic_rate, max_duration=1.0)

    chunks = []
    deadline = time.time() + max(float(duration), 0.0)
    while time.time() < deadline:
        sample = mini.media.get_audio_sample()
        if sample is not None:
            mono = _extract_first_channel(sample)
            if mono.size:
                chunks.append(mono)
        time.sleep(0.02)

    mini.media.stop_recording()

    if not chunks:
        logger.info("Play-again prompt heard no speech.")
        return ""

    raw = np.concatenate(chunks)
    wav_bytes = mic_samples_to_wav_bytes(raw, actual_rate=mic_rate)
    try:
        result = api_recognize(
            wav_bytes,
            language="english",
            expected_word="yes or no",
            romanized="yes or no",
            threshold=0,
        )
    except Exception as exc:
        logger.warning("Play-again recognition failed: %s", exc)
        return ""

    transcript = (result.get("transcribed") or "").strip()
    logger.info("Play-again transcript: %r", transcript)
    return transcript


def _prompt_play_again(
    mini,
    robot: RobotController,
    child_name: str,
    mic_rate: int,
    recognize_func=_capture_play_again_response,
    duration: float = PLAY_AGAIN_RECORD_DURATION_SEC,
) -> bool:
    """Ask whether to continue the lesson, using spoken mic recognition."""
    ask_line = random.choice([
        f"Play again, {child_name}? We can learn {REPLAY_WORD_BATCH} more words!",
        f"Do you want to keep going, {child_name}? I have {REPLAY_WORD_BATCH} more words ready!",
        f"Play again? If you want, we can do {REPLAY_WORD_BATCH} more words!",
    ])
    robot.idle()
    _say(robot, ask_line)

    print(f"Play again? Listening for {duration:.0f} seconds...")
    transcript = recognize_func(mini, mic_rate, duration)
    decision = _interpret_play_again_transcript(transcript)
    if decision is True:
        return True
    if decision is False:
        return False

    if transcript:
        logger.info("Treating unclear play-again response %r as 'no'.", transcript)
    else:
        logger.info("No play-again response received; ending session.")
    return False


# ── Single-word lesson cycle ───────────────────────────────────────────────────

def run_lesson_word(
    mini,
    robot: RobotController,
    languages: list,
    categories: list,
    threshold: int,
    max_attempts: int,
    child_name: str = "friend",
    mic_rate: int = SAMPLE_RATE,
    debug_audio: bool = False,
    prefetch: dict | None = None,
    prefetch_out: dict | None = None,
) -> str:
    """Run one complete word lesson. Returns 'correct' | 'revealed' | 'error'.

    prefetch:     optional {"word": <word_dict>, "tts_mp3": <bytes>} from a prior
                  recording-window prefetch — skips a network round-trip at word start.
    prefetch_out: mutable dict populated with {"next": {...}} before returning
                  so the session loop can pass it as prefetch to the next call.
    """

    # ── Fetch word (use prefetch if available) ─────────────────────────────────
    if prefetch and "word" in prefetch:
        word = prefetch["word"]
        tts_mp3_prefetched = prefetch.get("tts_mp3", b"")
        logger.info("Using prefetched word: %s", word.get("english"))
    else:
        try:
            word = api_get_word(languages, categories)
        except Exception as e:
            logger.error(f"Failed to fetch word: {e}")
            return "error"
        tts_mp3_prefetched = b""

    language = word["language"]
    translation = word["translation"]
    romanized = word["romanized"]
    english = word["english"].upper()
    emoji = word.get("emoji", "")

    print(f"\n{'─' * 44}")
    print(f"  {emoji}  {english}  →  {translation}  ({romanized})")
    print(f"  Language: {language}   Category: {word['category']}")
    print(f"{'─' * 44}")

    # ── Teach the word with meaning ────────────────────────────────────────────
    # Stop recording during teaching so the mic buffer doesn't fill with TTS audio.
    # Recording is restarted fresh right before each recognition attempt.
    mini.media.stop_recording()
    robot.idle()

    eng = word["english"].lower()
    lang_display = language.capitalize()

    # Build all 3 teaching phrases now so their TTS can be fetched in parallel.
    phrase1 = f"{child_name}, do you know what this word means?"
    phrase2 = f"{eng.capitalize()}! It means {eng} in {lang_display}."
    phrase3 = f"{child_name} repeat after me!"

    # Fetch word TTS + all 3 teaching phrase TTS in parallel (#2).
    # If word TTS was already prefetched, skip that fetch to save a thread slot.
    with ThreadPoolExecutor(max_workers=4) as pool:
        fut_word = (
            None if tts_mp3_prefetched
            else pool.submit(api_get_tts, translation, language, True)
        )
        fut_p1 = pool.submit(_fetch_tts_safe, phrase1, language)
        fut_p2 = pool.submit(_fetch_tts_safe, phrase2, language)
        fut_p3 = pool.submit(_fetch_tts_safe, phrase3, language)

        tts_mp3 = tts_mp3_prefetched
        if fut_word is not None:
            try:
                tts_mp3 = fut_word.result()
            except Exception as e:
                logger.warning(f"Word TTS failed: {e}")
                tts_mp3 = b""

        # _fetch_tts_safe never raises — returns b"" on failure
        phrase1_mp3 = fut_p1.result()
        phrase2_mp3 = fut_p2.result()
        phrase3_mp3 = fut_p3.result()

    def _play_phrase(mp3_bytes: bytes, fallback_text: str) -> None:
        """Play pre-fetched phrase MP3; fall back to live _say() on failure."""
        if mp3_bytes:
            try:
                _play(robot, mp3_bytes)
                return
            except Exception as e:
                logger.warning(f"Pre-fetched phrase playback failed: {e}")
        _say(robot, fallback_text, language)

    # 1. Curiosity hook — one voice throughout (lesson language for all speech)
    _play_phrase(phrase1_mp3, phrase1)
    if tts_mp3:
        try:
            _play(robot, tts_mp3)
            time.sleep(0.4)
        except Exception:
            pass

    # 2. Reveal the meaning, then play the native word again
    _play_phrase(phrase2_mp3, phrase2)
    if tts_mp3:
        try:
            _play(robot, tts_mp3)
            time.sleep(0.3)
        except Exception:
            pass

    # 3. Prompt the child to repeat
    _play_phrase(phrase3_mp3, phrase3)
    if tts_mp3:
        try:
            _play(robot, tts_mp3)
            time.sleep(0.3)
        except Exception:
            pass

    # ── Next-word prefetch — started once on attempt 1, during recording (#1, #5)
    # Fetches the next word + its TTS while Myra is recording (5 s dead time).
    # The result is collected at the end of this function and passed to the next
    # run_lesson_word call via prefetch_out, eliminating the word-start latency.
    _next_word_data: dict = {}
    _prefetch_thread: threading.Thread | None = None

    def _run_prefetch() -> None:
        try:
            nw = api_get_word(languages, categories)
            nt = api_get_tts(nw["translation"], nw["language"], slow=True)
            _next_word_data.update({"word": nw, "tts_mp3": nt})
            logger.info("Next-word prefetch ready: %s", nw.get("english"))
        except Exception as e:
            logger.debug(f"Next-word prefetch failed (non-fatal): {e}")

    # ── Attempt loop ──────────────────────────────────────────────────────────
    for attempt in range(1, max_attempts + 1):
        print(f"\n  🎤  Listening… (attempt {attempt}/{max_attempts})")
        robot.listen()

        # Restart recording with an empty buffer — avoids the long drain that
        # occurred when the mic accumulated TTS audio during teaching.
        mini.media.start_recording()
        time.sleep(0.3)  # let hardware initialize
        _drain_input_audio_queue(mini, mic_rate, max_duration=1.5)

        # Launch next-word prefetch once, on the first attempt, during recording.
        # Subsequent attempts reuse the same result (thread already done by then).
        if attempt == 1:
            _prefetch_thread = threading.Thread(
                target=_run_prefetch, daemon=True, name="next-word-prefetch"
            )
            _prefetch_thread.start()

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
            mini.media.stop_recording()
            return "error"

        # Stop recording immediately so subsequent TTS doesn't refill the buffer.
        mini.media.stop_recording()

        wav_bytes = mic_samples_to_wav_bytes(raw, actual_rate=actual_rate)

        if debug_audio:
            debug_path = "/tmp/myra_debug.wav"
            with open(debug_path, "wb") as f:
                f.write(wav_bytes)
            logger.info(f"Debug WAV saved → {debug_path}")
            _say(robot, f"{child_name}, I heard you say...", language)
            try:
                playback = wav_bytes_to_robot_samples(
                    wav_bytes,
                    output_rate=robot.output_sample_rate,
                )
                robot.play_audio(playback)
            except Exception as e:
                logger.warning(f"Debug playback failed: {e}")
            _say(robot, f"Let me check if that is correct, {child_name}!", language)

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
            praise_text = random.choice([
                f"Amazing, {child_name}! You got it!",
                f"Wonderful, {child_name}! You said it perfectly!",
                f"Great job {child_name}! You are amazing!",
                f"Yes, {child_name}! That is exactly right!",
            ])
            robot.celebrate()  # non-blocking — animation runs while audio plays

            # Fetch praise TTS in a thread while jingle generates locally (#3).
            # celebrate() animation runs in background throughout all of this.
            praise_mp3: bytes = b""
            _praise_result: list[bytes] = [b""]

            def _fetch_praise() -> None:
                _praise_result[0] = _fetch_tts_safe(praise_text, language)

            _praise_thread = threading.Thread(target=_fetch_praise, daemon=True)
            _praise_thread.start()
            jingle = _generate_celebration_jingle(robot.output_sample_rate)
            _praise_thread.join(timeout=15.0)
            praise_mp3 = _praise_result[0]

            # Mix jingle + praise voice into one buffer so they play simultaneously.
            try:
                praise_samples = (
                    mp3_bytes_to_robot_samples(praise_mp3, output_rate=robot.output_sample_rate)
                    if praise_mp3 else np.zeros((1, 1), dtype=np.float32)
                )
                j_len, p_len = len(jingle), len(praise_samples)
                mixed = np.zeros((max(j_len, p_len), 1), dtype=np.float32)
                mixed[:j_len] += jingle * 0.55       # jingle slightly ducked
                mixed[:p_len] += praise_samples       # voice at full level
                np.clip(mixed, -1.0, 1.0, out=mixed)
                robot.play_audio(mixed, suppress_speak_anim=True)
            except Exception as e:
                logger.warning(f"Celebration audio failed: {e}")
                _say(robot, praise_text, language)   # fallback: voice only

            _collect_prefetch(_prefetch_thread, _next_word_data, prefetch_out)
            return "correct"

        # Wrong answer — shake head + play "uh oh" jingle simultaneously
        robot.express_wrong()  # non-blocking — animation runs while jingle plays

        if attempt < max_attempts:
            retry = random.choice([
                f"Try again, {child_name}! Remember, it means {eng}. Say it!",
                f"Almost! It means {eng}. One more time, {child_name}!",
                f"You've got this, {child_name}! Say it!",
            ])
            # Fetch retry TTS in a thread while the uhoh jingle plays (#4).
            # By the time ~0.6 s of jingle finishes, TTS is ready to play.
            _retry_result: list[bytes] = [b""]

            def _fetch_retry() -> None:
                _retry_result[0] = _fetch_tts_safe(retry, language)

            _retry_thread = threading.Thread(target=_fetch_retry, daemon=True)
            _retry_thread.start()
            try:
                uhoh = _generate_uhoh_jingle(robot.output_sample_rate)
                robot.play_audio(uhoh, suppress_speak_anim=True)
            except Exception as e:
                logger.warning(f"Uh-oh jingle playback failed (non-fatal): {e}")
            _retry_thread.join(timeout=15.0)
            retry_mp3 = _retry_result[0]

            if retry_mp3:
                try:
                    _play(robot, retry_mp3)
                except Exception as e:
                    logger.warning(f"Retry TTS playback failed: {e}")
                    _say(robot, retry, language)
            else:
                _say(robot, retry, language)

            if tts_mp3:
                try:
                    _play(robot, tts_mp3)
                    time.sleep(0.3)
                except Exception:
                    pass
        else:
            # Last attempt — no retry needed, just play the jingle
            try:
                uhoh = _generate_uhoh_jingle(robot.output_sample_rate)
                robot.play_audio(uhoh, suppress_speak_anim=True)
            except Exception as e:
                logger.warning(f"Uh-oh jingle playback failed (non-fatal): {e}")

    # ── Out of attempts — reveal the word ─────────────────────────────────────
    print(f"\n  ℹ️  The word was: {translation} ({romanized})\n")
    robot.idle()
    _say(robot, f"The word was…", language)
    if tts_mp3:
        try:
            _play(robot, tts_mp3)
        except Exception:
            pass
    _say(robot, f"That's okay, {child_name}! Let's try the next one!", language)
    mini.media.start_recording()  # restore recording for the next word
    _collect_prefetch(_prefetch_thread, _next_word_data, prefetch_out)
    return "revealed"


# ── Session loop ───────────────────────────────────────────────────────────────

def run_lesson_session(
    languages: list,
    categories: list,
    num_words: int,
    threshold: int,
    max_attempts: int,
    child_name: str = "friend",
    debug_audio: bool = False,
    play_again_prompt_func=None,
):
    """Manage the ReachyMini context and run a full lesson session."""
    if not _ROBOT_SDK_AVAILABLE:
        raise SystemExit(
            "reachy-mini SDK not found. Install with:\n"
            "  pip install -r requirements-robot.txt\n"
            "Then re-run this script."
        )

    score = 0
    words_completed = 0
    session_target_words = num_words
    if play_again_prompt_func is None:
        play_again_prompt_func = _prompt_play_again

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

        # Pre-fetch greeting TTS before priming so audio plays immediately after
        # the warmup silence with no gap (prevents start-of-line clipping).
        greeting_text = f"Hi {child_name}! Let's learn some words today! Are you ready?"
        try:
            greeting_audio = api_get_dino_voice(greeting_text)
        except Exception as e:
            logger.warning(f"Greeting prefetch failed: {e}")
            greeting_audio = None

        print("\n" + "=" * 44)
        print(f"   🦕  {child_name}'s Language Lesson  🦕")
        print("=" * 44 + "\n")

        robot.idle()
        if greeting_audio:
            # Decode MP3 → samples, then prepend 400 ms of silence so the
            # hardware wakes up on silence rather than clipping the first word.
            greeting_samples = mp3_bytes_to_robot_samples(
                greeting_audio, output_rate=robot.output_sample_rate
            )
            n_pad = int(robot.output_sample_rate * 0.4)
            channels = greeting_samples.shape[1] if greeting_samples.ndim > 1 else 1
            pad = np.zeros((n_pad, channels), dtype=greeting_samples.dtype)
            robot.play_audio(np.concatenate([pad, greeting_samples]))
        else:
            time.sleep(1.5)

        # next_prefetch carries {"word": ..., "tts_mp3": ...} produced during
        # the recording window of the previous word, eliminating the word-fetch
        # and word-TTS network round-trips at the start of each lesson.
        next_prefetch: dict | None = None

        while words_completed < session_target_words:
            print(f"\n=== Word {words_completed + 1} of {session_target_words} ===")
            prefetch_out: dict = {}
            outcome = run_lesson_word(
                mini, robot, languages, categories, threshold, max_attempts,
                child_name=child_name, mic_rate=mic_rate, debug_audio=debug_audio,
                prefetch=next_prefetch,
                prefetch_out=prefetch_out,
            )
            next_prefetch = prefetch_out.get("next")
            words_completed += 1
            if outcome == "correct":
                score += 1
            elif outcome == "error":
                logger.warning("Skipping word due to error.")

            if words_completed < session_target_words:
                continue

            print(f"\n  Completed {words_completed} words.")
            if not play_again_prompt_func(mini, robot, child_name, mic_rate):
                break

            session_target_words += REPLAY_WORD_BATCH
            print(
                f"  ▶ Adding {REPLAY_WORD_BATCH} more words. "
                f"New session size: {session_target_words}"
            )
            _say(robot, f"Yay {child_name}! Let's learn {REPLAY_WORD_BATCH} more words!")

        # ── End of session ─────────────────────────────────────────────────────
        print("\n" + "=" * 44)
        print(f"  Session complete!  Score: {score} / {words_completed}")
        print("=" * 44 + "\n")

        robot.celebrate()
        end_line = random.choice([
            f"Great job {child_name}! We learned {words_completed} words today!",
            f"You got {score} out of {words_completed}! You are amazing, {child_name}!",
        ])
        _say(robot, end_line)

        robot.idle()
        time.sleep(1.0)

        mini.media.stop_recording()
        mini.media.stop_playing()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Reachy Mini × Myra Language Teacher",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--runtime-mode",
        default="cloud",
        metavar="MODE",
        help="cloud | reachy_local",
    )
    parser.add_argument(
        "--language",
        default="both",
        metavar="LANG",
        help="telugu | assamese | tamil | malayalam | both | all",
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
        "--server-url",
        default="",
        metavar="URL",
        help="Override server URL. Accepts LAN IPs (e.g. http://192.168.1.x:8765) "
             "or Tailscale IPs (e.g. http://100.x.x.x:8765). "
             "Skips local server startup. Use for Mac mini or MacBook over LAN or Tailscale.",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Only for reachy_local: use an already-running local Myra server instead of auto-starting one",
    )
    parser.add_argument(
        "--server-dir",
        default=DEFAULT_APP_DIR,
        metavar="PATH",
        help="Path to the myra-language-teacher directory on the Pi",
    )
    parser.add_argument(
        "--words-sync-to-gcs",
        default="never",
        metavar="POLICY",
        help="For reachy_local: never | session_end | shutdown",
    )
    parser.add_argument(
        "--gcs-bucket",
        default="myra-language-teacher-dynamic-words",
        metavar="BUCKET",
        help="GCS bucket for dynamic-words load and sync (reachy_local only)",
    )
    parser.add_argument(
        "--child-name",
        default="",
        metavar="NAME",
        help="Child's name used in voice prompts (prompted interactively if omitted)",
    )
    parser.add_argument(
        "--debug-audio",
        action="store_true",
        help="Play back each recording through the speaker before recognizing; save to /tmp/myra_debug.wav",
    )
    args = parser.parse_args()

    runtime_mode = args.runtime_mode.strip().lower()
    if runtime_mode not in RUNTIME_MODES:
        parser.error(
            f"Unknown --runtime-mode '{args.runtime_mode}'. "
            "Use cloud or reachy_local."
        )

    words_sync_to_gcs = args.words_sync_to_gcs.strip().lower()
    if words_sync_to_gcs not in {"never", "session_end", "shutdown"}:
        parser.error(
            f"Unknown --words-sync-to-gcs '{args.words_sync_to_gcs}'. "
            "Use never, session_end, or shutdown."
        )

    if runtime_mode == "cloud" and args.no_server:
        parser.error("--no-server is only valid with --runtime-mode reachy_local.")

    if runtime_mode == "cloud" and words_sync_to_gcs != "never":
        parser.error("--words-sync-to-gcs is only valid with --runtime-mode reachy_local.")

    # Resolve child's name
    child_name = args.child_name.strip()
    if not child_name:
        try:
            child_name = input("Child's name: ").strip()
        except (EOFError, KeyboardInterrupt):
            child_name = ""
    if not child_name:
        parser.error("Child's name is required. Use --child-name or enter it when prompted.")

    # Resolve languages
    if args.language == "both":
        languages = ["telugu", "assamese"]
    elif args.language == "all":
        languages = list(SUPPORTED_LESSON_LANGUAGES)
    elif args.language in SUPPORTED_LESSON_LANGUAGES:
        languages = [args.language]
    else:
        parser.error(
            f"Unknown --language '{args.language}'. "
            "Use telugu, assamese, tamil, malayalam, both, or all."
        )

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    if not categories:
        parser.error("--categories produced an empty list. Check the value.")

    configure_server_url(runtime_mode, args.server_url)

    # ── Server subprocess ──────────────────────────────────────────────────────
    server_proc = None
    if should_start_local_server(runtime_mode, args.no_server, args.server_url):
        server_proc = start_myra_server(
            app_dir=args.server_dir,
            words_sync_to_gcs=words_sync_to_gcs,
            gcs_bucket=args.gcs_bucket.strip(),
        )

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
    elif runtime_mode == "reachy_local" and args.no_server and words_sync_to_gcs == "shutdown":
        logger.warning(
            "Using --no-server with --words-sync-to-gcs=shutdown means sync depends on the existing server's shutdown config."
        )

    # ── Run the lesson ─────────────────────────────────────────────────────────
    try:
        run_lesson_session(
            languages=languages,
            categories=categories,
            num_words=args.words,
            threshold=args.threshold,
            max_attempts=args.max_attempts,
            child_name=child_name,
            debug_audio=args.debug_audio,
        )
    except KeyboardInterrupt:
        print("\nLesson stopped.")
    finally:
        if runtime_mode == "reachy_local" and words_sync_to_gcs == "session_end":
            try:
                synced = api_sync_words_to_gcs()
                if synced:
                    logger.info("Synced local custom words to GCS at session end.")
                else:
                    logger.info("Session-end word sync skipped (nothing new to upload or GCS not configured).")
            except Exception as exc:
                logger.warning("Session-end word sync failed: %s", exc)
        if server_proc and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()


if __name__ == "__main__":
    main()
