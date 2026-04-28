"""L2 gesture scheduler — priority, debounce, rate cap, barge-in.

Sits between the LLM (or any other gesture source) and the
:class:`MovementComposer`. Enforces the rules from
``tasks/plan-motion-director.md`` §7:

* **Priority lanes** — ``safety`` > ``system`` > ``celebration`` > ``affect``
  > ``idle_filler``. Higher-lane requests pre-empt lower-lane in-flight
  clips; equal-or-lower-lane requests drop while a clip is playing.
* **Debounce** — the same gesture can't repeat within
  :data:`DEFAULT_PER_NAME_COOLDOWN_S`.
* **Rate cap** — L2 fires at most once per
  :data:`DEFAULT_GLOBAL_COOLDOWN_S`.
* **Barge-in** — :meth:`flush` cancels the in-flight clip without
  trampling debounce timers.

Every decision is reported through an optional callback so the bridge can
log it to ``kids_review_store`` or wherever telemetry lands.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from motion.composer import MovementComposer
from motion.library import ChoreographyLibrary, Clip

logger = logging.getLogger(__name__)


DEFAULT_PER_NAME_COOLDOWN_S = 8.0
DEFAULT_GLOBAL_COOLDOWN_S = 4.0


# Higher number = higher priority. The "celebration" lane preempts "affect"
# (matching plan §7: "Long clips (`dance`) get their own lane and pre-empt
# shorter affect clips.").
LANE_PRIORITY: Dict[str, int] = {
    "safety": 5,
    "system": 4,
    "celebration": 3,
    "affect": 2,
    "idle_filler": 1,
}


@dataclass(frozen=True)
class GestureDecision:
    """Outcome of a single :meth:`GestureScheduler.request` call.

    ``accepted`` is ``True`` when the clip was started (possibly preempting
    a lower-lane clip). When ``False``, ``reason`` names the rule that
    dropped it: ``unknown_gesture``, ``per_name_cooldown``,
    ``global_cooldown``, or ``lower_priority``.
    """

    name: str
    accepted: bool
    reason: Optional[str]
    lane: Optional[str]


DecisionLogger = Callable[[GestureDecision], None]


class GestureScheduler:
    """Priority + cooldown gate in front of :class:`MovementComposer`."""

    def __init__(
        self,
        *,
        composer: MovementComposer,
        library: ChoreographyLibrary,
        clock: Callable[[], float] = time.monotonic,
        per_name_cooldown_s: float = DEFAULT_PER_NAME_COOLDOWN_S,
        global_cooldown_s: float = DEFAULT_GLOBAL_COOLDOWN_S,
        decision_logger: Optional[DecisionLogger] = None,
        logger_override: Optional[logging.Logger] = None,
    ) -> None:
        self._composer = composer
        self._library = library
        self._clock = clock
        self._per_name_cooldown_s = per_name_cooldown_s
        self._global_cooldown_s = global_cooldown_s
        self._decision_logger = decision_logger
        self._log = logger_override or logger

        self._lock = threading.Lock()
        self._last_fire_at: Dict[str, float] = {}
        self._global_last_fire_at: Optional[float] = None
        self._active_clip: Optional[Clip] = None
        self._active_started_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def request(self, name: str) -> GestureDecision:
        """Try to play the named gesture. Returns the decision + reason."""
        clip = self._library.get(name)
        if clip is None:
            decision = GestureDecision(
                name=name, accepted=False, reason="unknown_gesture", lane=None
            )
            self._emit(decision)
            return decision

        now = self._clock()
        with self._lock:
            decision = self._evaluate_locked(clip, now)
            if decision.accepted:
                self._active_clip = clip
                self._active_started_at = now
                self._last_fire_at[name] = now
                self._global_last_fire_at = now

        if decision.accepted:
            # Composer call goes outside the scheduler lock — it takes its
            # own lock and we don't want to block other request() callers.
            self._composer.play_clip(clip)
        self._emit(decision)
        return decision

    def flush(self) -> None:
        """Cancel the active clip without resetting cooldown timers.

        Called on barge-in (child speaks while a gesture is mid-flight)
        per plan §7 rule 4. Debounce + rate-cap state is preserved so the
        scheduler doesn't immediately re-fire a duplicate gesture.
        """
        with self._lock:
            self._active_clip = None
            self._active_started_at = None
        self._composer.cancel_clip()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _evaluate_locked(self, clip: Clip, now: float) -> GestureDecision:
        # Per-name cooldown — same gesture can't repeat within window.
        last_for_name = self._last_fire_at.get(clip.name)
        if (
            last_for_name is not None
            and (now - last_for_name) < self._per_name_cooldown_s
        ):
            return GestureDecision(
                name=clip.name,
                accepted=False,
                reason="per_name_cooldown",
                lane=clip.lane,
            )

        # Global rate cap — at most one L2 fire per window. System-lane
        # gestures (e.g. ``cancel``) bypass the rate cap so the scheduler
        # can always force a stop without waiting out the cooldown.
        is_system_or_safety = LANE_PRIORITY.get(clip.lane, 0) >= LANE_PRIORITY["system"]
        if (
            not is_system_or_safety
            and self._global_last_fire_at is not None
            and (now - self._global_last_fire_at) < self._global_cooldown_s
        ):
            return GestureDecision(
                name=clip.name,
                accepted=False,
                reason="global_cooldown",
                lane=clip.lane,
            )

        # Lane priority — only preempt lower-lane in-flight clips.
        if self._active_clip is not None and self._active_started_at is not None:
            elapsed = now - self._active_started_at
            still_playing = elapsed < self._active_clip.duration
            if still_playing:
                requested_priority = LANE_PRIORITY.get(clip.lane, 0)
                active_priority = LANE_PRIORITY.get(self._active_clip.lane, 0)
                if requested_priority <= active_priority:
                    return GestureDecision(
                        name=clip.name,
                        accepted=False,
                        reason="lower_priority",
                        lane=clip.lane,
                    )
            else:
                # Clip already ran out — clear stale state.
                self._active_clip = None
                self._active_started_at = None

        return GestureDecision(
            name=clip.name, accepted=True, reason=None, lane=clip.lane
        )

    def _emit(self, decision: GestureDecision) -> None:
        if decision.accepted:
            self._log.info(
                "[motion.scheduler] play %r (lane=%s)",
                decision.name,
                decision.lane,
            )
        else:
            self._log.debug(
                "[motion.scheduler] drop %r (lane=%s reason=%s)",
                decision.name,
                decision.lane,
                decision.reason,
            )
        cb = self._decision_logger
        if cb is None:
            return
        try:
            cb(decision)
        except Exception as exc:
            self._log.debug("[motion.scheduler] decision_logger raised: %s", exc)
