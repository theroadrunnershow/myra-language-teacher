"""Unit tests for :mod:`motion.face_offset`."""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Tuple

import pytest

from motion.face_offset import (
    DEFAULT_GAINS,
    MAX_PAN_RAD,
    MAX_TILT_RAD,
    FaceOffsetMixer,
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


class _FakeTracker:
    """Pollen-shaped subscribe API that lets the test push targets directly."""

    def __init__(self) -> None:
        self._subscribers: List[Callable[[Optional[Tuple[float, float]]], None]] = []
        self.subscribe_calls = 0
        self.unsubscribe_calls = 0

    def subscribe(self, callback) -> Callable[[], None]:
        self.subscribe_calls += 1
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            self.unsubscribe_calls += 1
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def publish(self, target: Optional[Tuple[float, float]]) -> None:
        for cb in list(self._subscribers):
            cb(target)


def _make(
    *,
    clock: Optional[_FakeClock] = None,
    no_target_release_s: float = 0.6,
):
    clock = clock or _FakeClock()
    mixer = FaceOffsetMixer(
        clock=clock,
        no_target_release_s=no_target_release_s,
    )
    tracker = _FakeTracker()
    mixer.attach(tracker)
    return mixer, tracker, clock


# ---------------------------------------------------------------------------
# Subscription lifecycle
# ---------------------------------------------------------------------------


def test_attach_subscribes_to_tracker():
    mixer = FaceOffsetMixer(clock=_FakeClock())
    tracker = _FakeTracker()
    mixer.attach(tracker)
    assert tracker.subscribe_calls == 1


def test_attach_is_idempotent():
    mixer = FaceOffsetMixer(clock=_FakeClock())
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.attach(tracker)
    assert tracker.subscribe_calls == 1


def test_detach_unsubscribes():
    mixer, tracker, _ = _make()
    mixer.detach()
    assert tracker.unsubscribe_calls == 1


def test_detach_is_idempotent():
    mixer, tracker, _ = _make()
    mixer.detach()
    mixer.detach()
    assert tracker.unsubscribe_calls == 1


def test_attach_to_non_tracker_raises_typeerror():
    mixer = FaceOffsetMixer(clock=_FakeClock())
    with pytest.raises(TypeError):
        mixer.attach(object())


# ---------------------------------------------------------------------------
# Initial / no-target behavior
# ---------------------------------------------------------------------------


def test_no_publish_yields_neutral():
    mixer, _, _ = _make()
    assert mixer.current_offset() == NEUTRAL


def test_none_publish_yields_neutral():
    mixer, tracker, _ = _make()
    tracker.publish(None)
    assert mixer.current_offset() == NEUTRAL


# ---------------------------------------------------------------------------
# Direction conventions + caps
# ---------------------------------------------------------------------------


def test_positive_pan_yields_positive_yaw():
    mixer, tracker, _ = _make()
    mixer.set_gain_state("child_speaking")  # gain = 1.0 to avoid scaling
    tracker.publish((1.0, 0.0))
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(MAX_PAN_RAD)
    assert offset.head_pitch == pytest.approx(0.0)


def test_negative_pan_yields_negative_yaw():
    mixer, tracker, _ = _make()
    mixer.set_gain_state("child_speaking")
    tracker.publish((-1.0, 0.0))
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(-MAX_PAN_RAD)


def test_positive_tilt_yields_positive_pitch():
    mixer, tracker, _ = _make()
    mixer.set_gain_state("child_speaking")
    tracker.publish((0.0, 1.0))
    offset = mixer.current_offset()
    assert offset.head_pitch == pytest.approx(MAX_TILT_RAD)


def test_pan_beyond_unit_clamps_to_max():
    mixer, tracker, _ = _make()
    mixer.set_gain_state("child_speaking")
    tracker.publish((5.0, 0.0))  # outside [-1, 1]
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(MAX_PAN_RAD)


# ---------------------------------------------------------------------------
# Gain state
# ---------------------------------------------------------------------------


def test_idle_gain_scales_offset():
    mixer, tracker, _ = _make()
    # default state is "idle" with gain 0.7
    tracker.publish((1.0, 0.0))
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(MAX_PAN_RAD * DEFAULT_GAINS["idle"])


def test_child_speaking_gain_is_one():
    mixer, tracker, _ = _make()
    mixer.set_gain_state("child_speaking")
    tracker.publish((1.0, 0.0))
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(MAX_PAN_RAD * 1.0)


def test_robot_speaking_gain_reduces_offset():
    mixer, tracker, _ = _make()
    tracker.publish((1.0, 0.0))
    mixer.set_gain_state("robot_speaking")
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(
        MAX_PAN_RAD * DEFAULT_GAINS["robot_speaking"]
    )


def test_unknown_gain_state_is_logged_and_ignored():
    mixer, tracker, _ = _make()
    mixer.set_gain_state("not_a_state")
    # State unchanged from default ("idle").
    assert mixer.gain_state == "idle"


# ---------------------------------------------------------------------------
# Robot-speaking hold (don't re-pick mid-utterance)
# ---------------------------------------------------------------------------


def test_robot_speaking_holds_target_when_tracker_publishes_none():
    mixer, tracker, _ = _make()
    tracker.publish((0.5, 0.5))
    mixer.set_gain_state("robot_speaking")
    # Tracker briefly loses the subject.
    tracker.publish(None)
    offset = mixer.current_offset()
    # Should still be looking at the snapshot — non-zero.
    assert offset.head_yaw != 0.0
    assert offset.head_pitch != 0.0


def test_robot_speaking_holds_target_when_tracker_re_publishes_different_target():
    """While the robot is talking we should NOT swing to a new subject.

    Critically: the fake clock MUST advance between the second publish and the
    later ticks. A frozen clock makes the slew limiter early-return at every
    tick (dt == 0), which masked the bug where ``_on_target`` overwrote
    ``_held_target`` mid-utterance.
    """
    clock = _FakeClock()
    mixer, tracker, _ = _make(clock=clock)
    tracker.publish((0.5, 0.0))
    mixer.set_gain_state("robot_speaking")
    expected_yaw = mixer.current_offset().head_yaw
    # A new subject appears — we ignore it during robot speech.
    tracker.publish((-1.0, 0.0))
    # Advance time so the slew limiter actually runs on subsequent ticks.
    # 2 s is well beyond the time it would take a 60°/s slew to swing from
    # +4° (gain 0.4 × 0.5 × 20°) to -8° (gain 0.4 × -1.0 × 20°), so the bug
    # would visibly land here if the held-target overwrite were happening.
    for _ in range(20):
        clock.advance(0.1)
        mixer.current_offset()
    assert mixer.current_offset().head_yaw == pytest.approx(expected_yaw, abs=1e-9)


def test_stale_tracker_target_releases_to_neutral_after_no_target_release_s():
    """If the tracker stops publishing (e.g. dead-zone entry suppresses
    publishes), the mixer must release the off-center target after
    ``no_target_release_s`` and ease back toward neutral.

    Regression for the case where ``FaceTracker._tick`` silently returns
    without publishing when the target is inside its dead-zone — leaving
    the mixer's stored target stuck at the previous off-center value.
    """
    clock = _FakeClock()
    mixer, tracker, _ = _make(clock=clock, no_target_release_s=0.6)

    # Settle the displayed offset on the off-center target by simulating
    # ~3 Hz publishes for a stretch (each well within the release window).
    for _ in range(2):
        tracker.publish((0.5, 0.0))
        for _ in range(10):
            clock.advance(0.025)
            mixer.current_offset()
    settled = mixer.current_offset()
    assert settled.head_yaw > 0  # confirm we did track it

    # Tracker goes silent (dead-zone suppression). Advance past the release
    # window so subsequent ticks see the target as stale and ease to neutral.
    for _ in range(60):
        clock.advance(0.05)
        mixer.current_offset()
    assert mixer.current_offset().head_yaw == pytest.approx(0.0, abs=1e-6)


def test_robot_speaking_ignores_stale_tracker_target():
    """During robot_speaking the held snapshot is authoritative; staleness
    of ``_tracker_target`` must not pull the head toward neutral."""
    clock = _FakeClock()
    mixer, tracker, _ = _make(clock=clock, no_target_release_s=0.6)
    tracker.publish((0.5, 0.0))
    mixer.set_gain_state("robot_speaking")
    expected = mixer.current_offset().head_yaw
    # Tracker goes silent for longer than the release window.
    clock.advance(5.0)
    for _ in range(20):
        clock.advance(0.05)
        mixer.current_offset()
    # Held target still drives the offset — staleness check skipped in
    # robot_speaking.
    assert mixer.current_offset().head_yaw == pytest.approx(expected, abs=1e-9)


def test_set_robot_speaking_snaps_held_to_latest_target():
    """Entering robot_speaking should lock onto the freshest known target."""
    mixer, tracker, _ = _make()
    tracker.publish((0.2, 0.0))
    tracker.publish((0.8, 0.0))  # newest target right before the lock
    mixer.set_gain_state("robot_speaking")
    tracker.publish(None)
    offset = mixer.current_offset()
    # Held value should reflect the 0.8 publish, not the earlier 0.2.
    assert offset.head_yaw == pytest.approx(
        MAX_PAN_RAD * 0.8 * DEFAULT_GAINS["robot_speaking"]
    )


def test_idle_state_follows_new_target_after_publish():
    mixer, tracker, _ = _make()
    tracker.publish((0.2, 0.0))
    mixer.current_offset()
    tracker.publish((-0.2, 0.0))
    # Long enough for slew to catch up.
    _, _, clock = (mixer, tracker, None)
    # We don't have a clock handle here; just call current_offset enough
    # times relying on the test fixture's clock advances.
    # Use a fresh helper:
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    tracker.publish((0.2, 0.0))
    mixer.current_offset()
    tracker.publish((-0.2, 0.0))
    # Slew @ 60°/s covers ±2.8° (0.2 × 20° × 0.7 idle gain) in well under
    # 100ms. Stay inside the no_target_release_s window (0.6s default) so the
    # staleness check doesn't release the target before the assertion.
    clock.advance(0.1)
    offset = mixer.current_offset()
    assert offset.head_yaw < 0


# ---------------------------------------------------------------------------
# Slew rate
# ---------------------------------------------------------------------------


def test_first_tick_snaps_to_target():
    """No prior displayed offset → snap to target on first tick."""
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("child_speaking")
    tracker.publish((1.0, 0.0))
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(MAX_PAN_RAD)


def test_slew_rate_caps_angular_velocity():
    """Big jumps are spread over multiple ticks at the velocity cap."""
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("child_speaking")

    # Start at one extreme, settle.
    tracker.publish((-1.0, 0.0))
    mixer.current_offset()
    # Jump to the other extreme.
    tracker.publish((1.0, 0.0))
    # One tick at 16ms — should not have crossed the full ±20° in one frame.
    clock.advance(0.016)
    intermediate = mixer.current_offset().head_yaw
    assert intermediate < MAX_PAN_RAD
    assert intermediate > -MAX_PAN_RAD

    # Eventually catches up. Re-publish each frame so the target stays fresh
    # while the slew (60°/s, must cover ~40°) takes ~0.7s to converge — a
    # one-shot 2s advance would exceed no_target_release_s and ease back to
    # neutral instead.
    for _ in range(50):
        tracker.publish((1.0, 0.0))
        clock.advance(0.02)
        mixer.current_offset()
    final = mixer.current_offset().head_yaw
    assert final == pytest.approx(MAX_PAN_RAD)


def test_slew_rate_max_step_per_tick_respects_velocity_cap():
    """Per-tick step ≤ max_velocity * dt."""
    clock = _FakeClock()
    mixer = FaceOffsetMixer(
        clock=clock, max_angular_velocity_rad_s=math.radians(60.0)
    )
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("child_speaking")

    tracker.publish((-1.0, 0.0))
    mixer.current_offset()
    tracker.publish((1.0, 0.0))
    clock.advance(0.1)  # 100ms at 60°/s = 6° max step
    step = mixer.current_offset().head_yaw - (-MAX_PAN_RAD)
    # Ensure we didn't exceed 6° + tiny tolerance.
    assert step <= math.radians(6.0) + 1e-9


# ---------------------------------------------------------------------------
# Recovery toward neutral when tracker drops the subject
# ---------------------------------------------------------------------------


def test_target_lost_eases_back_to_neutral():
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("idle")
    tracker.publish((1.0, 0.0))
    mixer.current_offset()
    tracker.publish(None)
    # Plenty of time to slew back.
    clock.advance(2.0)
    assert mixer.current_offset() == NEUTRAL
