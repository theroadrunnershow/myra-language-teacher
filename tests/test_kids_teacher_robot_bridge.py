"""Tests for the kids-teacher robot audio bridge.

These tests run entirely without the Reachy SDK: we stub ``reachy_mini`` and
``reachy_mini.utils`` in ``sys.modules`` before importing the bridge, and
inject a ``FakeRobotController`` for all animation + playback assertions.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from typing import List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub the optional robot SDK BEFORE importing the module under test so that
# importing kids_teacher_robot_bridge works in a clean environment. This
# mirrors the stubbing pattern used in tests/conftest.py for faster_whisper.
# ---------------------------------------------------------------------------
if "reachy_mini" not in sys.modules:
    sys.modules["reachy_mini"] = MagicMock()
if "reachy_mini.utils" not in sys.modules:
    sys.modules["reachy_mini.utils"] = MagicMock()


from kids_teacher_flow import build_robot_hooks  # noqa: E402
from kids_teacher_robot_bridge import (  # noqa: E402
    KidsTeacherRobotHooks,
    pump_microphone_to_backend,
)
from kids_teacher_types import (  # noqa: E402
    KidsStatusEvent,
    KidsTranscriptEvent,
    SessionStatus,
    Speaker,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeRobotController:
    """Records animation calls without touching the real SDK."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.output_sample_rate = 24000

    def listen(self) -> None:
        self.calls.append("listen")

    def speak(self) -> None:
        self.calls.append("speak")

    def idle(self) -> None:
        self.calls.append("idle")


class RecordingPlayChunk:
    """Playback callable that records invocations instead of touching HW."""

    def __init__(self) -> None:
        self.chunks: List[bytes] = []
        self.lock = threading.Lock()

    def __call__(self, robot_controller, audio_bytes: bytes, sample_rate: int) -> None:
        with self.lock:
            self.chunks.append(audio_bytes)


class FakeHandler:
    """Minimal ``KidsTeacherRealtimeHandler`` stand-in for the mic pump."""

    def __init__(self) -> None:
        self.pushed: List[bytes] = []

    async def push_audio(self, chunk: bytes) -> None:
        self.pushed.append(chunk)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_hooks(
    play_chunk: Optional[RecordingPlayChunk] = None,
) -> Tuple[KidsTeacherRobotHooks, FakeRobotController, RecordingPlayChunk]:
    robot = FakeRobotController()
    recorder = play_chunk or RecordingPlayChunk()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        sample_rate=24000,
        play_chunk=recorder,
    )
    return hooks, robot, recorder


def _status(status: SessionStatus, detail: Optional[str] = None) -> KidsStatusEvent:
    return KidsStatusEvent(
        status=status,
        session_id="s1",
        timestamp_ms=0,
        detail=detail,
    )


def _transcript(
    speaker: Speaker = Speaker.CHILD, text: str = "hi", is_partial: bool = False
) -> KidsTranscriptEvent:
    return KidsTranscriptEvent(
        speaker=speaker,
        text=text,
        is_partial=is_partial,
        timestamp_ms=0,
        session_id="s1",
        language="english",
    )


