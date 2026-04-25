"""Gaze-following tracker for kids-teacher mode (Chunk H of camera feature).

Keeps the robot's head pointed at the child it is teaching by observing the
shared :class:`CameraWorker` buffer. Detection is HOG-only via
:func:`face_service.detect_face_bboxes`; identity matching is delegated to
:func:`face_service.identify_in_frame` and runs only when the candidate set
changes (cache for ≤2 s) per FR-KID-27. See
``tasks/camera-object-recognition-design.md`` §2.7.

GAZE_TARGET FORMAT
==================
Published values: ``tuple[float, float] | None`` where the floats are
``(pan_offset, tilt_offset)`` in normalized ``[-1, 1]`` frame coordinates
(bbox center vs. frame center). Negative pan = left of center, positive
pan = right of center; negative tilt = above center, positive tilt = below
center.

A ``None`` value means "no subject — return to idle / neutral motion".

Subscribers register a callback via :meth:`FaceTracker.subscribe`. The
tracker calls each subscriber synchronously per tick from the gaze loop's
asyncio context. Subscribers MUST NOT block; if heavy work is needed,
dispatch to another asyncio task.

The motion director (``tasks/plan-motion-director.md``) is the planned
consumer but has not shipped. Until it does, a logging subscriber attached
by the session orchestrator keeps the channel observable. This module
never imports the motion director and never commands motors directly —
publishing tuples to subscribers is the only side-effect.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, List, Optional, Tuple

import face_service

logger = logging.getLogger(__name__)

GazeTarget = Optional[Tuple[float, float]]
Subscriber = Callable[[GazeTarget], None]

GAZE_HZ_ENV_VAR = "KIDS_TEACHER_GAZE_HZ"
GAZE_DEAD_ZONE_ENV_VAR = "KIDS_TEACHER_GAZE_DEAD_ZONE"
DEFAULT_GAZE_HZ = 3.0
DEFAULT_GAZE_DEAD_ZONE = 0.05
DEFAULT_HOLD_SECONDS = 1.0
# Cache the enrolled-child assignment for at most this long even when the
# bbox set is stable (FR-KID-27 step 1).
_IDENTIFY_CACHE_TTL = 2.0


def _resolve_float_env(env_var: str, default: float) -> float:
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; falling back to %s", env_var, raw, default)
        return default


class FaceTracker:
    """Periodic primary-subject picker that publishes ``(pan, tilt)`` targets.

    The tracker is a passive publisher: it owns no motors, no Reachy SDK
    handles, and no camera state beyond a reference to the shared
    ``CameraWorker``. Subscribers receive every non-suppressed tick; the
    dead-zone (FR-KID-28) and the post-loss hold (FR-KID-29) live inside
    the loop, not in subscribers.
    """

    def __init__(
        self,
        camera_worker,
        *,
        hz: Optional[float] = None,
        dead_zone: Optional[float] = None,
        hold_seconds: float = DEFAULT_HOLD_SECONDS,
        child_name: Optional[str] = None,
    ) -> None:
        self._camera_worker = camera_worker
        self._hz = hz if hz is not None else _resolve_float_env(
            GAZE_HZ_ENV_VAR, DEFAULT_GAZE_HZ
        )
        self._dead_zone = (
            dead_zone
            if dead_zone is not None
            else _resolve_float_env(GAZE_DEAD_ZONE_ENV_VAR, DEFAULT_GAZE_DEAD_ZONE)
        )
        self._hold_seconds = hold_seconds
        self._child_name = child_name
        self._subscribers: List[Subscriber] = []

        # State carried across ticks.
        self._last_target: GazeTarget = None
        self._last_seen_monotonic: Optional[float] = None
        self._cached_bboxes: Optional[List[Tuple[int, int, int, int]]] = None
        self._cached_child_idx: Optional[int] = None
        self._cached_at_monotonic: Optional[float] = None

        self._stopped = False

    # ------------------------------------------------------------------
    # Subscriber registration
    # ------------------------------------------------------------------
    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Register a subscriber; returns an unsubscribe handle."""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def _publish(self, target: GazeTarget) -> None:
        for subscriber in list(self._subscribers):
            try:
                subscriber(target)
            except Exception:
                logger.debug("[face_tracker] subscriber raised", exc_info=True)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def run(self, stop_event: asyncio.Event) -> None:
        """Tick at ``hz`` until ``stop_event`` or :meth:`stop` is signaled."""
        period = 1.0 / max(self._hz, 0.1)
        try:
            while not stop_event.is_set() and not self._stopped:
                try:
                    self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("[face_tracker] tick raised", exc_info=True)
                try:
                    await asyncio.sleep(period)
                except asyncio.CancelledError:
                    raise
        finally:
            # FR-KID-30: final publish of None so consumers can return to idle.
            self._publish(None)

    async def stop(self) -> None:
        """Signal the loop to exit and emit a final ``None`` target."""
        self._stopped = True

    # ------------------------------------------------------------------
    # Per-tick logic
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        frame = self._camera_worker.get_latest_frame()
        if frame is None:
            self._publish(None)
            self._last_target = None
            self._last_seen_monotonic = None
            return

        bboxes = face_service.detect_face_bboxes(frame, downscale=True)
        now = time.monotonic()

        if not bboxes:
            # FR-KID-29: hold the last target briefly before falling back to None.
            if (
                self._last_target is not None
                and self._last_seen_monotonic is not None
                and (now - self._last_seen_monotonic) < self._hold_seconds
            ):
                self._publish(self._last_target)
                return
            self._publish(None)
            self._last_target = None
            self._last_seen_monotonic = None
            return

        chosen = self._pick_subject(frame, bboxes, now)
        if chosen is None:
            # No subject selectable from a non-empty bbox set is unexpected,
            # but treat it the same as the empty-frame branch.
            self._publish(None)
            self._last_target = None
            self._last_seen_monotonic = None
            return

        target = self._normalized_target(frame.shape, chosen)
        self._last_target = target
        self._last_seen_monotonic = now

        # FR-KID-28: dead-zone — suppress publish on near-centered targets.
        pan, tilt = target
        if abs(pan) < self._dead_zone and abs(tilt) < self._dead_zone:
            return

        self._publish(target)

    def _pick_subject(
        self,
        frame,
        bboxes: List[Tuple[int, int, int, int]],
        now: float,
    ) -> Optional[Tuple[int, int, int, int]]:
        """Apply FR-KID-27 selection: enrolled child > largest > none."""
        if self._child_name and bboxes:
            child_idx = self._resolve_child_index(frame, bboxes, now)
            if child_idx is not None and 0 <= child_idx < len(bboxes):
                return bboxes[child_idx]

        # Fallback: largest bbox by area.
        return max(bboxes, key=_bbox_area, default=None)

    def _resolve_child_index(
        self,
        frame,
        bboxes: List[Tuple[int, int, int, int]],
        now: float,
    ) -> Optional[int]:
        """Run identification iff the bbox set changed (or cache expired)."""
        cache_valid = (
            self._cached_bboxes == bboxes
            and self._cached_at_monotonic is not None
            and (now - self._cached_at_monotonic) < _IDENTIFY_CACHE_TTL
        )
        if cache_valid:
            return self._cached_child_idx

        names = face_service.identify_in_frame(frame)
        child_idx: Optional[int] = None
        if self._child_name and self._child_name in names:
            # identify_in_frame returns deduped names, not per-bbox alignment.
            # Pick the largest bbox as the child's seat — it's the closest
            # face to the robot, which is the right "primary child" tiebreak
            # when only a name is known.
            child_idx = max(range(len(bboxes)), key=lambda i: _bbox_area(bboxes[i]))

        self._cached_bboxes = list(bboxes)
        self._cached_child_idx = child_idx
        self._cached_at_monotonic = now
        return child_idx

    def _normalized_target(
        self,
        frame_shape: Tuple[int, ...],
        bbox: Tuple[int, int, int, int],
    ) -> Tuple[float, float]:
        height, width = frame_shape[0], frame_shape[1]
        top, right, bottom, left = bbox
        cx = (left + right) / 2.0
        cy = (top + bottom) / 2.0
        # Normalize to [-1, 1] vs. frame center.
        pan = (cx - width / 2.0) / (width / 2.0) if width > 0 else 0.0
        tilt = (cy - height / 2.0) / (height / 2.0) if height > 0 else 0.0
        # Clamp defensively — a bbox outside the frame would otherwise yield
        # |x|>1 and confuse easing logic in any subscriber.
        pan = max(-1.0, min(1.0, pan))
        tilt = max(-1.0, min(1.0, tilt))
        return (pan, tilt)


def _bbox_area(bbox: Tuple[int, int, int, int]) -> int:
    top, right, bottom, left = bbox
    return max(0, right - left) * max(0, bottom - top)
