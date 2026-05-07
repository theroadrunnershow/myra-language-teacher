"""Unit tests for :mod:`motion.face_offset`."""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Tuple

import pytest

from motion.face_offset import (
    CAMERA_HFOV_ENV_VAR,
    CAMERA_VFOV_ENV_VAR,
    DEFAULT_GAINS,
    DEFAULT_HFOV_DEG,
    DEFAULT_VFOV_DEG,
    MAX_PAN_RAD,
    MAX_TILT_RAD,
    FaceOffsetMixer,
)
from motion.types import NEUTRAL, PoseOffset


# Pre-computed angle scales for assertions. The mixer scales normalized
# tracker output by HALF the camera FOV so a face at the frame edge
# commands the geometric angle the face is offset by.
_HALF_HFOV_RAD = math.radians(DEFAULT_HFOV_DEG / 2.0)
_HALF_VFOV_RAD = math.radians(DEFAULT_VFOV_DEG / 2.0)


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
):
    clock = clock or _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    return mixer, tracker, clock


def _settle(mixer: FaceOffsetMixer, clock: _FakeClock, *, ticks: int = 200, dt: float = 0.033) -> None:
    """Run the slew limiter to convergence."""
    for _ in range(ticks):
        clock.advance(dt)
        mixer.current_offset()


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


def test_none_publish_yields_neutral_when_never_tracked():
    """If we never tracked a subject, holding "the last offset" is neutral."""
    mixer, tracker, _ = _make()
    tracker.publish(None)
    assert mixer.current_offset() == NEUTRAL


# ---------------------------------------------------------------------------
# FOV-based mapping + direction conventions + caps
# ---------------------------------------------------------------------------


def test_positive_pan_yields_negative_yaw_scaled_by_half_hfov():
    """Empirical SDK convention: face on right of frame → head turns right
    via *negative* yaw. The naive `pan * (HFOV/2)` is negated."""
    mixer, tracker, clock = _make()
    mixer.set_gain_state("child_speaking")  # gain = 1.0 to avoid scaling
    tracker.publish((1.0, 0.0))
    _settle(mixer, clock)
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(-_HALF_HFOV_RAD)
    assert offset.head_pitch == pytest.approx(0.0)


def test_negative_pan_yields_positive_yaw_scaled_by_half_hfov():
    mixer, tracker, clock = _make()
    mixer.set_gain_state("child_speaking")
    tracker.publish((-1.0, 0.0))
    _settle(mixer, clock)
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(_HALF_HFOV_RAD)


def test_positive_tilt_yields_positive_pitch_scaled_by_half_vfov():
    mixer, tracker, clock = _make()
    mixer.set_gain_state("child_speaking")
    tracker.publish((0.0, 1.0))
    _settle(mixer, clock)
    offset = mixer.current_offset()
    assert offset.head_pitch == pytest.approx(_HALF_VFOV_RAD)


def test_pan_beyond_unit_clamps_to_safety_cap():
    mixer, tracker, clock = _make()
    mixer.set_gain_state("child_speaking")
    # An out-of-range value the tracker would clip first, but the mixer
    # also clips against its safety cap. Use a value × half-HFOV that
    # exceeds MAX_PAN_RAD (35°) so the cap is the binding limit. Sign is
    # negated by the empirical convention (see _target_offset_locked).
    tracker.publish((5.0, 0.0))
    _settle(mixer, clock)
    offset = mixer.current_offset()
    assert offset.head_yaw == pytest.approx(-MAX_PAN_RAD)


def test_tilt_beyond_unit_clamps_to_safety_cap():
    mixer, tracker, clock = _make()
    mixer.set_gain_state("child_speaking")
    tracker.publish((0.0, 5.0))
    _settle(mixer, clock)
    offset = mixer.current_offset()
    assert offset.head_pitch == pytest.approx(MAX_TILT_RAD)


