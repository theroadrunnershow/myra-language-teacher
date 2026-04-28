"""Unit tests for :mod:`motion.tool_specs`."""

from __future__ import annotations

import json

import pytest

from motion.composer import MovementComposer
from motion.library import ChoreographyLibrary, Clip, default_library
from motion.scheduler import GestureScheduler
from motion.tool_specs import (
    MOTION_TOOL_NAMES,
    PLAY_GESTURE_TOOL_NAME,
    STOP_MOTION_TOOL_NAME,
    GestureToolRouter,
    ToolCallResult,
    gesture_tool_specs,
    gesture_vocabulary_prompt_block,
)
from motion.types import NEUTRAL, PoseOffset


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def _make_router(library: ChoreographyLibrary | None = None):
    library = library or default_library()
    clock = _FakeClock()

    def _sink(_offset: PoseOffset, _period: float) -> None:
        return None

    composer = MovementComposer(pose_sink=_sink, tick_hz=30.0, clock=clock)
    scheduler = GestureScheduler(
        composer=composer,
        library=library,
        clock=clock,
    )
    return GestureToolRouter(scheduler=scheduler), scheduler, clock


# ---------------------------------------------------------------------------
# tool specs
# ---------------------------------------------------------------------------


def test_gesture_tool_specs_has_play_and_stop():
    specs = gesture_tool_specs(default_library())
    names = {s["name"] for s in specs}
    assert names == {PLAY_GESTURE_TOOL_NAME, STOP_MOTION_TOOL_NAME}


def test_play_gesture_enum_lists_every_clip():
    library = default_library()
    specs = gesture_tool_specs(library)
    play = next(s for s in specs if s["name"] == PLAY_GESTURE_TOOL_NAME)
    assert sorted(play["parameters"]["properties"]["name"]["enum"]) == library.names()


def test_play_gesture_requires_name():
    specs = gesture_tool_specs(default_library())
    play = next(s for s in specs if s["name"] == PLAY_GESTURE_TOOL_NAME)
    assert play["parameters"]["required"] == ["name"]
    assert play["parameters"]["additionalProperties"] is False


def test_stop_motion_takes_no_args():
    specs = gesture_tool_specs(default_library())
    stop = next(s for s in specs if s["name"] == STOP_MOTION_TOOL_NAME)
    assert stop["parameters"]["required"] == []
    assert stop["parameters"]["properties"] == {}


def test_motion_tool_names_constant_matches_specs():
    assert set(MOTION_TOOL_NAMES) == {
        s["name"] for s in gesture_tool_specs(default_library())
    }


def test_specs_are_json_serializable():
    """The OpenAI Realtime SDK serialises to JSON — fail loudly if we can't."""
    specs = gesture_tool_specs(default_library())
    json.dumps(specs)


def test_vocabulary_prompt_block_mentions_gestures():
    block = gesture_vocabulary_prompt_block()
    assert "play_gesture" in block
    for name in (
        "nod_encourage",
        "head_tilt_curious",
        "mini_dance_excited",
        "victory_wiggle",
    ):
        assert name in block


# ---------------------------------------------------------------------------
# ToolCallResult
# ---------------------------------------------------------------------------


def test_tool_call_result_to_payload_round_trips():
    result = ToolCallResult(ok=True, detail="playing nod_encourage")
    parsed = json.loads(result.to_payload())
    assert parsed == {"ok": True, "detail": "playing nod_encourage"}


# ---------------------------------------------------------------------------
# GestureToolRouter — happy paths
# ---------------------------------------------------------------------------


def test_play_gesture_with_dict_args_routes_to_scheduler():
    router, _, _ = _make_router()
    result = router.dispatch(PLAY_GESTURE_TOOL_NAME, {"name": "nod_encourage"})
    assert result.ok is True
    assert "nod_encourage" in result.detail


def test_play_gesture_with_json_string_args_routes_to_scheduler():
    """OpenAI Realtime ships function args as JSON strings."""
    router, _, _ = _make_router()
    result = router.dispatch(
        PLAY_GESTURE_TOOL_NAME, json.dumps({"name": "head_tilt_curious"})
    )
    assert result.ok is True


def test_stop_motion_flushes_scheduler():
    router, scheduler, _ = _make_router()
    # Start a gesture so we have something to flush.
    scheduler.request("nod_encourage")
    result = router.dispatch(STOP_MOTION_TOOL_NAME, {})
    assert result.ok is True
    assert "stopped" in result.detail


# ---------------------------------------------------------------------------
# GestureToolRouter — error paths
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_error():
    router, _, _ = _make_router()
    result = router.dispatch("send_email", {"to": "x"})
    assert result.ok is False
    assert "unknown tool" in result.detail


def test_play_gesture_with_unknown_clip_reports_drop_reason():
    router, _, _ = _make_router()
    result = router.dispatch(PLAY_GESTURE_TOOL_NAME, {"name": "tango"})
    assert result.ok is False
    assert "unknown_gesture" in result.detail


def test_play_gesture_with_missing_name_returns_error():
    router, _, _ = _make_router()
    result = router.dispatch(PLAY_GESTURE_TOOL_NAME, {})
    assert result.ok is False
    assert "name" in result.detail


def test_play_gesture_with_non_string_name_returns_error():
    router, _, _ = _make_router()
    result = router.dispatch(PLAY_GESTURE_TOOL_NAME, {"name": 7})
    assert result.ok is False


def test_play_gesture_with_invalid_json_returns_error():
    router, _, _ = _make_router()
    result = router.dispatch(PLAY_GESTURE_TOOL_NAME, "{not json")
    assert result.ok is False
    assert "invalid arguments" in result.detail


def test_play_gesture_with_empty_string_args_returns_error():
    router, _, _ = _make_router()
    result = router.dispatch(PLAY_GESTURE_TOOL_NAME, "")
    assert result.ok is False  # empty -> {} -> missing name


def test_play_gesture_with_bytes_json_args_works():
    router, _, _ = _make_router()
    result = router.dispatch(
        PLAY_GESTURE_TOOL_NAME, b'{"name": "sad_droop"}'
    )
    assert result.ok is True


def test_play_gesture_with_non_object_json_returns_error():
    router, _, _ = _make_router()
    result = router.dispatch(PLAY_GESTURE_TOOL_NAME, "[1, 2, 3]")
    assert result.ok is False


def test_dispatch_never_raises_on_garbage():
    """Tool-router safety: any input must produce a result, never raise."""
    router, _, _ = _make_router()
    for arg in (None, 42, 3.14, object()):
        result = router.dispatch(PLAY_GESTURE_TOOL_NAME, arg)
        assert isinstance(result, ToolCallResult)
        assert result.ok is False


# ---------------------------------------------------------------------------
# Cooldown propagation
# ---------------------------------------------------------------------------


def test_play_gesture_inside_cooldown_reports_drop_reason():
    router, _, clock = _make_router()
    router.dispatch(PLAY_GESTURE_TOOL_NAME, {"name": "nod_encourage"})
    clock.advance(0.5)  # well inside per-name cooldown
    result = router.dispatch(PLAY_GESTURE_TOOL_NAME, {"name": "nod_encourage"})
    assert result.ok is False
    assert "per_name_cooldown" in result.detail
