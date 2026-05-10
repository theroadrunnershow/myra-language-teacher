"""Reachy Mini audio + motion primitives for the kids-teacher flow.

Shared low-level helpers (sample-rate conversion, mono extraction, MP3 →
robot-sample decoding) plus the :class:`RobotController` that owns head
and antenna animation. The realtime kids-teacher bridge composes these
into its speak/listen/idle/celebrate state machine.
"""

from __future__ import annotations

import io
import logging
import threading
import time

import numpy as np
import scipy.signal
from pydub import AudioSegment

# Deferred robot SDK import — keeps unit tests runnable on hosts without
# the Reachy Mini hardware libraries installed.
try:
    from reachy_mini.utils import create_head_pose
except ImportError:
    def create_head_pose(**kwargs):
        return kwargs


logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # robot mic/speaker rate (fixed by SDK hardware)


# ── Audio helpers ──────────────────────────────────────────────────────────────

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


def mp3_bytes_to_robot_samples(mp3_bytes: bytes, output_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode MP3 bytes → numpy array shaped (N, 1) for push_audio_sample()."""
    seg = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    seg = seg.set_channels(1).set_frame_rate(output_rate)
    raw = np.array(seg.get_array_of_samples())
    samples = _to_float32_audio(raw)
    return samples.reshape(-1, 1)


def _audio_duration(samples: np.ndarray, sample_rate: int) -> float:
    return len(samples) / max(sample_rate, 1)


# ── Robot motion + playback controller ────────────────────────────────────────

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

    # ── Streaming pose tick (motion-director sink) ────────────────────────────

    def apply_pose(
        self,
        *,
        head_pitch: float = 0.0,
        head_yaw: float = 0.0,
        head_roll: float = 0.0,
        head_x: float = 0.0,
        head_y: float = 0.0,
        head_z: float = 0.0,
        antenna_left: float = 0.0,
        antenna_right: float = 0.0,
        duration: float = 0.05,
    ) -> bool:
        """Push a single pose frame to the SDK. Drops the frame if the
        motion lock is contended.

        Designed for the motion-director composer: called at ~30 Hz with
        durations matching the tick period. Bypasses the cooldown / retry
        logic in :meth:`_goto_target_safe` because at this cadence we'd
        rather drop a stale frame than queue or back off.

        All values are SI units (radians, metres).

        Returns True if the SDK call ran, False if the frame was dropped.
        """
        if not self._motion_lock.acquire(timeout=max(duration * 0.5, 0.005)):
            return False
        try:
            head = create_head_pose(
                pitch=head_pitch,
                yaw=head_yaw,
                roll=head_roll,
                x=head_x,
                y=head_y,
                z=head_z,
                degrees=False,
            )
            antennas = np.array([antenna_left, antenna_right], dtype=float)
            self._mini.goto_target(
                head=head,
                antennas=antennas,
                duration=max(duration, 1e-3),
                method="minjerk",
            )
            return True
        except Exception as exc:
            logger.debug("[RobotController] apply_pose dropped frame: %s", exc)
            return False
        finally:
            self._motion_lock.release()

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
        time.sleep(duration + 0.15)
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
        logger.info(
            "[diag] play_audio_streaming → push_audio_sample shape=%s dtype=%s",
            samples.shape,
            samples.dtype,
        )
        try:
            self._mini.media.push_audio_sample(samples)
        except Exception as exc:
            logger.warning("[diag] push_audio_sample raised: %s", exc)
            raise
        logger.info("[diag] play_audio_streaming → push_audio_sample returned")

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


# ── Free-function helpers ─────────────────────────────────────────────────────

def _play(robot: RobotController, mp3_bytes: bytes):
    """Decode MP3 bytes and play through the robot speaker with speak animation."""
    if not mp3_bytes:
        return
    samples = mp3_bytes_to_robot_samples(mp3_bytes, output_rate=robot.output_sample_rate)
    robot.play_audio(samples)
