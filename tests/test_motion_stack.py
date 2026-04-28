"""Unit tests for :mod:`motion.stack`."""

from __future__ import annotations

import struct
from typing import List

import pytest

from motion.stack import (
    ALL_LAYERS,
    LAYER_GESTURES,
    LAYER_TRACKING,
    LAYER_WOBBLE,
    MotionStack,
    MotionStackConfig,
    parse_layers,
)
from motion.tool_specs import (
    PLAY_GESTURE_TOOL_NAME,
    STOP_MOTION_TOOL_NAME,
)


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _FakeRobotController:
    def __init__(self) -> None:
        self.pose_calls: List[dict] = []

    def apply_pose(self, **kwargs) -> bool:
        self.pose_calls.append(kwargs)
        return True


class _FakeTracker:
    def __init__(self) -> None:
        self.subscribers: List = []

    def subscribe(self, cb):
        self.subscribers.append(cb)
        return lambda: self.subscribers.remove(cb)

    def publish(self, target):
        for cb in list(self.subscribers):
            cb(target)


def _loud_chunk(n: int = 480) -> bytes:
    import math

    return struct.pack(
        f"<{n}h", *(int(12000 * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(n))
    )


# ---------------------------------------------------------------------------
# parse_layers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", [None, "", "full", "FULL"])
def test_parse_layers_defaults_to_all(spec):
    assert parse_layers(spec) == ALL_LAYERS


def test_parse_layers_none_yields_empty():
    assert parse_layers("none") == frozenset()


def test_parse_layers_csv_subset():
    assert parse_layers("wobble,tracking") == frozenset({"wobble", "tracking"})


def test_parse_layers_drops_unknown_layers():
    assert parse_layers("wobble,quasar") == frozenset({"wobble"})


def test_parse_layers_handles_extra_whitespace():
    assert parse_layers(" gestures , tracking ") == frozenset({"gestures", "tracking"})


# ---------------------------------------------------------------------------
# Construction with subsets of layers
# ---------------------------------------------------------------------------


def test_full_stack_constructs_all_layers():
    robot = _FakeRobotController()
    stack = MotionStack(robot_controller=robot)
    # additional_tool_specs is the visible signal that the gestures layer
    # is on.
    names = {t["name"] for t in stack.additional_tool_specs()}
    assert names == {PLAY_GESTURE_TOOL_NAME, STOP_MOTION_TOOL_NAME}


def test_no_layers_means_no_tools_or_face_subscription():
    robot = _FakeRobotController()
    stack = MotionStack(
        robot_controller=robot,
        config=MotionStackConfig(layers=frozenset()),
    )
    assert stack.additional_tool_specs() == []
    assert stack.gesture_vocabulary_prompt_block() == ""

    tracker = _FakeTracker()
    stack.attach_face_tracker(tracker)  # should be no-op
    assert tracker.subscribers == []


def test_only_tracking_layer_subscribes_to_tracker_but_skips_tools():
    robot = _FakeRobotController()
    stack = MotionStack(
        robot_controller=robot,
        config=MotionStackConfig(layers=frozenset({LAYER_TRACKING})),
    )
    assert stack.additional_tool_specs() == []
    tracker = _FakeTracker()
    stack.attach_face_tracker(tracker)
    assert len(tracker.subscribers) == 1


def test_only_wobble_layer_feeds_wobbler_only():
    robot = _FakeRobotController()
    stack = MotionStack(
        robot_controller=robot,
        config=MotionStackConfig(layers=frozenset({LAYER_WOBBLE})),
    )
    # Should be safe to call; tracker calls become no-ops.
    stack.feed_assistant_audio(_loud_chunk())
    assert stack.additional_tool_specs() == []


# ---------------------------------------------------------------------------
# Bridge-facing hooks
# ---------------------------------------------------------------------------


def test_feed_assistant_audio_no_op_when_wobble_disabled():
    """Should not raise even though there's no wobbler."""
    robot = _FakeRobotController()
    stack = MotionStack(
        robot_controller=robot,
        config=MotionStackConfig(layers=frozenset({LAYER_GESTURES})),
    )
    stack.feed_assistant_audio(_loud_chunk())  # no exception


def test_handle_tool_call_routes_to_router():
    robot = _FakeRobotController()
    stack = MotionStack(robot_controller=robot)
    payload = stack.handle_tool_call(
        "call_1", PLAY_GESTURE_TOOL_NAME, '{"name": "nod_encourage"}'
    )
    assert payload is not None
    import json
    assert json.loads(payload)["ok"] is True


def test_handle_tool_call_returns_none_when_gestures_disabled():
    robot = _FakeRobotController()
    stack = MotionStack(
        robot_controller=robot,
        config=MotionStackConfig(layers=frozenset({LAYER_WOBBLE, LAYER_TRACKING})),
    )
    assert stack.handle_tool_call("c", PLAY_GESTURE_TOOL_NAME, "{}") is None


def test_enter_exit_assistant_speech_does_not_raise_with_no_layers():
    robot = _FakeRobotController()
    stack = MotionStack(
        robot_controller=robot,
        config=MotionStackConfig(layers=frozenset()),
    )
    # Smoke: every bridge hook callable with no layers.
    stack.enter_assistant_speech()
    stack.exit_assistant_speech()
    stack.enter_child_speech()
    stack.exit_child_speech()
    stack.set_idle()


def test_face_tracker_target_drives_pose_sink():
    """End-to-end smoke: tracker publish → composer tick → robot.apply_pose."""
    robot = _FakeRobotController()
    stack = MotionStack(robot_controller=robot)
    tracker = _FakeTracker()
    stack.attach_face_tracker(tracker)

    # Push a target and tick the composer manually.
    tracker.publish((0.5, 0.0))
    stack._composer.tick_at(0.0)
    stack._composer.tick_at(1.0)  # let slew settle

    assert robot.pose_calls
    last = robot.pose_calls[-1]
    # head_yaw should be > 0 (positive pan -> turn right).
    assert last["head_yaw"] > 0


def test_pose_sink_with_robot_lacking_apply_pose_is_silent():
    class _BareRobot:
        pass

    stack = MotionStack(robot_controller=_BareRobot())
    # Tick should not raise even though apply_pose is missing.
    stack._composer.tick_at(0.0)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_start_then_stop_runs_at_least_one_tick():
    import time as _time

    robot = _FakeRobotController()
    stack = MotionStack(
        robot_controller=robot,
        config=MotionStackConfig(layers=frozenset(), tick_hz=200.0),
    )
    stack.start()
    deadline = _time.monotonic() + 1.0
    while _time.monotonic() < deadline and len(robot.pose_calls) < 3:
        _time.sleep(0.005)
    stack.stop(timeout=1.0)
    assert len(robot.pose_calls) >= 3


def test_stop_without_start_is_safe():
    stack = MotionStack(robot_controller=_FakeRobotController())
    stack.stop()
