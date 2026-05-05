"""60 Hz-class additive layer composer for the motion director.

Per ``tasks/plan-motion-director.md`` §4 the composer mixes:

    pose = state pose (idle/listen/speak)
         + primary clip (L2, sparse)
         + L1 audio wobble (optional)
         + L3 face offset (optional)

The result is clipped against a safety cap and handed to a sink callable.
The sink owns the SDK conversion (``create_head_pose`` etc.) so this module
stays free of any robot SDK import.

Threading model
---------------
The composer runs a single daemon thread that calls :meth:`tick` at
``tick_hz``. All public methods are safe to call from any thread; mutating
state takes a short lock. :meth:`tick` also runs under the lock so a
mid-tick ``play_clip`` can't tear the active clip mid-evaluation.

For tests, drive the composer manually via :meth:`tick_at` or
:meth:`compose_pose` and skip :meth:`start` / :meth:`stop`.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, Dict, Optional

from motion.library import Clip
from motion.types import NEUTRAL, PoseOffset

logger = logging.getLogger(__name__)

PoseSink = Callable[[PoseOffset, float], None]
PoseSource = Callable[[], PoseOffset]


_DEG = math.pi / 180.0


# State -> pose mapping. ``idle`` is neutral so the wobble / face layers
# define the only motion at rest; ``listen`` is neutral on Reachy Mini
# until we re-tune (the inherited 15° head_roll from RobotController.listen()
# was effectively dormant under the apply_pose degree/radian bug fixed in
# b193519 and felt excessive once it actually reached the joint); ``speak``
# is neutral because L1 wobble drives the head-bob equivalent.
DEFAULT_STATE_POSES: Dict[str, PoseOffset] = {
    "idle": NEUTRAL,
    "listen": NEUTRAL,
    "speak": NEUTRAL,
}


# Final output safety caps. Generous enough to accommodate the listen-state
# 15° roll plus a clip envelope; tight enough that a runaway source can't
# whip the head. Tune in Phase 1+ once we have hardware in the loop.
DEFAULT_SAFETY_CAPS = PoseOffset(
    head_pitch=25.0 * _DEG,
    head_yaw=25.0 * _DEG,
    head_roll=25.0 * _DEG,
    head_x=0.020,  # 20 mm
    head_y=0.020,
    head_z=0.020,
    antenna_left=70.0 * _DEG,
    antenna_right=70.0 * _DEG,
)


class MovementComposer:
    """Per-tick layer mixer feeding a single pose sink.

    See module docstring for the composition rule. The composer is created
    in a stopped state with no active clip and no L1/L3 sources; the bridge
    wires sources in as each layer comes online.
    """

    def __init__(
        self,
        *,
        pose_sink: PoseSink,
        tick_hz: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        state_poses: Optional[Dict[str, PoseOffset]] = None,
        safety_caps: PoseOffset = DEFAULT_SAFETY_CAPS,
        logger_override: Optional[logging.Logger] = None,
    ) -> None:
        if tick_hz <= 0.0:
            raise ValueError(f"tick_hz must be positive, got {tick_hz!r}")
        self._sink = pose_sink
        self._tick_hz = float(tick_hz)
        self._tick_period = 1.0 / self._tick_hz
        self._clock = clock
        self._state_poses = dict(state_poses) if state_poses else dict(DEFAULT_STATE_POSES)
        self._caps = safety_caps
        self._log = logger_override or logger

        self._lock = threading.Lock()
        self._state: str = "idle"
        self._active_clip: Optional[Clip] = None
        self._clip_started_at: Optional[float] = None
        self._wobble_source: Optional[PoseSource] = None
        self._face_offset_source: Optional[PoseSource] = None

        self._stop_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Configuration (thread-safe)
    # ------------------------------------------------------------------

    def set_state(self, state: str) -> None:
        """Switch the base state pose. Unknown states fall back to ``idle``."""
        with self._lock:
            if state not in self._state_poses:
                self._log.warning(
                    "[motion.composer] unknown state %r; falling back to idle", state
                )
                self._state = "idle"
            else:
                self._state = state

    def play_clip(self, clip: Clip) -> None:
        """Start a clip on the L2 lane. Replaces any in-flight clip."""
        with self._lock:
            self._active_clip = clip
            self._clip_started_at = self._clock()

    def cancel_clip(self) -> None:
        """Drop the active L2 clip. The L1/L3 layers continue."""
        with self._lock:
            self._active_clip = None
            self._clip_started_at = None

    def set_wobble_source(self, source: Optional[PoseSource]) -> None:
        with self._lock:
            self._wobble_source = source

    def set_face_offset_source(self, source: Optional[PoseSource]) -> None:
        with self._lock:
            self._face_offset_source = source

    # ------------------------------------------------------------------
    # Tick / compose
    # ------------------------------------------------------------------

    def compose_pose(self, now: Optional[float] = None) -> PoseOffset:
        """Return the composed pose for ``now`` without invoking the sink.

        Pure with respect to the composer's current configuration — used by
        tests to assert layer math directly.
        """
        if now is None:
            now = self._clock()
        with self._lock:
            base = self._state_poses.get(self._state, NEUTRAL)
            clip_offset = self._eval_clip_locked(now)
            wobble = _safe_call(self._wobble_source, self._log, "wobble_source")
            face = _safe_call(self._face_offset_source, self._log, "face_offset_source")
        return (base + clip_offset + wobble + face).clipped(self._caps)

    def tick(self) -> PoseOffset:
        """Compose the current pose and hand it to the sink.

        Returns the composed offset for caller introspection / telemetry.
        """
        now = self._clock()
        offset = self.compose_pose(now)
        try:
            self._sink(offset, self._tick_period)
        except Exception as exc:
            self._log.warning("[motion.composer] pose_sink raised: %s", exc)
        return offset

    def tick_at(self, now: float) -> PoseOffset:
        """Test helper: tick using an explicit clock value."""
        offset = self.compose_pose(now)
        try:
            self._sink(offset, self._tick_period)
        except Exception as exc:
            self._log.warning("[motion.composer] pose_sink raised: %s", exc)
        return offset

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the daemon ticking thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="motion-composer",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        """Signal the loop to exit and wait briefly. Idempotent."""
        self._stop_flag.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                self._log.warning(
                    "[motion.composer] composer thread did not exit in %.1fs",
                    timeout,
                )
        self._thread = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        next_tick = self._clock()
        while not self._stop_flag.is_set():
            self.tick()
            next_tick += self._tick_period
            sleep_for = next_tick - self._clock()
            if sleep_for > 0:
                # Wait on the stop flag so shutdown doesn't have to ride out
                # a full tick period.
                self._stop_flag.wait(timeout=sleep_for)
            else:
                # We fell behind — rebase so we don't burn CPU catching up.
                next_tick = self._clock()

    def _eval_clip_locked(self, now: float) -> PoseOffset:
        clip = self._active_clip
        started_at = self._clip_started_at
        if clip is None or started_at is None:
            return NEUTRAL
        elapsed = now - started_at
        if elapsed < 0.0 or elapsed >= clip.duration:
            self._active_clip = None
            self._clip_started_at = None
            return NEUTRAL
        try:
            return clip.pose_at(elapsed)
        except Exception as exc:
            self._log.warning(
                "[motion.composer] clip %r pose_at(%.3f) raised: %s — dropping",
                clip.name,
                elapsed,
                exc,
            )
            self._active_clip = None
            self._clip_started_at = None
            return NEUTRAL


def _safe_call(
    source: Optional[PoseSource], log: logging.Logger, label: str
) -> PoseOffset:
    if source is None:
        return NEUTRAL
    try:
        offset = source()
    except Exception as exc:
        log.debug("[motion.composer] %s raised: %s", label, exc)
        return NEUTRAL
    if offset is None:
        return NEUTRAL
    return offset