# ---------------------------------------------------------------------------
# FOV env-var overrides
# ---------------------------------------------------------------------------


def test_hfov_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv(CAMERA_HFOV_ENV_VAR, "90.0")
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("child_speaking")
    tracker.publish((0.5, 0.0))
    _settle(mixer, clock)
    # 0.5 × (90°/2) = 22.5°, then negated for SDK convention.
    assert mixer.current_offset().head_yaw == pytest.approx(-math.radians(22.5))


def test_vfov_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv(CAMERA_VFOV_ENV_VAR, "70.0")
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("child_speaking")
    tracker.publish((0.0, 0.5))
    _settle(mixer, clock)
    # 0.5 × (70°/2) = 17.5° — inside the 25° tilt cap.
    assert mixer.current_offset().head_pitch == pytest.approx(math.radians(17.5))


def test_invalid_fov_env_var_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(CAMERA_HFOV_ENV_VAR, "not-a-number")
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("child_speaking")
    tracker.publish((1.0, 0.0))
    _settle(mixer, clock)
    assert mixer.current_offset().head_yaw == pytest.approx(-_HALF_HFOV_RAD)


# ---------------------------------------------------------------------------
# Gain state
# ---------------------------------------------------------------------------


def test_idle_gain_scales_offset():
    mixer, tracker, clock = _make()
    # default state is "idle" with gain 0.7
    tracker.publish((1.0, 0.0))
    _settle(mixer, clock)
    assert mixer.current_offset().head_yaw == pytest.approx(
        -_HALF_HFOV_RAD * DEFAULT_GAINS["idle"]
    )


def test_child_speaking_gain_is_one():
    mixer, tracker, clock = _make()
    mixer.set_gain_state("child_speaking")
    tracker.publish((1.0, 0.0))
    _settle(mixer, clock)
    assert mixer.current_offset().head_yaw == pytest.approx(-_HALF_HFOV_RAD * 1.0)


