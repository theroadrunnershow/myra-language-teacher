"""L3 face-tracking offset mixer.

Subscribes to :class:`face_tracker.FaceTracker` and turns its 3 Hz
``(pan, tilt) ∈ [-1, 1]²`` publishes into smooth additive head offsets the
composer can blend on top of the state pose. Per
``tasks/plan-motion-director.md`` §6.3:

* 3 Hz publish → ~60 Hz consume → ease toward target rather than snap.
* Hard caps: ±20° pan, ±15° tilt, ~60°/s angular velocity.
* Three gain states tied to VAD / playback edges:

  ============  ======  ===========
  state          gain   target source
  ============  ======  ===========
  idle           0.7    latest tracker publish (or last good if None)
  child_speak    1.0    latest tracker publish (or last good if None)
  robot_speak    0.4    held — do **not** re-pick mid-utterance
  ============  ======  ===========

* When the tracker publishes ``None`` (no subject), the mixer eases the
  offset back to zero over ``no_target_release_s``. Hardware shutdown
  surfaces as the same path because :meth:`FaceTracker.run` publishes
  ``None`` on stop.

The mixer never imports the FaceTracker class — :meth:`attach` accepts
anything with a Pollen-shaped ``subscribe(callback) -> unsubscribe`` API.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable, Optional, Tuple

from motion.types import NEUTRAL, PoseOffset

logger = logging.getLogger(__name__)


_DEG = math.pi / 180.0


# Hard caps from plan §6.3.
MAX_PAN_RAD = 20.0 * _DEG
MAX_TILT_RAD = 15.0 * _DEG
MAX_ANGULAR_VELOCITY_RAD_S = 60.0 * _DEG

# Default gain table.
DEFAULT_GAINS = {
    "idle": 0.7,
    "child_speaking": 1.0,
    "robot_speaking": 0.4,
}

# How long to ease the offset back to neutral after the tracker publishes
# ``None`` (no subject). Matches the plan's "ease back to zero" behavior on
# tracker shutdown / lost subject.
DEFAULT_NO_TARGET_RELEASE_S = 0.6

# Mapping convention: pan > 0 is right-of-center → head_yaw > 0 (head turns
# right). tilt > 0 is below-center → head_pitch > 0 (head looks down).


GazeTarget = Optional[Tuple[float, float]]
_Subscribe = Callable[[Callable[[GazeTarget], None]], Callable[[], None]]


class FaceOffsetMixer:
    """Composer-facing additive offset source backed by the FaceTracker."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        gains: Optional[dict] = None,
        max_pan_rad: float = MAX_PAN_RAD,
        max_tilt_rad: float = MAX_TILT_RAD,
        max_angular_velocity_rad_s: float = MAX_ANGULAR_VELOCITY_RAD_S,
        no_target_release_s: float = DEFAULT_NO_TARGET_RELEASE_S,
        logger_override: Optional[logging.Logger] = None,
    ) -> None:
        self._clock = clock
        self._gains = dict(gains) if gains else dict(DEFAULT_GAINS)
        self._max_pan = max_pan_rad
        self._max_tilt = max_tilt_rad
        self._max_velocity = max_angular_velocity_rad_s
        self._no_target_release_s = no_target_release_s
        self._log = logger_override or logger

        self._lock = threading.Lock()
        self._gain_state: str = "idle"
        # Latest target from the tracker. ``None`` means "no subject right
        # now"; we blend toward zero in that case.
        self._tracker_target: GazeTarget = None
        self._tracker_target_at: Optional[float] = None
        # Cached last good target — used during robot_speaking to lock onto
        # whoever we were looking at when the assistant started talking.
        self._held_target: GazeTarget = None
        # Current displayed offset (post-smoothing). Drives the velocity
        # cap and is what the composer reads each tick.
        self._displayed_offset: PoseOffset = NEUTRAL
        self._displayed_at: Optional[float] = None

        self._unsubscribe: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------
    # Tracker integration
    # ------------------------------------------------------------------

    def attach(self, tracker: Any) -> None:
        """Subscribe to ``tracker.subscribe(...)``. Idempotent.

        Caller is responsible for the tracker's own lifecycle (start/stop).
        """
        if self._unsubscribe is not None:
            return
        try:
            self._unsubscribe = tracker.subscribe(self._on_target)
        except AttributeError as exc:
            raise TypeError(
                "FaceOffsetMixer.attach: tracker has no subscribe(callback) API"
            ) from exc

    def detach(self) -> None:
        """Unsubscribe from the tracker. Idempotent."""
        unsub = self._unsubscribe
        self._unsubscribe = None
        if unsub is None:
            return
        try:
            unsub()
        except Exception as exc:
            self._log.debug("[motion.face_offset] unsubscribe raised: %s", exc)

    def _on_target(self, target: GazeTarget) -> None:
        """Subscriber callback — runs from the FaceTracker's loop."""
        now = self._clock()
        with self._lock:
            self._tracker_target = target
            self._tracker_target_at = now
            if target is not None:
                # Update the held-target snapshot so a transition into
                # robot_speaking locks onto the freshest known subject.
                self._held_target = target

    # ------------------------------------------------------------------
    # Gain state
    # ------------------------------------------------------------------

    def set_gain_state(self, state: str) -> None:
        """Switch gain. Snapshots the held target on robot_speaking entry."""
        with self._lock:
            if state not in self._gains:
                self._log.warning(
                    "[motion.face_offset] unknown gain state %r; ignoring",
                    state,
                )
                return
            if state == "robot_speaking" and self._tracker_target is not None:
                # Lock onto the freshest known target so a stale held value
                # doesn't make us look at the wrong place.
                self._held_target = self._tracker_target
            self._gain_state = state

    @property
    def gain_state(self) -> str:
        return self._gain_state

    # ------------------------------------------------------------------
    # Composer-facing source
    # ------------------------------------------------------------------

    def current_offset(self) -> PoseOffset:
        """Return the current additive head offset.

        Smooths from the previously-displayed offset toward the target at
        ``max_angular_velocity_rad_s``. Composer queries this on every tick.
        """
        now = self._clock()
        with self._lock:
            target_offset = self._target_offset_locked(now)
            displayed = self._slew_locked(target_offset, now)
            self._displayed_offset = displayed
            self._displayed_at = now
            return displayed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _target_offset_locked(self, now: float) -> PoseOffset:
        gain = self._gains.get(self._gain_state, 0.0)

        # Robot-speaking mode locks onto the snapshot taken when we entered
        # this state. We do NOT honor "tracker publishes None" here —
        # holding still is the desired behavior during a robot utterance.
        if self._gain_state == "robot_speaking":
            target = self._held_target
        else:
            target = self._tracker_target
            # No subject right now: ease toward zero. We let the slew limiter
            # do that for us by returning NEUTRAL as the target. After
            # ``no_target_release_s`` we're effectively at zero anyway.
            if target is None:
                _ = self._no_target_release_s  # documented; slew handles it
                return NEUTRAL

        if target is None:
            return NEUTRAL

        pan, tilt = target
        head_yaw = _clamp(pan * self._max_pan, self._max_pan) * gain
        head_pitch = _clamp(tilt * self._max_tilt, self._max_tilt) * gain
        return PoseOffset(head_yaw=head_yaw, head_pitch=head_pitch)

    def _slew_locked(self, target: PoseOffset, now: float) -> PoseOffset:
        """Move the displayed offset toward ``target`` under the velocity cap."""
        last_at = self._displayed_at
        if last_at is None:
            # First tick — snap so we start tracking immediately.
            return target
        dt = max(now - last_at, 0.0)
        if dt <= 0.0:
            return self._displayed_offset
        max_step = self._max_velocity * dt
        return PoseOffset(
            head_yaw=_step_toward(self._displayed_offset.head_yaw, target.head_yaw, max_step),
            head_pitch=_step_toward(
                self._displayed_offset.head_pitch, target.head_pitch, max_step
            ),
        )


def _clamp(value: float, cap: float) -> float:
    if value > cap:
        return cap
    if value < -cap:
        return -cap
    return value


def _step_toward(current: float, target: float, max_step: float) -> float:
    delta = target - current
    if abs(delta) <= max_step:
        return target
    return current + math.copysign(max_step, delta)
