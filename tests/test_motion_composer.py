"""Unit tests for :mod:`motion.composer`."""

from __future__ import annotations

import math
import threading
import time
from typing import List, Tuple

import pytest

from motion.composer import (
    DEFAULT_SAFETY_CAPS,
    DEFAULT_STATE_POSES,
    MovementComposer,
)
from motion.library import Clip
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


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: List[Tuple[PoseOffset, float]] = []

    def __call__(self, offset: PoseOffset, period: float) -> None:
        self.calls.append((offset, period))


def _const_clip(value: PoseOffset, duration: float = 1.0, lane: str = "affect") -> Clip:
    return Clip(name="test", duration=duration, lane=lane, pose_at=lambda t: value)  # noqa: ARG005


def _make(
    *, clock: _FakeClock | None = None, tick_hz: float = 30.0
) -> Tuple[MovementComposer, _FakeClock, _RecordingSink]:
    clock = clock or _FakeClock()
    sink = _RecordingSink()
    composer = MovementComposer(pose_sink=sink, tick_hz=tick_hz, clock=clock)
    return composer, clock, sink


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_constructor_rejects_non_positive_tick_hz():
    with pytest.raises(ValueError):
        MovementComposer(pose_sink=_RecordingSink(), tick_hz=0.0)
    with pytest.raises(ValueError):
        MovementComposer(pose_sink=_RecordingSink(), tick_hz=-5.0)


def test_default_state_is_idle_and_idle_pose_is_neutral():
    composer, _, _ = _make()
    assert composer.compose_pose(0.0) == NEUTRAL


# ---------------------------------------------------------------------------
# State poses
# ---------------------------------------------------------------------------


def test_listen_state_yields_listen_roll():
    composer, _, _ = _make()
    composer.set_state("listen")
    pose = composer.compose_pose(0.0)
    assert math.isclose(pose.head_roll, DEFAULT_STATE_POSES["listen"].head_roll)


def test_unknown_state_falls_back_to_idle():
    composer, _, _ = _make()
    composer.set_state("not_a_state")
    assert composer.compose_pose(0.0) == NEUTRAL


# ---------------------------------------------------------------------------
# L2 clip
# ---------------------------------------------------------------------------


def test_play_clip_contributes_to_pose():
    composer, clock, _ = _make()
    composer.play_clip(_const_clip(PoseOffset(head_pitch=0.1)))
    assert composer.compose_pose(clock.now).head_pitch == pytest.approx(0.1)


def test_clip_expires_after_duration_and_returns_neutral():
    composer, clock, _ = _make()
    composer.play_clip(_const_clip(PoseOffset(head_pitch=0.1), duration=1.0))
    clock.advance(1.0)
    pose = composer.compose_pose(clock.now)
    assert pose == NEUTRAL


def test_play_clip_replaces_active_clip():
    composer, clock, _ = _make()
    composer.play_clip(_const_clip(PoseOffset(head_pitch=0.1), duration=10.0))
    clock.advance(0.5)
    composer.play_clip(_const_clip(PoseOffset(head_yaw=0.2), duration=10.0))
    pose = composer.compose_pose(clock.now)
    assert pose.head_pitch == 0.0
    assert pose.head_yaw == pytest.approx(0.2)


def test_cancel_clip_drops_active_clip():
    composer, clock, _ = _make()
    composer.play_clip(_const_clip(PoseOffset(head_pitch=0.1), duration=10.0))
    composer.cancel_clip()
    assert composer.compose_pose(clock.now) == NEUTRAL


def test_clip_pose_at_exception_drops_clip():
    """A misbehaving clip must not crash the composer thread."""
    composer, clock, _ = _make()

    def _explode(t: float) -> PoseOffset:  # noqa: ARG001
        raise RuntimeError("kaboom")

    composer.play_clip(Clip("bad", duration=1.0, lane="affect", pose_at=_explode))
    pose = composer.compose_pose(clock.now)
    assert pose == NEUTRAL
    # Subsequent ticks should keep returning neutral, not re-raise.
    assert composer.compose_pose(clock.now) == NEUTRAL


# ---------------------------------------------------------------------------
# L1 / L3 sources
# ---------------------------------------------------------------------------


def test_wobble_source_adds_to_pose():
    composer, _, _ = _make()
    composer.set_wobble_source(lambda: PoseOffset(head_pitch=0.05))
    assert composer.compose_pose(0.0).head_pitch == pytest.approx(0.05)


def test_face_offset_source_adds_to_pose():
    composer, _, _ = _make()
    composer.set_face_offset_source(lambda: PoseOffset(head_yaw=0.07))
    assert composer.compose_pose(0.0).head_yaw == pytest.approx(0.07)


