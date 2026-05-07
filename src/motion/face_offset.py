"""L3 face-tracking offset mixer.

Subscribes to :class:`face_tracker.FaceTracker` and turns its publishes into
smooth additive head offsets the composer can blend on top of the state pose.

The published target is a normalized ``(pan, tilt) ∈ [-1, 1]²`` pair where
``±1`` is the camera-frame edge. The mixer converts those normalized
fractions into actual head angles by multiplying by half the camera FOV, so
the head turns by the geometric angle the face is offset by:

    head_yaw   = pan  * (HFOV / 2) * gain
    head_pitch = tilt * (VFOV / 2) * gain

Defaults assume a Pi-Camera-v2 class lens (62° H × 49° V); both are
overridable via ``KIDS_TEACHER_CAMERA_HFOV_DEG`` and
``KIDS_TEACHER_CAMERA_VFOV_DEG`` env vars.

Behaviour summary:

* Smooth slew at ``max_angular_velocity_rad_s`` (default 30°/s) — no first-
  tick snap. The composer ticks at ~30 Hz; the slew limiter spreads any
  jump over multiple ticks.
* Three gain states tied to VAD / playback edges:

  ============  ======  ===========
  state          gain   target source
  ============  ======  ===========
  idle           0.7    latest tracker publish (or last good if None)
  child_speak    1.0    latest tracker publish (or last good if None)
  robot_speak    0.4    held — do **not** re-pick mid-utterance
  ============  ======  ===========

* When the tracker publishes ``None`` (no subject) the head **holds the last
  commanded offset** — it does not ease back to neutral. The intent is "the
  subject left frame; keep looking where they were" rather than a confused
  recentering. Hard reset to neutral happens only on session-level
  transitions handled by the bridge.
* Final magnitudes are clipped against safety caps (``max_pan_rad`` /
  ``max_tilt_rad``) so a runaway tracker can't whip the joints past their
  mechanical envelope.

The mixer never imports the FaceTracker class — :meth:`attach` accepts
anything with a Pollen-shaped ``subscribe(callback) -> unsubscribe`` API.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Any, Callable, Optional, Tuple

from motion.types import NEUTRAL, PoseOffset

logger = logging.getLogger(__name__)


_DEG = math.pi / 180.0


# Camera FOV defaults (Pi Camera v2 class). Override via env vars.
DEFAULT_HFOV_DEG = 62.0
DEFAULT_VFOV_DEG = 49.0
CAMERA_HFOV_ENV_VAR = "KIDS_TEACHER_CAMERA_HFOV_DEG"
CAMERA_VFOV_ENV_VAR = "KIDS_TEACHER_CAMERA_VFOV_DEG"


# Safety caps — final wall the FOV-scaled command is clipped against.
# Sized to the Reachy Mini head's mechanical envelope rather than to any
# expected target value, so a wide-FOV camera or a misconfigured FOV env
# var still can't drive the joints past their limit.
MAX_PAN_RAD = 35.0 * _DEG
MAX_TILT_RAD = 25.0 * _DEG
MAX_ANGULAR_VELOCITY_RAD_S = 30.0 * _DEG

# Default gain table.
DEFAULT_GAINS = {
    "idle": 0.7,
    "child_speaking": 1.0,
    "robot_speaking": 0.4,
}

# Mapping convention: pan > 0 is right-of-center → head_yaw > 0 (head turns
# right). tilt > 0 is below-center → head_pitch > 0 (head looks down).


GazeTarget = Optional[Tuple[float, float]]
_Subscribe = Callable[[Callable[[GazeTarget], None]], Callable[[], None]]


def _resolve_fov_deg(env_var: str, default: float) -> float:
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; falling back to %s", env_var, raw, default)
        return default
    if value <= 0.0:
        logger.warning("Non-positive %s=%r; falling back to %s", env_var, raw, default)
        return default
    return value


class FaceOffsetMixer:
    """Composer-facing additive offset source backed by the FaceTracker."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        gains: Optional[dict] = None,
        hfov_deg: Optional[float] = None,
        vfov_deg: Optional[float] = None,
        max_pan_rad: float = MAX_PAN_RAD,
        max_tilt_rad: float = MAX_TILT_RAD,
        max_angular_velocity_rad_s: float = MAX_ANGULAR_VELOCITY_RAD_S,
        logger_override: Optional[logging.Logger] = None,
    ) -> None:
        self._clock = clock
        self._gains = dict(gains) if gains else dict(DEFAULT_GAINS)
        hfov = hfov_deg if hfov_deg is not None else _resolve_fov_deg(
            CAMERA_HFOV_ENV_VAR, DEFAULT_HFOV_DEG
        )
        vfov = vfov_deg if vfov_deg is not None else _resolve_fov_deg(
            CAMERA_VFOV_ENV_VAR, DEFAULT_VFOV_DEG
        )
        self._half_hfov_rad = (hfov * _DEG) / 2.0
        self._half_vfov_rad = (vfov * _DEG) / 2.0
        self._max_pan = max_pan_rad
        self._max_tilt = max_tilt_rad
        self._max_velocity = max_angular_velocity_rad_s
        self._log = logger_override or logger

        self._lock = threading.Lock()
        self._gain_state: str = "idle"
        # Latest target from the tracker. ``None`` means "no subject right
        # now"; we hold the displayed offset in that case.
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
            # Update the held-target snapshot so a future transition into
            # robot_speaking locks onto the freshest known subject. While
            # already in robot_speaking we MUST NOT overwrite — the contract
            # is "do not re-pick mid-utterance". The lock-on snapshot taken
            # in set_gain_state covers entry to that state.
            if target is not None and self._gain_state != "robot_speaking":
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
            target_offset = self._target_offset_locked()
            displayed = self._slew_locked(target_offset, now)
            self._displayed_offset = displayed
            self._displayed_at = now
            return displayed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _target_offset_locked(self) -> PoseOffset:
        gain = self._gains.get(self._gain_state, 0.0)

        # Robot-speaking mode locks onto the snapshot taken when we entered
        # this state. We do NOT honor "tracker publishes None" here — holding
        # still is the desired behavior during a robot utterance.
        if self._gain_state == "robot_speaking":
            target = self._held_target
        else:
            target = self._tracker_target

        if target is None:
            # No subject — hold whatever we last commanded. The slew
            # limiter will see step==0 and the head stays put.
            return self._displayed_offset

        pan, tilt = target
        head_yaw = _clamp(pan * self._half_hfov_rad * gain, self._max_pan)
        head_pitch = _clamp(tilt * self._half_vfov_rad * gain, self._max_tilt)
        return PoseOffset(head_yaw=head_yaw, head_pitch=head_pitch)

    def _slew_locked(self, target: PoseOffset, now: float) -> PoseOffset:
        """Move the displayed offset toward ``target`` under the velocity cap.

        Always slews — there is no first-tick snap. With a NEUTRAL initial
        displayed offset, the first publish ramps in smoothly from zero.
        """
        last_at = self._displayed_at
        if last_at is None:
            # First tick: bootstrap the timestamp and start slewing from
            # whatever the displayed offset currently is (NEUTRAL by
            # default). No instantaneous jump.
            return self._displayed_offset
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