def _wait_until(predicate, timeout: float = 1.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Playback queue + animation behavior
# ---------------------------------------------------------------------------


def test_start_assistant_playback_triggers_speak_on_first_chunk():
    hooks, robot, _ = _make_hooks()

    hooks.start_assistant_playback(b"\x00\x01")

    assert robot.calls == ["speak"]


def test_start_assistant_playback_is_idempotent_for_speak_animation():
    hooks, robot, _ = _make_hooks()

    hooks.start_assistant_playback(b"\x00\x01")
    hooks.start_assistant_playback(b"\x02\x03")
    hooks.start_assistant_playback(b"\x04\x05")

    # Only one speak() call across three chunks in the same turn.
    assert robot.calls.count("speak") == 1


def test_stop_assistant_playback_clears_queue_and_returns_to_listen():
    hooks, robot, recorder = _make_hooks()

    # Load several chunks but do NOT start the thread yet — they should be
    # dropped on stop without ever being played.
    hooks.start_assistant_playback(b"\x00\x01")
    hooks.start_assistant_playback(b"\x02\x03")

    hooks.stop_assistant_playback()

    # Queue was cleared before any playback thread ran.
    assert recorder.chunks == []
    # Animation must have returned to listening.
    assert robot.calls[-1] == "listen"
    # A fresh chunk after stop should re-trigger speak (speaking flag reset).
    hooks.start_assistant_playback(b"\x10\x11")
    assert robot.calls.count("speak") == 2


# ---------------------------------------------------------------------------
# Status → animation mapping
# ---------------------------------------------------------------------------


def test_publish_status_listening_triggers_listen_animation():
    hooks, robot, _ = _make_hooks()

    hooks.publish_status(_status(SessionStatus.LISTENING))

    assert robot.calls == ["listen"]


def test_publish_status_ended_triggers_idle_animation():
    hooks, robot, _ = _make_hooks()

    hooks.publish_status(_status(SessionStatus.ENDED, detail="all done"))

    assert robot.calls == ["idle"]


def test_publish_status_thinking_is_noop():
    hooks, robot, _ = _make_hooks()

    hooks.publish_status(_status(SessionStatus.THINKING))

    assert robot.calls == []


# ---------------------------------------------------------------------------
# Transcript + persistence behavior
# ---------------------------------------------------------------------------


def test_publish_transcript_does_not_play_audio():
    hooks, robot, recorder = _make_hooks()

    hooks.publish_transcript(_transcript(Speaker.ASSISTANT, "Hello there"))

    assert recorder.chunks == []
    # No animation side-effect from logging a transcript.
    assert robot.calls == []


def test_persist_artifact_is_noop():
    hooks, robot, recorder = _make_hooks()

    # Must not raise and must not touch the robot or playback.
    hooks.persist_artifact(_transcript(), audio=b"raw-audio")

    assert robot.calls == []
    assert recorder.chunks == []


# ---------------------------------------------------------------------------
# Thread lifecycle + actual playback
# ---------------------------------------------------------------------------


def test_playback_thread_starts_and_stops_cleanly():
    hooks, _, _ = _make_hooks()

    hooks.start()
    assert hooks._thread is not None
    assert hooks._thread.is_alive()

    t0 = time.monotonic()
    hooks.stop(timeout=1.0)
    elapsed = time.monotonic() - t0

    # Should exit well before the timeout — if it deadlocks this blows up.
    assert elapsed < 1.0
    assert hooks._thread is None


def test_playback_thread_drains_queued_chunks():
    hooks, _, recorder = _make_hooks()
    hooks.start()
    try:
        hooks.start_assistant_playback(b"chunk-1")
        hooks.start_assistant_playback(b"chunk-2")

        assert _wait_until(lambda: len(recorder.chunks) == 2, timeout=1.0)
        assert recorder.chunks == [b"chunk-1", b"chunk-2"]
    finally:
        hooks.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# pump_microphone_to_backend
# ---------------------------------------------------------------------------


async def test_mic_pump_forwards_generator_chunks_until_exhausted():
    handler = FakeHandler()

    def mic_gen():
        yield b"\x01\x02"
        yield b"\x03\x04"
        yield b"\x05\x06"

    await pump_microphone_to_backend(handler, mic_source=mic_gen())

    assert handler.pushed == [b"\x01\x02", b"\x03\x04", b"\x05\x06"]


async def test_mic_pump_stops_when_stop_event_is_set():
    handler = FakeHandler()
    stop_event = asyncio.Event()

    call_count = {"n": 0}

    def reader() -> bytes:
        call_count["n"] += 1
        if call_count["n"] >= 3:
            stop_event.set()
        return b"\xaa\xbb"

    await asyncio.wait_for(
        pump_microphone_to_backend(
            handler, mic_source=reader, stop_event=stop_event
        ),
        timeout=1.0,
    )

    # Loop should stop promptly; allow for one extra push before the flag
    # is checked on the next iteration.
    assert len(handler.pushed) <= 3
    assert stop_event.is_set()


async def test_mic_pump_treats_empty_bytes_as_end_of_stream():
    handler = FakeHandler()

    chunks = [b"\x01\x02", b""]

    def reader() -> bytes:
        return chunks.pop(0)

    await pump_microphone_to_backend(handler, mic_source=reader)

    assert handler.pushed == [b"\x01\x02"]


async def test_mic_pump_handles_callable_that_raises_stopiteration():
    handler = FakeHandler()

    gen = iter([b"\x01\x02"])

    await pump_microphone_to_backend(handler, mic_source=gen)

    assert handler.pushed == [b"\x01\x02"]


# ---------------------------------------------------------------------------
# Import robustness
# ---------------------------------------------------------------------------


def test_module_imports_without_reachy_sdk(monkeypatch):
    """Re-importing the bridge with reachy_mini absent must still succeed.

    The bridge deliberately defers any robot-SDK import to the playback
    callable, so this test confirms the module is safe in SDK-less envs
    (CI, cloud server, pre-commit hooks, etc.).
    """
    # Drop cached copies so the reload path is exercised fresh.
    monkeypatch.delitem(sys.modules, "reachy_mini", raising=False)
    monkeypatch.delitem(sys.modules, "reachy_mini.utils", raising=False)
    monkeypatch.delitem(sys.modules, "kids_teacher_robot_bridge", raising=False)

    import importlib

    module = importlib.import_module("kids_teacher_robot_bridge")

    assert hasattr(module, "KidsTeacherRobotHooks")
    assert hasattr(module, "pump_microphone_to_backend")


# ---------------------------------------------------------------------------
# build_robot_hooks() factory in kids_teacher_flow
# ---------------------------------------------------------------------------


def test_build_robot_hooks_returns_concrete_bridge():
    robot = FakeRobotController()

    hooks = build_robot_hooks(robot)

    assert isinstance(hooks, KidsTeacherRobotHooks)
    # Wiring sanity check — the new hooks talk to the robot we passed in.
    hooks.publish_status(_status(SessionStatus.LISTENING))
    assert robot.calls == ["listen"]


def test_build_robot_hooks_stub_still_raises_for_backcompat():
    from kids_teacher_flow import build_robot_hooks_stub

    with pytest.raises(NotImplementedError):
        build_robot_hooks_stub(object())