def test_all_layers_sum():
    composer, clock, _ = _make()
    composer.set_state("listen")  # base: head_roll = 15°
    composer.play_clip(_const_clip(PoseOffset(head_pitch=0.05)))
    composer.set_wobble_source(lambda: PoseOffset(head_pitch=0.02))
    composer.set_face_offset_source(lambda: PoseOffset(head_yaw=0.03))

    pose = composer.compose_pose(clock.now)
    assert pose.head_pitch == pytest.approx(0.07)  # clip + wobble
    assert pose.head_yaw == pytest.approx(0.03)  # face only
    assert pose.head_roll == pytest.approx(DEFAULT_STATE_POSES["listen"].head_roll)


def test_source_returning_none_is_treated_as_neutral():
    composer, _, _ = _make()
    composer.set_wobble_source(lambda: None)  # type: ignore[arg-type,return-value]
    assert composer.compose_pose(0.0) == NEUTRAL


def test_source_raising_is_swallowed():
    composer, _, _ = _make()

    def _explode() -> PoseOffset:
        raise RuntimeError("kaboom")

    composer.set_wobble_source(_explode)
    assert composer.compose_pose(0.0) == NEUTRAL


def test_source_can_be_cleared_with_none():
    composer, _, _ = _make()
    composer.set_wobble_source(lambda: PoseOffset(head_pitch=0.1))
    composer.set_wobble_source(None)
    assert composer.compose_pose(0.0) == NEUTRAL


# ---------------------------------------------------------------------------
# Safety caps
# ---------------------------------------------------------------------------


def test_safety_caps_clip_runaway_source():
    sink = _RecordingSink()
    tight_caps = PoseOffset(head_pitch=0.05)
    composer = MovementComposer(
        pose_sink=sink, tick_hz=30.0, clock=_FakeClock(), safety_caps=tight_caps
    )
    composer.set_wobble_source(lambda: PoseOffset(head_pitch=10.0))
    pose = composer.compose_pose(0.0)
    assert pose.head_pitch == pytest.approx(0.05)


def test_default_caps_allow_listen_pose():
    """Sanity: the default cap must be loose enough for the listen state."""
    composer, _, _ = _make()
    composer.set_state("listen")
    pose = composer.compose_pose(0.0)
    assert math.isclose(pose.head_roll, DEFAULT_STATE_POSES["listen"].head_roll)
    assert pose.head_roll <= DEFAULT_SAFETY_CAPS.head_roll


# ---------------------------------------------------------------------------
# tick / sink
# ---------------------------------------------------------------------------


def test_tick_calls_sink_with_period():
    composer, clock, sink = _make(tick_hz=50.0)
    composer.set_wobble_source(lambda: PoseOffset(head_pitch=0.1))
    composer.tick()
    assert len(sink.calls) == 1
    offset, period = sink.calls[0]
    assert offset.head_pitch == pytest.approx(0.1)
    assert period == pytest.approx(1.0 / 50.0)


def test_tick_swallows_sink_exceptions():
    def _broken_sink(_offset: PoseOffset, _period: float) -> None:
        raise RuntimeError("sink down")

    composer = MovementComposer(pose_sink=_broken_sink, tick_hz=30.0, clock=_FakeClock())
    # Must not raise.
    pose = composer.tick()
    assert pose == NEUTRAL


def test_tick_at_uses_provided_clock_value():
    composer, _, sink = _make()
    composer.play_clip(_const_clip(PoseOffset(head_pitch=0.1), duration=10.0))
    composer.tick_at(0.5)
    composer.tick_at(11.0)  # past clip end; should be neutral now
    assert sink.calls[0][0].head_pitch == pytest.approx(0.1)
    assert sink.calls[1][0] == NEUTRAL


# ---------------------------------------------------------------------------
# Background loop lifecycle
# ---------------------------------------------------------------------------


def test_start_then_stop_runs_at_least_one_tick():
    sink = _RecordingSink()
    composer = MovementComposer(
        pose_sink=sink,
        tick_hz=200.0,  # fast so we don't slow tests
    )
    composer.start()
    # Wait briefly for ticks to land.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and len(sink.calls) < 3:
        time.sleep(0.01)
    composer.stop(timeout=1.0)
    assert len(sink.calls) >= 3


def test_start_is_idempotent():
    sink = _RecordingSink()
    composer = MovementComposer(pose_sink=sink, tick_hz=200.0)
    composer.start()
    composer.start()  # should not spawn a second thread or raise
    composer.stop(timeout=1.0)


def test_stop_without_start_is_safe():
    sink = _RecordingSink()
    composer = MovementComposer(pose_sink=sink, tick_hz=30.0)
    # Should not raise.
    composer.stop(timeout=0.1)


def test_concurrent_set_state_and_compose_is_safe():
    """Smoke: hammer set_state from one thread while compose_pose runs."""
    composer, clock, _ = _make()
    composer.set_wobble_source(lambda: PoseOffset(head_pitch=0.01))
    stop = threading.Event()

    def _flipper() -> None:
        states = ("idle", "listen", "speak")
        i = 0
        while not stop.is_set():
            composer.set_state(states[i % 3])
            i += 1

    thread = threading.Thread(target=_flipper)
    thread.start()
    try:
        for _ in range(500):
            composer.compose_pose(clock.now)
    finally:
        stop.set()
        thread.join(timeout=1.0)