def test_robot_speaking_gain_reduces_offset():
    mixer, tracker, clock = _make()
    tracker.publish((1.0, 0.0))
    mixer.set_gain_state("robot_speaking")
    _settle(mixer, clock)
    assert mixer.current_offset().head_yaw == pytest.approx(
        -_HALF_HFOV_RAD * DEFAULT_GAINS["robot_speaking"]
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
    mixer, tracker, clock = _make()
    tracker.publish((0.5, 0.5))
    mixer.set_gain_state("robot_speaking")
    _settle(mixer, clock)
    # Tracker briefly loses the subject.
    tracker.publish(None)
    offset = mixer.current_offset()
    # Should still be looking at the snapshot — non-zero.
    assert offset.head_yaw != 0.0
    assert offset.head_pitch != 0.0


def test_robot_speaking_holds_target_when_tracker_re_publishes_different_target():
    """While the robot is talking we should NOT swing to a new subject."""
    clock = _FakeClock()
    mixer, tracker, _ = _make(clock=clock)
    tracker.publish((0.5, 0.0))
    mixer.set_gain_state("robot_speaking")
    _settle(mixer, clock)
    expected_yaw = mixer.current_offset().head_yaw
    # A new subject appears — we ignore it during robot speech.
    tracker.publish((-1.0, 0.0))
    _settle(mixer, clock)
    assert mixer.current_offset().head_yaw == pytest.approx(expected_yaw, abs=1e-9)


def test_set_robot_speaking_snaps_held_to_latest_target():
    """Entering robot_speaking should lock onto the freshest known target."""
    mixer, tracker, clock = _make()
    tracker.publish((0.2, 0.0))
    tracker.publish((0.8, 0.0))  # newest target right before the lock
    mixer.set_gain_state("robot_speaking")
    tracker.publish(None)
    _settle(mixer, clock)
    # Held value should reflect the 0.8 publish, not the earlier 0.2.
    assert mixer.current_offset().head_yaw == pytest.approx(
        -_HALF_HFOV_RAD * 0.8 * DEFAULT_GAINS["robot_speaking"]
    )


def test_idle_state_follows_new_target_after_publish():
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    tracker.publish((0.2, 0.0))
    _settle(mixer, clock)
    initial = mixer.current_offset().head_yaw
    # pan>0 produces yaw<0 under the empirical SDK convention.
    assert initial < 0
    tracker.publish((-0.2, 0.0))
    _settle(mixer, clock)
    assert mixer.current_offset().head_yaw > 0


# ---------------------------------------------------------------------------
# Hold-on-None contract: no ease-back to neutral
# ---------------------------------------------------------------------------


def test_target_lost_holds_last_offset():
    """When the tracker publishes None, the head holds where it was."""
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("idle")
    tracker.publish((1.0, 0.5))
    _settle(mixer, clock)
    held = mixer.current_offset()
    # Sign-agnostic: just confirm we settled off-neutral on both axes.
    assert held.head_yaw != 0.0
    assert held.head_pitch != 0.0

    tracker.publish(None)
    # Plenty of time for any decay to manifest. None: must not happen.
    _settle(mixer, clock, ticks=300, dt=0.05)
    after = mixer.current_offset()
    assert after.head_yaw == pytest.approx(held.head_yaw, abs=1e-9)
    assert after.head_pitch == pytest.approx(held.head_pitch, abs=1e-9)


def test_tracker_silence_holds_last_offset():
    """No publish at all (tracker quiet) holds the last commanded offset.

    Replaces the prior staleness-release test — there is no longer a release
    window, so the dead-zone-suppression case must hold the head still.
    """
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("idle")

    tracker.publish((0.5, 0.0))
    _settle(mixer, clock)
    held = mixer.current_offset()
    # pan>0 → head_yaw<0 under the empirical SDK convention.
    assert held.head_yaw < 0

    # Tracker goes silent — no further publishes (e.g. dead-zone suppression).
    _settle(mixer, clock, ticks=300, dt=0.05)
    assert mixer.current_offset().head_yaw == pytest.approx(held.head_yaw, abs=1e-9)


# ---------------------------------------------------------------------------
# Slew rate
# ---------------------------------------------------------------------------


def test_first_tick_does_not_snap():
    """No prior displayed offset → the first tick stays at NEUTRAL.

    The slew limiter then ramps the offset in over subsequent ticks at the
    velocity cap; there is no instantaneous jump on first tick.
    """
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("child_speaking")
    tracker.publish((1.0, 0.0))
    first = mixer.current_offset()
    assert first == NEUTRAL


def test_slew_eventually_converges_to_target():
    """Big jumps are spread over multiple ticks at the velocity cap, then converge."""
    clock = _FakeClock()
    mixer = FaceOffsetMixer(clock=clock)
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("child_speaking")

    tracker.publish((1.0, 0.0))
    # 100 ticks × 33 ms = 3.3 s — comfortably more than (HFOV/2) / 30°/s.
    _settle(mixer, clock)
    assert mixer.current_offset().head_yaw == pytest.approx(-_HALF_HFOV_RAD)


def test_slew_rate_max_step_per_tick_respects_velocity_cap():
    """Per-tick step ≤ max_velocity * dt."""
    clock = _FakeClock()
    mixer = FaceOffsetMixer(
        clock=clock, max_angular_velocity_rad_s=math.radians(30.0)
    )
    tracker = _FakeTracker()
    mixer.attach(tracker)
    mixer.set_gain_state("child_speaking")

    # Bootstrap displayed_at without crossing toward the new target.
    mixer.current_offset()
    tracker.publish((1.0, 0.0))
    clock.advance(0.1)  # 100 ms at 30°/s = 3° max step
    step = mixer.current_offset().head_yaw
    # pan>0 drives yaw negative; |step| ≤ 3° and the head moved.
    assert abs(step) <= math.radians(3.0) + 1e-9
    assert step < 0.0
