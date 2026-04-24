"""Robot audio bridge for kids-teacher mode.

Wires the realtime handler's runtime hooks to a physical Reachy Mini robot:
assistant audio chunks stream through a background playback thread, and
session status events map to the robot's animation methods in
``robot_teacher.RobotController``.

The Reachy SDK is NOT imported at module load time — this module must import
cleanly in environments where the robot SDK is missing (test environment,
CI, cloud server). Callers that actually drive a robot are responsible for
constructing a real ``RobotController``; this module only talks to it through
its public animation API.

Microphone capture is NOT owned by :class:`KidsTeacherRobotHooks`. The
realtime handler pulls audio from the backend (OpenAI does server-side VAD),
so the robot flow runs a separate mic-pump task that reads robot mic frames
and forwards them to ``handler.push_audio(...)``. See
:func:`pump_microphone_to_backend`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any, Callable, Deque, Optional

from kids_teacher_types import (
    KidsStatusEvent,
    KidsTeacherRuntimeHooks,  # noqa: F401  (re-exported for type hints)
    KidsTranscriptEvent,
    SessionStatus,
    Speaker,
)

logger = logging.getLogger(__name__)


# Bound the queue so a stuck playback thread can't balloon memory. Eighty
# chunks at ~80ms each gives about 6.4s of buffered assistant audio before
# the hook starts dropping — plenty of slack for any well-behaved backend.
_DEFAULT_MAX_QUEUED_CHUNKS = 80

# How long the playback thread waits on a chunk before checking stop flags.
_PLAYBACK_POLL_SECONDS = 0.05


class KidsTeacherRobotHooks:
    """Implements :class:`KidsTeacherRuntimeHooks` for a Reachy Mini robot.

    Behavior summary:

    * ``start_assistant_playback`` appends a chunk to a bounded deque and
      kicks the speaking animation on the first chunk after idle/listen.
    * ``stop_assistant_playback`` clears the queue and returns to the
      listening animation for barge-in.
    * ``publish_status`` maps :class:`SessionStatus` transitions to robot
      animations via ``RobotController``.
    * ``publish_transcript`` logs speaker + text snippet; it does NOT speak
      the transcript (assistant audio is produced by the backend, not us).
    * ``persist_artifact`` is a no-op — the flow's review-store wrapper
      owns persistence.

    Playback is handled by a synchronous background thread (the robot SDK's
    audio playback API is synchronous, so asyncio doesn't fit). Call
    :meth:`start` before the first session event and :meth:`stop` once the
    session has ended.
    """

    def __init__(
        self,
        *,
        robot_controller: Any,
        sample_rate: int = 24000,
        logger_override: Optional[logging.Logger] = None,
        max_queued_chunks: int = _DEFAULT_MAX_QUEUED_CHUNKS,
        play_chunk: Optional[Callable[[Any, bytes, int], None]] = None,
    ) -> None:
        """Create a hook implementation bound to ``robot_controller``.

        Args:
            robot_controller: Instance of :class:`RobotController` (or a
                compatible fake in tests). Must expose ``listen``, ``speak``,
                and ``idle`` animation methods.
            sample_rate: Expected sample rate of incoming audio chunks.
                OpenAI Realtime streams PCM16 at 24kHz by default.
            logger_override: Optional logger — useful for tests.
            max_queued_chunks: Soft bound on the playback deque.
            play_chunk: Injectable playback callable invoked for each chunk
                on the background thread. Signature
                ``play_chunk(robot_controller, audio_bytes, sample_rate)``.
                Defaults to :func:`_default_play_chunk`, which decodes bytes
                with :mod:`robot_teacher` helpers and pushes to the robot
                speaker. Tests override this to avoid SDK imports.
        """
        self._robot = robot_controller
        self._sample_rate = sample_rate
        self._log = logger_override or logger
        self._play_chunk = play_chunk or _default_play_chunk

        # Playback queue + signaling primitives. deque + Event is lighter
        # than queue.Queue and gives us O(1) flush for barge-in.
        self._queue_lock = threading.Lock()
        self._queue: Deque[bytes] = deque(maxlen=max_queued_chunks)
        self._chunk_available = threading.Event()
        self._stop_flag = threading.Event()

        self._thread: Optional[threading.Thread] = None
        # Tracks whether the speaking animation has already been triggered
        # for the current assistant turn. Reset on stop_assistant_playback.
        self._speaking_active = False
        self._speaking_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spin up the background playback thread.

        Idempotent — calling twice is safe.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._chunk_available.clear()
        # Warm up the speaker now (idempotent) so the first delta is not
        # delayed by the ~0.3s priming sleep inside play_audio_streaming.
        prime = getattr(self._robot, "prime_speaker", None)
        if callable(prime):
            try:
                prime()
            except Exception as exc:
                self._log.warning(
                    "[kids_teacher_robot_bridge] prime_speaker raised: %s", exc
                )
        self._thread = threading.Thread(
            target=self._playback_loop,
            name="kids-teacher-robot-playback",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the playback thread to exit and wait briefly for it.

        Safe to call multiple times.
        """
        self._stop_flag.set()
        # Wake the loop so it notices the stop flag.
        self._chunk_available.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                self._log.warning(
                    "[kids_teacher_robot_bridge] playback thread did not exit in %.1fs",
                    timeout,
                )
        self._thread = None

    # ------------------------------------------------------------------
    # KidsTeacherRuntimeHooks protocol
    # ------------------------------------------------------------------

    def start_assistant_playback(self, audio_chunk: bytes) -> None:
        """Queue an assistant audio chunk and kick speaking animation once."""
        if not audio_chunk:
            return
        with self._queue_lock:
            self._queue.append(audio_chunk)
        self._chunk_available.set()

        # Only trigger the speak animation on the FIRST chunk of a turn.
        # Subsequent chunks within the same turn should not re-kick the
        # animation — otherwise every delta restarts the nod loop.
        with self._speaking_lock:
            if not self._speaking_active:
                self._speaking_active = True
                self._safe_call("speak")

    def stop_assistant_playback(self) -> None:
        """Flush queued audio and restore the listening animation.

        Called on barge-in. Must not block the caller. Clears both the
        bridge-level deque (chunks not yet handed to the sink) AND the
        speaker pipeline itself (chunks already pushed into the appsrc
        queue but not yet audible). Without the second step, the child
        would keep hearing the tail of the assistant response for several
        seconds after interrupting.
        """
        with self._queue_lock:
            self._queue.clear()
        with self._speaking_lock:
            self._speaking_active = False
        self._safe_call("flush_output_audio")
        self._safe_call("listen")

    def publish_transcript(self, event: KidsTranscriptEvent) -> None:
        """Log transcript events for robot-side observability only."""
        snippet = (event.text or "")[:80]
        partial_tag = "partial" if event.is_partial else "final"
        self._log.info(
            "[kids_teacher_robot_bridge] %s %s transcript: %r",
            event.speaker.value,
            partial_tag,
            snippet,
        )

    def publish_status(self, event: KidsStatusEvent) -> None:
        """Map session status transitions to robot animations."""
        status = event.status
        if status == SessionStatus.LISTENING:
            self._safe_call("listen")
        elif status == SessionStatus.SPEAKING:
            # Speaking is normally triggered by the first audio chunk.
            # Calling again here is a safe idempotent backstop.
            with self._speaking_lock:
                if not self._speaking_active:
                    self._speaking_active = True
                    self._safe_call("speak")
        elif status == SessionStatus.THINKING:
            # RobotController has no dedicated thinking pose; no-op keeps
            # the listen/speak animations intact during the pause.
            return
        elif status == SessionStatus.IDLE:
            self._safe_call("idle")
        elif status == SessionStatus.ENDED:
            self._log.info(
                "[kids_teacher_robot_bridge] session ended: %s",
                event.detail or "goodbye",
            )
            self._safe_call("idle")
        elif status == SessionStatus.ERROR:
            self._log.warning(
                "[kids_teacher_robot_bridge] session error: %s",
                event.detail or "unknown",
            )
            self._safe_call("idle")

    def persist_artifact(
        self,
        event: KidsTranscriptEvent,
        audio: Optional[bytes] = None,
    ) -> None:
        """No-op. The flow's review-store wrapper handles persistence."""
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _playback_loop(self) -> None:
        """Background thread: drain chunks and play them through the robot."""
        while not self._stop_flag.is_set():
            chunk = self._pop_chunk()
            if chunk is None:
                # No chunk available yet — wait to be woken by either a new
                # chunk landing in the queue or a stop request.
                self._chunk_available.wait(timeout=_PLAYBACK_POLL_SECONDS)
                self._chunk_available.clear()
                continue

            try:
                self._play_chunk(self._robot, chunk, self._sample_rate)
            except Exception as exc:
                # Never let a decode/playback failure kill the thread —
                # future chunks should still get a chance to play.
                self._log.warning(
                    "[kids_teacher_robot_bridge] chunk playback failed: %s", exc
                )

    def _pop_chunk(self) -> Optional[bytes]:
        with self._queue_lock:
            if not self._queue:
                return None
            return self._queue.popleft()

    def _safe_call(self, method_name: str) -> None:
        method = getattr(self._robot, method_name, None)
        if method is None:
            self._log.debug(
                "[kids_teacher_robot_bridge] robot controller missing %s(); skipping",
                method_name,
            )
            return
        try:
            method()
        except Exception as exc:
            self._log.warning(
                "[kids_teacher_robot_bridge] %s() raised: %s", method_name, exc
            )


def _default_play_chunk(robot_controller: Any, audio_bytes: bytes, sample_rate: int) -> None:
    """Default playback: decode assistant PCM16 audio and push it into the
    robot's streaming speaker path.

    Uses ``play_audio_streaming`` (not ``play_audio``) so back-to-back deltas
    concatenate without the per-chunk tail sleep that one-shot playback
    imposes. Import ``robot_teacher`` helpers lazily so this module stays
    importable on stripped-down test hosts (no ``pydub``/``ffmpeg``).
    """
    import numpy as np

    # Imported lazily: robot_teacher pulls numpy + scipy + pydub; tests that
    # don't touch playback should not pay that cost.
    from robot_teacher import _resample_audio, _to_float32_audio  # local import

    pcm16 = np.frombuffer(audio_bytes, dtype="<i2")
    samples = _to_float32_audio(pcm16)
    samples = _resample_audio(
        samples,
        sample_rate,
        getattr(robot_controller, "output_sample_rate", sample_rate),
    ).reshape(-1, 1)
    robot_controller.play_audio_streaming(samples)


# ----------------------------------------------------------------------
# Microphone pump
# ----------------------------------------------------------------------


async def pump_microphone_to_backend(
    handler: Any,
    *,
    mic_source: Any,
    chunk_duration_ms: int = 80,  # noqa: ARG001 — reserved for future pacing
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Background coroutine: forward mic chunks into the realtime handler.

    ``mic_source`` contract: each ``next(mic_source)`` (for generators) or
    each ``mic_source()`` call (for callables) must return **PCM16 mono
    bytes at the rate expected by the OpenAI Realtime API**. This function
    does NOT resample or re-encode; producing the right format is the
    caller's job (see ``robot_teacher.mic_samples_to_wav_bytes`` et al.
    for utilities).

    Return-value semantics:

    * non-empty ``bytes`` — forwarded to ``handler.push_audio``.
    * ``None`` — no frame available yet; pump yields briefly and retries.
      (Matches the robot ``mini.media.get_audio_sample()`` API, which
      returns ``None`` when its buffer is momentarily empty.)
    * ``b""`` — end of stream (loop exits).

    Stop conditions (any one ends the loop):

    * ``stop_event`` is set (coroutine-friendly shutdown signal).
    * ``mic_source`` raises :class:`StopIteration` (generator exhausted).
    * ``mic_source`` returns ``b""`` (empty bytes — end of stream).

    When the robot SDK is unavailable, the caller should not construct a
    real mic source — this function will simply exit immediately on the
    first empty/missing read.
    """
    read = _resolve_mic_reader(mic_source)
    chunk_duration_ms = max(1, int(chunk_duration_ms))

    while True:
        if stop_event is not None and stop_event.is_set():
            return

        try:
            chunk = read()
        except StopIteration:
            return
        except Exception as exc:
            logger.warning("[kids_teacher_robot_bridge] mic_source raised: %s", exc)
            return

        if chunk is None:
            # No frame ready yet — yield to the loop so the websocket reader
            # and keepalive can make progress, then poll again.
            await asyncio.sleep(0.01)
            continue

        if not chunk:
            # Empty bytes means "end of stream" per the docstring contract.
            return

        try:
            await handler.push_audio(chunk)
        except Exception as exc:
            logger.warning(
                "[kids_teacher_robot_bridge] handler.push_audio raised: %s", exc
            )
            return

        # Yield to the event loop so this coroutine plays nicely with the
        # realtime handler's own run() loop. No sleep — pacing is driven by
        # the mic_source itself (it should block until a chunk is ready).
        await asyncio.sleep(0)


def _resolve_mic_reader(mic_source: Any) -> Callable[[], bytes]:
    """Accept a callable, generator, or iterator and expose a uniform read().

    Generators/iterators: each call returns ``next(mic_source)``. When the
    iterator is exhausted, ``StopIteration`` propagates out of ``read()`` so
    the pump loop can exit cleanly.
    """
    if callable(mic_source):
        return mic_source  # type: ignore[return-value]

    iterator = iter(mic_source)

    def _read() -> bytes:
        return next(iterator)

    return _read
