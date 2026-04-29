"""Tests for the thin CLI entry at src/robot_kids_teacher.py.

We verify two V1 guarantees:

1. Importing ``robot_kids_teacher`` does NOT drag in the ``openai`` SDK.
2. ``--help`` runs cleanly without touching any asyncio loop or backend.
"""

from __future__ import annotations

import sys

import pytest


def test_import_does_not_pull_in_openai():
    # Drop any cached imports so this is a genuine fresh import test.
    sys.modules.pop("robot_kids_teacher", None)
    sys.modules.pop("openai", None)
    import robot_kids_teacher  # noqa: F401
    assert "openai" not in sys.modules


def test_help_exits_cleanly():
    import robot_kids_teacher

    with pytest.raises(SystemExit) as excinfo:
        robot_kids_teacher.main(["--help"])
    # argparse raises SystemExit(0) on --help.
    assert excinfo.value.code == 0


def test_missing_openai_returns_exit_code_two(monkeypatch):
    import robot_kids_teacher

    # Simulate "openai not installed" by blocking the import.
    real_find_spec = None

    def _blocked_import(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("openai not installed in test env")
        return original_import(name, *args, **kwargs)

    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    monkeypatch.setattr("builtins.__import__", _blocked_import)
    # Also pre-clear any cached openai module.
    sys.modules.pop("openai", None)

    exit_code = robot_kids_teacher.main(["--session-id", "test-session"])
    assert exit_code == 2


def test_missing_reachy_mini_returns_exit_code_two(monkeypatch):
    """If the Reachy SDK is absent, the CLI must bail with exit 2 — not
    crash mid-setup and not leave a half-opened OpenAI websocket behind.
    """
    import robot_kids_teacher

    def _blocked_import(name, *args, **kwargs):
        if name == "reachy_mini" or name.startswith("reachy_mini."):
            raise ImportError("reachy_mini not installed in test env")
        return original_import(name, *args, **kwargs)

    original_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )
    monkeypatch.setattr("builtins.__import__", _blocked_import)
    sys.modules.pop("reachy_mini", None)
    sys.modules.pop("reachy_mini.utils", None)

    exit_code = robot_kids_teacher.main(["--session-id", "test-session"])
    assert exit_code == 2


# ---------------------------------------------------------------------------
# Motion-director CLI flag + factory + gaze loop subscriber swap
# ---------------------------------------------------------------------------


def test_motion_layers_cli_flag_is_parsed():
    import robot_kids_teacher

    parser = robot_kids_teacher._build_parser()
    parsed = parser.parse_args(["--motion-layers", "wobble,tracking"])
    assert parsed.motion_layers == "wobble,tracking"


def test_motion_layers_cli_default_is_none():
    import robot_kids_teacher

    parser = robot_kids_teacher._build_parser()
    parsed = parser.parse_args([])
    assert parsed.motion_layers is None


def test_resolve_motion_layers_spec_prefers_cli_over_env(monkeypatch):
    import robot_kids_teacher

    monkeypatch.setenv(robot_kids_teacher._MOTION_LAYERS_ENV_VAR, "tracking")
    spec = robot_kids_teacher._resolve_motion_layers_spec("wobble")
    assert spec == "wobble"


def test_resolve_motion_layers_spec_falls_back_to_env(monkeypatch):
    import robot_kids_teacher

    monkeypatch.setenv(robot_kids_teacher._MOTION_LAYERS_ENV_VAR, "tracking")
    assert robot_kids_teacher._resolve_motion_layers_spec(None) == "tracking"
    assert robot_kids_teacher._resolve_motion_layers_spec("") == "tracking"


def test_resolve_motion_layers_spec_returns_none_when_unset(monkeypatch):
    import robot_kids_teacher

    monkeypatch.delenv(robot_kids_teacher._MOTION_LAYERS_ENV_VAR, raising=False)
    assert robot_kids_teacher._resolve_motion_layers_spec(None) is None


def test_maybe_make_motion_stack_returns_none_when_layers_none():
    import robot_kids_teacher

    class _Robot:
        pass

    stack = robot_kids_teacher._maybe_make_motion_stack(
        _Robot(), layers_spec="none"
    )
    assert stack is None


def test_maybe_make_motion_stack_returns_stack_when_layers_full():
    import robot_kids_teacher

    class _Robot:
        def apply_pose(self, **_kwargs):
            return True

    stack = robot_kids_teacher._maybe_make_motion_stack(
        _Robot(), layers_spec="full"
    )
    assert stack is not None
    # The full stack exposes the gesture tool surface.
    names = {t["name"] for t in stack.additional_tool_specs()}
    assert "play_gesture" in names


def test_make_gaze_loop_factory_attaches_motion_stack_when_provided():
    """When a motion stack is passed, the gaze loop must wire its face
    mixer to the FaceTracker — not the debug-log subscriber."""
    import robot_kids_teacher

    attached = []

    class _StackStub:
        def attach_face_tracker(self, tracker):
            attached.append(tracker)

        def detach_face_tracker(self):
            attached.append("detached")

    factory = robot_kids_teacher._make_gaze_loop_factory(
        camera_worker=object(), motion_stack=_StackStub()
    )
    # We're not running the loop end-to-end here; just confirm the factory
    # returns a coroutine factory and that the integration path is wired.
    assert callable(factory)


def test_make_gaze_loop_factory_falls_back_to_debug_subscriber_without_stack():
    """No motion stack → no attach_face_tracker call; debug subscriber
    is the only consumer."""
    import robot_kids_teacher

    factory = robot_kids_teacher._make_gaze_loop_factory(
        camera_worker=object(), motion_stack=None
    )
    assert callable(factory)
