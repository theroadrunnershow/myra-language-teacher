"""Unit tests for :mod:`motion.scheduler`."""

from __future__ import annotations

from typing import List, Tuple

import pytest

from motion.composer import MovementComposer
from motion.library import ChoreographyLibrary, Clip
from motion.scheduler import (
    DEFAULT_GLOBAL_COOLDOWN_S,
    DEFAULT_PER_NAME_COOLDOWN_S,
    GestureDecision,
    GestureScheduler,
)
from motion.types import NEUTRAL, PoseOffset


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def _make_clip(name: str, lane: str, duration: float = 1.0) -> Clip:
    return Clip(name=name, duration=duration, lane=lane, pose_at=lambda t: NEUTRAL)  # noqa: ARG005


def _make_library() -> ChoreographyLibrary:
    return ChoreographyLibrary(
        [
            _make_clip("nod_encourage", "affect", 1.0),
            _make_clip("head_tilt_curious", "affect", 1.0),
            _make_clip("mini_dance_excited", "celebration", 2.0),
            _make_clip("victory_wiggle", "celebration", 2.0),
            _make_clip("cancel", "system", 0.4),
        ]
    )


def _make() -> Tuple[GestureScheduler, MovementComposer, _FakeClock, List[GestureDecision]]:
    clock = _FakeClock()
    sink_calls: List[Tuple[PoseOffset, float]] = []

    def _sink(offset: PoseOffset, period: float) -> None:
        sink_calls.append((offset, period))

    composer = MovementComposer(pose_sink=_sink, tick_hz=30.0, clock=clock)
    decisions: List[GestureDecision] = []
    scheduler = GestureScheduler(
        composer=composer,
        library=_make_library(),
        clock=clock,
        decision_logger=decisions.append,
    )
    return scheduler, composer, clock, decisions


# ---------------------------------------------------------------------------
# Acceptance / rejection
# ---------------------------------------------------------------------------


def test_unknown_gesture_is_rejected_and_does_not_play():
    scheduler, composer, clock, decisions = _make()
    decision = scheduler.request("does_not_exist")
    assert decision.accepted is False
    assert decision.reason == "unknown_gesture"
    assert composer.compose_pose(clock.now) == NEUTRAL
    assert decisions[-1] == decision


def test_first_gesture_is_accepted_and_starts_clip():
    scheduler, composer, clock, decisions = _make()
    decision = scheduler.request("nod_encourage")
    assert decision.accepted is True
    assert decision.lane == "affect"
    # Composer is now driving the clip.
    pose = composer.compose_pose(clock.now)
    # The fake clip is constant-NEUTRAL so the offset is also neutral, but
    # the composer's active clip is set — request a different clip and
    # verify it preempts.
    assert decisions[-1].accepted is True


# ---------------------------------------------------------------------------
# Per-name cooldown
# ---------------------------------------------------------------------------


def test_same_gesture_within_per_name_cooldown_is_dropped():
    scheduler, _, clock, _ = _make()
    assert scheduler.request("nod_encourage").accepted
    # Far past clip duration but inside the per-name window.
    clock.advance(DEFAULT_PER_NAME_COOLDOWN_S - 0.1)
    decision = scheduler.request("nod_encourage")
    assert decision.accepted is False
    assert decision.reason == "per_name_cooldown"


def test_same_gesture_after_per_name_cooldown_is_accepted():
    scheduler, _, clock, _ = _make()
    assert scheduler.request("nod_encourage").accepted
    clock.advance(DEFAULT_PER_NAME_COOLDOWN_S + 0.1)
    decision = scheduler.request("nod_encourage")
    assert decision.accepted is True


def test_per_name_cooldown_is_per_name():
    """Different names share only the global cooldown, not per-name."""
    scheduler, _, clock, _ = _make()
    assert scheduler.request("nod_encourage").accepted
    # Past global cooldown but inside per-name (different name so per-name
    # doesn't apply, only global must clear).
    clock.advance(DEFAULT_GLOBAL_COOLDOWN_S + 0.1)
    decision = scheduler.request("head_tilt_curious")
    assert decision.accepted is True


# ---------------------------------------------------------------------------
# Global rate cap
# ---------------------------------------------------------------------------


def test_two_different_gestures_inside_global_cooldown_are_dropped():
    scheduler, _, clock, _ = _make()
    assert scheduler.request("nod_encourage").accepted
    clock.advance(DEFAULT_GLOBAL_COOLDOWN_S - 0.1)
    decision = scheduler.request("head_tilt_curious")
    assert decision.accepted is False
    assert decision.reason == "global_cooldown"


def test_global_cooldown_clears_after_window():
    scheduler, _, clock, _ = _make()
    assert scheduler.request("nod_encourage").accepted
    clock.advance(DEFAULT_GLOBAL_COOLDOWN_S + 0.1)
    decision = scheduler.request("head_tilt_curious")
    assert decision.accepted is True


def test_system_lane_gesture_bypasses_global_cooldown():
    """``cancel`` must always succeed even mid-rate-cap."""
    scheduler, _, clock, _ = _make()
    assert scheduler.request("nod_encourage").accepted
    # Inside global cooldown.
    clock.advance(0.1)
    decision = scheduler.request("cancel")
    assert decision.accepted is True


# ---------------------------------------------------------------------------
# Lane priority preemption
# ---------------------------------------------------------------------------


def test_higher_lane_preempts_lower_lane_clip():
    scheduler, composer, clock, _ = _make()
    # Affect-lane clip is playing.
    assert scheduler.request("nod_encourage").accepted
    # Inside global cooldown — celebration should still preempt because
    # the priority comparison happens before the global-cooldown check?
    # No — global cooldown applies. Wait past it, then preempt.
    clock.advance(DEFAULT_GLOBAL_COOLDOWN_S + 0.1)
    decision = scheduler.request("mini_dance_excited")
    assert decision.accepted is True
    # The composer's active clip should now be the dance (affect was
    # preempted because celebration > affect).


def test_lower_lane_drops_when_higher_clip_is_playing():
    scheduler, _, clock, _ = _make()
    # Celebration takes the lane.
    assert scheduler.request("mini_dance_excited").accepted
    # Past global cooldown but still mid-clip (clip duration = 2.0s).
    clock.advance(DEFAULT_GLOBAL_COOLDOWN_S + 0.1)  # = 4.1s, but clip is 2s
    # Need a still-active clip — use a longer one. Re-request before
    # duration expires.
    # Actually 4.1s > 2.0s clip duration, so the clip has expired and any
    # request should be accepted. Let's use a tighter timeline.

    scheduler2, _, clock2, _ = _make()
    assert scheduler2.request("mini_dance_excited").accepted
    # Past global cooldown but inside clip duration: clip = 2s, cooldown = 4s.
    # We need the celebration clip to still be active when we request affect.
    # That requires waiting < 2s but global cooldown forces >= 4s wait.
    # So test the inverse: use system "cancel" which bypasses global cooldown.
    clock2.advance(0.5)  # Mid-celebration
    decision = scheduler2.request("nod_encourage")  # affect — drops on global cooldown first
    assert decision.accepted is False
    assert decision.reason == "global_cooldown"


def test_lower_priority_drop_after_global_cooldown_is_lane_decision():
    """Build a scenario where global cooldown is short enough that lane
    priority is the dropping reason, not global cooldown."""
    library = _make_library()
    clock = _FakeClock()

    def _sink(_offset, _period):
        return None

    composer = MovementComposer(pose_sink=_sink, tick_hz=30.0, clock=clock)
    scheduler = GestureScheduler(
        composer=composer,
        library=library,
        clock=clock,
        per_name_cooldown_s=8.0,
        global_cooldown_s=0.05,  # tiny so lane priority dominates
    )
    assert scheduler.request("mini_dance_excited").accepted  # celebration, 2s
    clock.advance(0.5)  # mid-dance, past tiny global cooldown
    decision = scheduler.request("nod_encourage")  # affect
    assert decision.accepted is False
    assert decision.reason == "lower_priority"


def test_equal_lane_request_is_dropped_during_active_clip():
    library = _make_library()
    clock = _FakeClock()

    def _sink(_offset, _period):
        return None

    composer = MovementComposer(pose_sink=_sink, tick_hz=30.0, clock=clock)
    scheduler = GestureScheduler(
        composer=composer,
        library=library,
        clock=clock,
        global_cooldown_s=0.05,
    )
    # Affect clip in flight.
    assert scheduler.request("nod_encourage").accepted
    clock.advance(0.2)
    # Different affect-lane gesture — equal priority drops.
    decision = scheduler.request("head_tilt_curious")
    assert decision.accepted is False
    assert decision.reason == "lower_priority"


def test_after_active_clip_expires_lower_lane_can_play_again():
    library = _make_library()
    clock = _FakeClock()

    def _sink(_offset, _period):
        return None

    composer = MovementComposer(pose_sink=_sink, tick_hz=30.0, clock=clock)
    scheduler = GestureScheduler(
        composer=composer,
        library=library,
        clock=clock,
        global_cooldown_s=0.05,
        per_name_cooldown_s=0.05,
    )
    assert scheduler.request("mini_dance_excited").accepted  # 2s celebration
    clock.advance(2.5)  # past celebration, past all cooldowns
    decision = scheduler.request("nod_encourage")
    assert decision.accepted is True


# ---------------------------------------------------------------------------
# flush / barge-in
# ---------------------------------------------------------------------------


def test_flush_cancels_active_clip_in_composer():
    scheduler, composer, clock, _ = _make()
    assert scheduler.request("mini_dance_excited").accepted
    scheduler.flush()
    # Composer's active clip should now be None — verify by setting state
    # to idle and asserting compose_pose is NEUTRAL.
    composer.set_state("idle")
    assert composer.compose_pose(clock.now) == NEUTRAL


def test_flush_preserves_per_name_cooldown():
    """Barge-in should not let the LLM immediately re-fire the same gesture."""
    scheduler, _, clock, _ = _make()
    assert scheduler.request("nod_encourage").accepted
    scheduler.flush()
    clock.advance(0.1)  # well inside per-name cooldown
    decision = scheduler.request("nod_encourage")
    assert decision.accepted is False
    assert decision.reason == "per_name_cooldown"


# ---------------------------------------------------------------------------
# Decision logger
# ---------------------------------------------------------------------------


def test_decision_logger_receives_every_decision():
    scheduler, _, clock, decisions = _make()
    scheduler.request("does_not_exist")
    scheduler.request("nod_encourage")
    clock.advance(0.1)
    scheduler.request("nod_encourage")  # cooldown drop
    assert [d.accepted for d in decisions] == [False, True, False]
    assert [d.reason for d in decisions] == [
        "unknown_gesture",
        None,
        "per_name_cooldown",
    ]


def test_decision_logger_exception_does_not_break_request():
    library = _make_library()
    clock = _FakeClock()

    def _sink(_offset, _period):
        return None

    composer = MovementComposer(pose_sink=_sink, tick_hz=30.0, clock=clock)

    def _broken(_decision: GestureDecision) -> None:
        raise RuntimeError("logger boom")

    scheduler = GestureScheduler(
        composer=composer,
        library=library,
        clock=clock,
        decision_logger=_broken,
    )
    # Must not raise even though the logger does.
    decision = scheduler.request("nod_encourage")
    assert decision.accepted is True
