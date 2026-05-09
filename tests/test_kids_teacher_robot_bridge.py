"""Tests for the kids-teacher robot audio bridge.

These tests run entirely without the Reachy SDK: we stub ``reachy_mini`` and
``reachy_mini.utils`` in ``sys.modules`` before importing the bridge, and
inject a ``FakeRobotController`` for all animation + playback assertions.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import types
from typing import List, Optional, Tuple
from unittest.mock import MagicMock

import numpy as np
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
    _default_play_chunk,
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
        self.prime_calls = 0
        self.streamed_audio = None
        self.played_audio = None
        self.suppress_speak_anim: Optional[bool] = None
        self.flush_calls = 0

    def listen(self) -> None:
        self.calls.append("listen")

    def speak(self) -> None:
        self.calls.append("speak")

    def idle(self) -> None:
        self.calls.append("idle")

    def prime_speaker(self) -> None:
        self.prime_calls += 1
        self.calls.append("prime_speaker")

    def play_audio(self, samples, suppress_speak_anim: bool = False) -> None:
        # Retained for any legacy call-sites; the streaming bridge path
        # should prefer play_audio_streaming().
        self.played_audio = samples
        self.suppress_speak_anim = suppress_speak_anim

    def play_audio_streaming(self, samples) -> None:
        self.streamed_audio = samples
        self.calls.append("play_audio_streaming")

    def flush_output_audio(self) -> None:
        self.flush_calls += 1
        self.calls.append("flush_output_audio")


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


def test_default_play_chunk_decodes_pcm16_and_resamples(monkeypatch):
    robot = FakeRobotController()
    robot.output_sample_rate = 16000

    calls = {}

    def fake_to_float32_audio(samples):
        calls["to_float32_audio"] = np.array(samples, copy=True)
        return samples.astype(np.float32) / 32767.0

    def fake_resample_audio(samples, src_rate: int, dst_rate: int):
        calls["resample"] = (np.array(samples, copy=True), src_rate, dst_rate)
        return np.array([0.25, -0.25], dtype=np.float32)

    fake_robot_teacher = types.SimpleNamespace(
        _to_float32_audio=fake_to_float32_audio,
        _resample_audio=fake_resample_audio,
    )
    monkeypatch.setitem(sys.modules, "robot_teacher", fake_robot_teacher)

    audio_bytes = np.array([1000, -1000, 2000], dtype="<i2").tobytes()
    _default_play_chunk(robot, audio_bytes, 24000)

    np.testing.assert_array_equal(
        calls["to_float32_audio"],
        np.array([1000, -1000, 2000], dtype=np.int16),
    )
    assert calls["resample"][1:] == (24000, 16000)
    # Decoded samples must reach the robot via the STREAMING path so they
    # concatenate without the 150ms tail sleep imposed by play_audio.
    np.testing.assert_array_equal(
        robot.streamed_audio,
        np.array([[0.25], [-0.25]], dtype=np.float32),
    )
    # One-shot play_audio must NOT be used for streaming deltas.
    assert robot.played_audio is None


def test_default_play_chunk_stretches_output_rate_when_speed_below_one(monkeypatch):
    """playback_speed=0.8 must bump dst_rate to output_rate / 0.8 so the
    speaker gets 1.25x the samples and plays the audio 20% slower.
    """
    robot = FakeRobotController()
    robot.output_sample_rate = 16000

    captured = {}

    def fake_to_float32_audio(samples):
        return samples.astype(np.float32) / 32767.0

    def fake_resample_audio(samples, src_rate: int, dst_rate: int):
        captured["rates"] = (src_rate, dst_rate)
        return np.zeros(4, dtype=np.float32)

    monkeypatch.setitem(
        sys.modules,
        "robot_teacher",
        types.SimpleNamespace(
            _to_float32_audio=fake_to_float32_audio,
            _resample_audio=fake_resample_audio,
        ),
    )

    audio_bytes = np.array([0, 1, -1], dtype="<i2").tobytes()
    _default_play_chunk(robot, audio_bytes, 24000, playback_speed=0.8)

    assert captured["rates"] == (24000, 20000)


def test_default_play_chunk_ignores_invalid_speed(monkeypatch):
    """Non-positive speed must NOT reach _resample_audio — resample would
    raise or produce garbage. Falls back to the native output_sample_rate.
    """
    robot = FakeRobotController()
    robot.output_sample_rate = 16000

    captured = {}

    def fake_resample_audio(samples, src_rate: int, dst_rate: int):
        captured["rates"] = (src_rate, dst_rate)
        return np.zeros(2, dtype=np.float32)

    monkeypatch.setitem(
        sys.modules,
        "robot_teacher",
        types.SimpleNamespace(
            _to_float32_audio=lambda s: s.astype(np.float32),
            _resample_audio=fake_resample_audio,
        ),
    )

    audio_bytes = np.array([0, 1], dtype="<i2").tobytes()
    _default_play_chunk(robot, audio_bytes, 24000, playback_speed=0.0)

    assert captured["rates"] == (24000, 16000)


def test_hooks_thread_playback_speed_through_to_default_player(monkeypatch):
    """Constructing KidsTeacherRobotHooks with playback_speed != 1.0 must
    flow that speed into the default _default_play_chunk invocation on the
    playback thread (the default, non-injected path).
    """
    import kids_teacher_robot_bridge as bridge

    captured: List[Tuple[int, float]] = []

    def fake_default_play_chunk(robot, audio_bytes, sample_rate, *, playback_speed=1.0):
        captured.append((sample_rate, playback_speed))

    monkeypatch.setattr(bridge, "_default_play_chunk", fake_default_play_chunk)

    robot = FakeRobotController()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        sample_rate=24000,
        playback_speed=0.8,
    )
    hooks.start()
    try:
        hooks.start_assistant_playback(b"chunk-1")
        assert _wait_until(lambda: len(captured) == 1, timeout=1.0)
    finally:
        hooks.stop(timeout=1.0)

    assert captured[0] == (24000, 0.8)


def test_bridge_start_primes_speaker_eagerly():
    """Priming at bridge.start() means the first audible delta doesn't pay
    the ~0.3s warmup cost on the critical path."""
    hooks, robot, _ = _make_hooks()

    hooks.start()
    try:
        assert robot.prime_calls == 1
    finally:
        hooks.stop(timeout=1.0)


def test_stop_assistant_playback_flushes_speaker_pipeline():
    """Barge-in must flush audio already queued in the speaker sink, not
    just the bridge-level deque — otherwise the child hears the tail of
    the assistant response for several seconds after interrupting."""
    hooks, robot, _ = _make_hooks()

    hooks.start_assistant_playback(b"\x00\x01")
    hooks.stop_assistant_playback()

    assert robot.flush_calls == 1
    # Flush must happen before returning to listen so the animation state
    # transition lines up with actual silence.
    flush_idx = robot.calls.index("flush_output_audio")
    listen_idx = robot.calls.index("listen")
    assert flush_idx < listen_idx


def test_stop_assistant_playback_tolerates_controller_without_flush():
    """Backwards-safe: if a controller (e.g. an older stub) does not
    expose flush_output_audio, stop_assistant_playback must still return
    cleanly and still transition to listen."""

    class ControllerWithoutFlush:
        def __init__(self) -> None:
            self.calls: List[str] = []
            self.output_sample_rate = 24000

        def listen(self) -> None:
            self.calls.append("listen")

        def speak(self) -> None:
            self.calls.append("speak")

        def prime_speaker(self) -> None:
            self.calls.append("prime_speaker")

    robot = ControllerWithoutFlush()
    recorder = RecordingPlayChunk()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        sample_rate=24000,
        play_chunk=recorder,
    )

    hooks.start_assistant_playback(b"\x00\x01")
    hooks.stop_assistant_playback()  # must not raise

    assert "listen" in robot.calls


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


async def test_mic_pump_treats_none_as_retry_not_end_of_stream():
    """``None`` from mic_source must not end the stream — pump polls again.

    The robot API ``mini.media.get_audio_sample()`` returns ``None`` between
    frames; treating that as end-of-stream would tear the session down on
    the first quiet moment.
    """
    handler = FakeHandler()
    stop_event = asyncio.Event()

    schedule = [None, None, b"\x01\x02", None, b"\x03\x04"]
    index = {"i": 0}

    def reader():
        i = index["i"]
        index["i"] = i + 1
        if i >= len(schedule):
            stop_event.set()
            return None
        return schedule[i]

    await asyncio.wait_for(
        pump_microphone_to_backend(
            handler, mic_source=reader, stop_event=stop_event
        ),
        timeout=1.0,
    )

    assert handler.pushed == [b"\x01\x02", b"\x03\x04"]
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


def test_build_robot_hooks_honors_playback_speed_env(monkeypatch):
    """KIDS_TEACHER_PLAYBACK_SPEED must flow into the constructed hooks and
    out to the default play_chunk path.
    """
    import kids_teacher_robot_bridge as bridge

    captured: List[float] = []

    def fake_default_play_chunk(robot, audio_bytes, sample_rate, *, playback_speed=1.0):
        captured.append(playback_speed)

    monkeypatch.setattr(bridge, "_default_play_chunk", fake_default_play_chunk)
    monkeypatch.setenv("KIDS_TEACHER_PLAYBACK_SPEED", "0.8")

    hooks = build_robot_hooks(FakeRobotController())
    hooks.start()
    try:
        hooks.start_assistant_playback(b"chunk")
        assert _wait_until(lambda: len(captured) == 1, timeout=1.0)
    finally:
        hooks.stop(timeout=1.0)

    assert captured[0] == 0.8


@pytest.mark.parametrize("bad_value", ["nope", "0.1", "5.0", ""])
def test_build_robot_hooks_falls_back_on_bad_speed_env(monkeypatch, bad_value):
    """Unparseable or out-of-range values must fall back to 1.0 (native)."""
    import kids_teacher_robot_bridge as bridge

    captured: List[float] = []

    def fake_default_play_chunk(robot, audio_bytes, sample_rate, *, playback_speed=1.0):
        captured.append(playback_speed)

    monkeypatch.setattr(bridge, "_default_play_chunk", fake_default_play_chunk)
    if bad_value:
        monkeypatch.setenv("KIDS_TEACHER_PLAYBACK_SPEED", bad_value)
    else:
        monkeypatch.delenv("KIDS_TEACHER_PLAYBACK_SPEED", raising=False)

    hooks = build_robot_hooks(FakeRobotController())
    hooks.start()
    try:
        hooks.start_assistant_playback(b"chunk")
        assert _wait_until(lambda: len(captured) == 1, timeout=1.0)
    finally:
        hooks.stop(timeout=1.0)

    assert captured[0] == 1.0


# ---------------------------------------------------------------------------
# RECONNECTING status → recovery cue (E)
#
# When the Gemini Live session drops at the 10-min ceiling and the backend
# rebuilds it, the bridge must (a) drop any chunks queued against the dead
# socket, (b) restore the listening pose so the robot doesn't appear frozen
# mid-speak, and (c) fire the injected recovery cue off-thread so the slow
# TTS fetch can't block the realtime handler.
# ---------------------------------------------------------------------------


def test_publish_status_reconnecting_clears_queue_and_returns_to_listen():
    hooks, robot, _ = _make_hooks()
    hooks.start_assistant_playback(b"\x00\x01")  # arms speaking_active
    hooks.start_assistant_playback(b"\x02\x03")

    hooks.publish_status(_status(SessionStatus.RECONNECTING, detail="dropped"))

    assert robot.flush_calls == 1
    assert robot.calls[-1] == "listen"
    # Once the robot is back in LISTENING, a brand-new chunk after reconnect
    # should re-arm the speak animation (speaking_active flag was cleared).
    hooks.start_assistant_playback(b"\x10\x11")
    assert robot.calls.count("speak") == 2


def test_publish_status_reconnecting_invokes_recovery_cue_off_thread():
    """The cue runs in a daemon thread so a slow TTS fetch can't stall the
    event loop. We assert (a) the cue ran with the robot controller and
    (b) it ran on a thread that's NOT the caller's thread."""
    cue_calls: List[Tuple[object, int]] = []
    cue_started = threading.Event()
    release = threading.Event()
    caller_thread_id = threading.get_ident()

    def slow_cue(robot: object) -> None:
        cue_started.set()
        release.wait(timeout=2.0)
        cue_calls.append((robot, threading.get_ident()))

    robot = FakeRobotController()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        play_chunk=RecordingPlayChunk(),
        recovery_cue=slow_cue,
    )

    hooks.publish_status(_status(SessionStatus.RECONNECTING))

    # publish_status returned without waiting for the cue — it must be
    # running on its own thread.
    assert cue_started.wait(timeout=1.0)
    assert cue_calls == []  # still blocked on `release`
    release.set()
    assert _wait_until(lambda: len(cue_calls) == 1, timeout=1.0)
    cue_robot, cue_thread = cue_calls[0]
    assert cue_robot is robot
    assert cue_thread != caller_thread_id


def test_publish_status_reconnecting_without_cue_is_no_op():
    """recovery_cue defaults to None — bridge must still publish the
    listening pose without raising."""
    hooks, robot, _ = _make_hooks()
    hooks.publish_status(_status(SessionStatus.RECONNECTING))
    assert robot.calls[-1] == "listen"


def test_publish_status_reconnecting_swallows_cue_exceptions():
    """A broken cue must not crash the bridge thread or propagate out."""
    def broken_cue(_robot: object) -> None:
        raise RuntimeError("simulated TTS outage")

    robot = FakeRobotController()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        play_chunk=RecordingPlayChunk(),
        recovery_cue=broken_cue,
    )

    # Must return without raising; the exception is logged and swallowed
    # inside the worker thread.
    hooks.publish_status(_status(SessionStatus.RECONNECTING))
    assert robot.calls[-1] == "listen"


def test_build_robot_hooks_wires_default_recovery_cue():
    """build_robot_hooks must inject a default cue so that on a real robot
    deployment a session drop produces audible recovery feedback rather
    than silence. The cue's body is exercised in the flow-level test below.
    """
    hooks = build_robot_hooks(FakeRobotController())
    assert hooks._recovery_cue is not None  # type: ignore[attr-defined]


def test_default_recovery_cue_calls_dino_voice_and_plays(monkeypatch):
    """The default cue should fetch a short 'one moment' line via the
    local TTS (api_get_dino_voice) and play it through the robot speaker.
    Mocks: monkeypatch the robot_teacher import so no HTTP / SDK is hit.
    """
    import kids_teacher_flow as flow

    fake_module = types.ModuleType("robot_teacher")
    fetched_text: List[str] = []
    played: List[Tuple[object, bytes]] = []

    def fake_api_get_dino_voice(text: str) -> bytes:
        fetched_text.append(text)
        return b"FAKEMP3"

    def fake_play(robot, mp3_bytes):
        played.append((robot, mp3_bytes))

    fake_module.api_get_dino_voice = fake_api_get_dino_voice  # type: ignore[attr-defined]
    fake_module._play = fake_play  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "robot_teacher", fake_module)

    robot = FakeRobotController()
    flow._default_recovery_cue(robot)

    assert fetched_text == [flow._RECOVERY_CUE_TEXT]
    assert played == [(robot, b"FAKEMP3")]


def test_default_recovery_cue_swallows_tts_failure(monkeypatch):
    """A network outage on the TTS fetch must not raise out of the cue —
    the recovery path is best-effort, never fatal to the session."""
    import kids_teacher_flow as flow

    fake_module = types.ModuleType("robot_teacher")

    def boom(_text: str) -> bytes:
        raise ConnectionError("simulated TTS outage")

    fake_module.api_get_dino_voice = boom  # type: ignore[attr-defined]
    fake_module._play = lambda r, b: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "robot_teacher", fake_module)

    flow._default_recovery_cue(FakeRobotController())  # must not raise


def test_default_recovery_cue_swallows_missing_robot_teacher(monkeypatch):
    """If robot_teacher (and its audio deps) isn't installed on the host —
    e.g. a stripped CI image — the cue must silently no-op rather than
    crash the bridge."""
    import kids_teacher_flow as flow

    monkeypatch.setitem(sys.modules, "robot_teacher", None)
    # importlib raises if a module is bound to None; emulate that path
    # by deleting + setting up an import that fails.
    sys.modules.pop("robot_teacher", None)

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fail_import(name, *args, **kwargs):
        if name == "robot_teacher":
            raise ImportError("robot_teacher not available on this host")
        return real_import(name, *args, **kwargs)

    if isinstance(__builtins__, dict):
        __builtins__["__import__"] = fail_import
    else:
        __builtins__.__import__ = fail_import  # type: ignore[assignment]
    try:
        flow._default_recovery_cue(FakeRobotController())  # must not raise
    finally:
        if isinstance(__builtins__, dict):
            __builtins__["__import__"] = real_import
        else:
            __builtins__.__import__ = real_import  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Motion-stack integration (motion-director wiring)
# ---------------------------------------------------------------------------


class _RecordingMotionStack:
    """Minimal MotionStack stand-in that records every bridge-facing call."""

    def __init__(
        self,
        *,
        tool_payload: Optional[str] = '{"ok": true}',
        tool_raises: bool = False,
    ) -> None:
        self.calls: List[str] = []
        self.audio_chunks: List[bytes] = []
        self.tool_calls: List[Tuple[str, str, str]] = []
        self.tool_payload = tool_payload
        self.tool_raises = tool_raises
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop(self, timeout: float = 1.0) -> None:
        self.stop_calls += 1

    def feed_assistant_audio(self, chunk: bytes) -> None:
        self.audio_chunks.append(chunk)

    def enter_assistant_speech(self) -> None:
        self.calls.append("enter_assistant_speech")

    def exit_assistant_speech(self) -> None:
        self.calls.append("exit_assistant_speech")

    def enter_child_speech(self) -> None:
        self.calls.append("enter_child_speech")

    def exit_child_speech(self) -> None:
        self.calls.append("exit_child_speech")

    def set_idle(self) -> None:
        self.calls.append("set_idle")

    def handle_tool_call(self, call_id: str, name: str, arguments: str) -> Optional[str]:
        self.tool_calls.append((call_id, name, arguments))
        if self.tool_raises:
            raise RuntimeError("stack boom")
        return self.tool_payload


def _make_hooks_with_stack(stack):
    robot = FakeRobotController()
    recorder = RecordingPlayChunk()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        sample_rate=24000,
        play_chunk=recorder,
        motion_stack=stack,
    )
    return hooks, robot, recorder


def test_motion_stack_replaces_legacy_speak_listen_idle_calls():
    """When a stack is wired the bridge must NOT call robot.speak/listen/idle —
    the composer is the sole motor driver."""
    stack = _RecordingMotionStack()
    hooks, robot, _ = _make_hooks_with_stack(stack)

    hooks.start_assistant_playback(b"\x00\x01")
    hooks.stop_assistant_playback()
    hooks.publish_status(_status(SessionStatus.IDLE))

    # The legacy animation methods must not appear in the call log.
    legacy = {"speak", "listen", "idle"}
    assert legacy.isdisjoint(set(robot.calls))


def test_motion_stack_enter_assistant_on_first_chunk_only():
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)

    hooks.start_assistant_playback(b"\x00\x01")
    hooks.start_assistant_playback(b"\x02\x03")
    hooks.start_assistant_playback(b"\x04\x05")

    # Three chunks fed; only one enter_assistant_speech.
    assert stack.audio_chunks == [b"\x00\x01", b"\x02\x03", b"\x04\x05"]
    assert stack.calls.count("enter_assistant_speech") == 1


def test_motion_stack_exit_assistant_on_stop():
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)

    hooks.start_assistant_playback(b"\x00\x01")
    hooks.stop_assistant_playback()

    assert stack.calls[-1] == "exit_assistant_speech"


def test_motion_stack_status_listening_routes_to_exit_assistant():
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)

    hooks.publish_status(_status(SessionStatus.LISTENING))
    assert stack.calls == ["exit_assistant_speech"]


def test_motion_stack_status_speaking_routes_to_enter_assistant():
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)

    hooks.publish_status(_status(SessionStatus.SPEAKING))
    assert stack.calls == ["enter_assistant_speech"]


def test_motion_stack_status_idle_and_ended_route_to_set_idle():
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)

    hooks.publish_status(_status(SessionStatus.IDLE))
    hooks.publish_status(_status(SessionStatus.ENDED))
    hooks.publish_status(_status(SessionStatus.ERROR))

    assert stack.calls == ["set_idle", "set_idle", "set_idle"]


def test_motion_stack_reconnecting_clears_speaking_and_exits_assistant():
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)

    hooks.start_assistant_playback(b"\x00\x01")
    stack.calls.clear()  # ignore the enter call

    hooks.publish_status(_status(SessionStatus.RECONNECTING, detail="testing"))

    assert "exit_assistant_speech" in stack.calls


def test_motion_stack_listening_resets_speaking_flag_for_next_turn():
    """Normal turn end (LISTENING transition, no barge-in / reconnect) must
    reset the speaking-active flag so the NEXT turn's first audio chunk
    re-fires enter_assistant_speech. Without the reset the composer stays
    pinned in "listen" and the face mixer never enters "robot_speaking" on
    subsequent turns — see kids_teacher_robot_bridge.publish_status."""
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)

    # Turn 1: first chunk fires enter_assistant_speech once.
    hooks.start_assistant_playback(b"\x00\x01")
    assert stack.calls.count("enter_assistant_speech") == 1

    # Normal turn completion — no barge-in (stop_assistant_playback) and no
    # reconnect, just the realtime handler reporting LISTENING after
    # response.done.
    hooks.publish_status(_status(SessionStatus.LISTENING))

    # Turn 2: a brand-new assistant chunk must re-arm the speak state.
    hooks.start_assistant_playback(b"\x02\x03")
    assert stack.calls.count("enter_assistant_speech") == 2


def test_on_speech_started_routes_to_enter_child_speech():
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)

    hooks.on_speech_started()

    assert stack.calls == ["enter_child_speech"]


def test_on_speech_stopped_routes_to_exit_child_speech():
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)

    hooks.on_speech_stopped()

    assert stack.calls == ["exit_child_speech"]


def test_handle_tool_call_routes_to_stack_and_returns_payload():
    stack = _RecordingMotionStack(tool_payload='{"ok": true, "detail": "ok"}')
    hooks, _, _ = _make_hooks_with_stack(stack)

    payload = hooks.handle_tool_call("c1", "play_gesture", '{"name": "nod"}')

    assert stack.tool_calls == [("c1", "play_gesture", '{"name": "nod"}')]
    assert payload == '{"ok": true, "detail": "ok"}'


def test_handle_tool_call_returns_none_when_no_motion_stack():
    hooks, _, _ = _make_hooks()
    assert hooks.handle_tool_call("c1", "play_gesture", "{}") is None


def test_handle_tool_call_swallows_stack_exception_and_returns_none():
    stack = _RecordingMotionStack(tool_raises=True)
    hooks, _, _ = _make_hooks_with_stack(stack)
    payload = hooks.handle_tool_call("c1", "play_gesture", "{}")
    assert payload is None


def test_motion_stack_lifecycle_called_on_start_and_stop():
    stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_stack(stack)
    hooks.start()
    try:
        assert stack.start_calls == 1
    finally:
        hooks.stop()
    assert stack.stop_calls >= 1


def test_motion_stack_absent_preserves_legacy_speak_listen_calls():
    """Smoke: no regression for the kill-switch path."""
    hooks, robot, _ = _make_hooks()  # no motion_stack
    hooks.start_assistant_playback(b"\x00\x01")
    hooks.stop_assistant_playback()
    assert "speak" in robot.calls
    assert "listen" in robot.calls


# ---------------------------------------------------------------------------
# Tools-framework registry mount (Step 4 of plan-tools-framework.md)
# ---------------------------------------------------------------------------


from tools.base import ToolRegistry, ToolResult


class _FakeRegistryTool:
    """Stand-in ``Tool`` for registry-mounting tests."""

    def __init__(
        self,
        name: str,
        *,
        prompt: str = "",
        result: Optional[ToolResult] = None,
        raises: Optional[Exception] = None,
    ) -> None:
        self.name = name
        self._prompt = prompt
        self._result = result or ToolResult(ok=True, detail=f"{name} ok")
        self._raises = raises
        self.calls: List[dict] = []

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": f"{self.name} test tool",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }

    def prompt_block(self) -> str:
        return self._prompt

    async def call(self, arguments):
        self.calls.append(dict(arguments))
        if self._raises is not None:
            raise self._raises
        return self._result


def _make_hooks_with_registry(*, registry, motion_stack=None):
    robot = FakeRobotController()
    recorder = RecordingPlayChunk()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        sample_rate=24000,
        play_chunk=recorder,
        motion_stack=motion_stack,
        tool_registry=registry,
    )
    return hooks, robot, recorder


async def test_handle_tool_call_routes_unknown_motion_name_to_registry():
    """A tool name that motion doesn't own falls through to the registry."""
    tool = _FakeRegistryTool(
        "register_current_location",
        result=ToolResult(
            ok=True, detail="ok", data={"location": "Seattle, WA 98177"}
        ),
    )
    registry = ToolRegistry([tool])
    motion_stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_registry(
        registry=registry, motion_stack=motion_stack
    )

    awaitable = hooks.handle_tool_call(
        "c1", "register_current_location", '{"location": "Seattle"}'
    )
    payload = await awaitable

    assert tool.calls == [{"location": "Seattle"}]
    assert payload is not None
    body = json.loads(payload)
    assert body["ok"] is True
    assert body["location"] == "Seattle, WA 98177"
    # Motion stack must NOT see registry tool calls.
    assert motion_stack.tool_calls == []


async def test_handle_tool_call_motion_name_takes_precedence_over_registry():
    """If a motion tool name is invoked, motion handles it sync — even
    when the registry also exposes a same-named tool. Motion's tighter
    integration with the bridge is the reason §3.3 (a) keeps it
    separate from the registry."""
    motion_stack = _RecordingMotionStack(tool_payload='{"ok": true, "detail": "motion"}')
    # Registry shadows the motion name on purpose to prove precedence.
    shadow = _FakeRegistryTool("play_gesture")
    registry = ToolRegistry([shadow])
    hooks, _, _ = _make_hooks_with_registry(
        registry=registry, motion_stack=motion_stack
    )

    payload = hooks.handle_tool_call("c1", "play_gesture", '{"name": "nod"}')

    assert payload == '{"ok": true, "detail": "motion"}'
    assert motion_stack.tool_calls == [("c1", "play_gesture", '{"name": "nod"}')]
    assert shadow.calls == []


async def test_handle_tool_call_unknown_name_falls_through_to_registry():
    """A name owned by neither motion nor the registry routes to the
    registry's unknown-tool error payload (motion isn't asked)."""
    tool = _FakeRegistryTool("register_current_location")
    registry = ToolRegistry([tool])
    motion_stack = _RecordingMotionStack()
    hooks, _, _ = _make_hooks_with_registry(
        registry=registry, motion_stack=motion_stack
    )

    payload = await hooks.handle_tool_call("c1", "totally_unknown", "{}")

    assert motion_stack.tool_calls == []
    assert payload is not None
    body = json.loads(payload)
    assert body["ok"] is False
    assert "unknown tool" in body["detail"]


async def test_handle_tool_call_registry_dispatch_exception_returns_none():
    """An exception inside the registry's dispatch must not crash the bridge."""

    class _BoomRegistry:
        def specs(self):
            return []

        def prompt_block(self):
            return ""

        async def dispatch(self, name, arguments):
            raise RuntimeError("kaboom")

    hooks, _, _ = _make_hooks_with_registry(registry=_BoomRegistry())
    awaitable = hooks.handle_tool_call("c1", "anything", "{}")
    payload = await awaitable
    assert payload is None


def test_additional_tool_specs_merges_motion_and_registry():
    motion_stack = _RecordingMotionStack()
    # _RecordingMotionStack didn't expose additional_tool_specs originally;
    # patch via subclass for this test.

    class _StackWithSpecs(_RecordingMotionStack):
        def additional_tool_specs(self):
            return [{"type": "function", "name": "play_gesture", "parameters": {}}]

        def gesture_vocabulary_prompt_block(self):
            return "Use play_gesture for nods."

    motion_stack = _StackWithSpecs()
    tool = _FakeRegistryTool(
        "register_current_location", prompt="Use register_current_location for the city."
    )
    registry = ToolRegistry([tool])
    hooks, _, _ = _make_hooks_with_registry(
        registry=registry, motion_stack=motion_stack
    )

    specs = hooks.additional_tool_specs()
    names = {s["name"] for s in specs}
    assert names == {"play_gesture", "register_current_location"}

    instructions = hooks.additional_instructions()
    assert "Use play_gesture for nods." in instructions
    assert "Use register_current_location for the city." in instructions


def test_additional_tool_specs_only_registry_when_motion_absent():
    tool = _FakeRegistryTool("register_current_location")
    registry = ToolRegistry([tool])
    hooks, _, _ = _make_hooks_with_registry(registry=registry, motion_stack=None)

    specs = hooks.additional_tool_specs()
    assert [s["name"] for s in specs] == ["register_current_location"]


def test_additional_tool_specs_empty_when_neither_configured():
    hooks, _, _ = _make_hooks()  # no motion, no registry
    assert hooks.additional_tool_specs() == []
    assert hooks.additional_instructions() == ""


# ---------------------------------------------------------------------------
# Refusal-recovery cue (issue #52)
#
# When the realtime handler intercepts a model persona-drift refusal it
# calls hooks.on_refusal_recovery(escalated=...). The bridge must:
#   * flush any chunks queued against the cancelled assistant turn,
#   * return to the listening pose (so the robot doesn't appear frozen),
#   * fire the injected refusal cue off-thread with the escalation flag.
# ---------------------------------------------------------------------------


def test_on_refusal_recovery_clears_queue_and_returns_to_listen():
    hooks, robot, _ = _make_hooks()
    hooks.start_assistant_playback(b"\x00\x01")  # arms speaking_active
    hooks.start_assistant_playback(b"\x02\x03")

    hooks.on_refusal_recovery(escalated=False)

    assert robot.flush_calls == 1
    assert robot.calls[-1] == "listen"
    # Once back in LISTENING, a new chunk re-arms the speak animation.
    hooks.start_assistant_playback(b"\x10\x11")
    assert robot.calls.count("speak") == 2


def test_on_refusal_recovery_invokes_cue_off_thread_with_escalated_flag():
    """The cue runs in a daemon thread and receives the escalated flag
    so the flow-level default can pick the soft vs adult-redirect line."""
    cue_calls: List[Tuple[object, bool, int]] = []
    cue_started = threading.Event()
    release = threading.Event()
    caller_thread_id = threading.get_ident()

    def slow_cue(robot: object, escalated: bool) -> None:
        cue_started.set()
        release.wait(timeout=2.0)
        cue_calls.append((robot, escalated, threading.get_ident()))

    robot = FakeRobotController()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        play_chunk=RecordingPlayChunk(),
        refusal_recovery_cue=slow_cue,
    )

    hooks.on_refusal_recovery(escalated=True)

    # Returned without waiting — the cue must be running off-thread.
    assert cue_started.wait(timeout=1.0)
    assert cue_calls == []
    release.set()
    assert _wait_until(lambda: len(cue_calls) == 1, timeout=1.0)
    cue_robot, cue_escalated, cue_thread = cue_calls[0]
    assert cue_robot is robot
    assert cue_escalated is True
    assert cue_thread != caller_thread_id


def test_on_refusal_recovery_passes_soft_flag_when_not_escalated():
    cue_calls: List[bool] = []

    def cue(_robot: object, escalated: bool) -> None:
        cue_calls.append(escalated)

    robot = FakeRobotController()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        play_chunk=RecordingPlayChunk(),
        refusal_recovery_cue=cue,
    )

    hooks.on_refusal_recovery(escalated=False)

    assert _wait_until(lambda: len(cue_calls) == 1, timeout=1.0)
    assert cue_calls == [False]


def test_on_refusal_recovery_without_cue_is_no_op():
    """refusal_recovery_cue defaults to None — bridge must still publish
    the listening pose without raising."""
    hooks, robot, _ = _make_hooks()  # no refusal cue wired
    hooks.on_refusal_recovery(escalated=True)
    assert robot.calls[-1] == "listen"


def test_on_refusal_recovery_swallows_cue_exceptions():
    """A broken cue must not crash the bridge thread or propagate out."""
    def broken_cue(_robot: object, _escalated: bool) -> None:
        raise RuntimeError("simulated TTS outage")

    robot = FakeRobotController()
    hooks = KidsTeacherRobotHooks(
        robot_controller=robot,
        play_chunk=RecordingPlayChunk(),
        refusal_recovery_cue=broken_cue,
    )

    # Must return without raising; the worker thread logs and swallows.
    hooks.on_refusal_recovery(escalated=False)
    assert robot.calls[-1] == "listen"


def test_build_robot_hooks_wires_default_refusal_recovery_cue():
    """build_robot_hooks must inject a default cue so a real robot
    deployment plays the recovery line on intercept rather than going
    silent."""
    hooks = build_robot_hooks(FakeRobotController())
    assert hooks._refusal_recovery_cue is not None  # type: ignore[attr-defined]


def test_default_refusal_recovery_cue_picks_soft_or_escalated_text(monkeypatch):
    """The default cue selects between the soft and escalated lines
    based on the escalated flag and pushes the chosen text through the
    same TTS path used by the reconnect cue."""
    import kids_teacher_flow as flow

    fake_module = types.ModuleType("robot_teacher")
    fetched: List[str] = []

    def fake_api_get_dino_voice(text: str) -> bytes:
        fetched.append(text)
        return b"FAKEMP3"

    def fake_play(_robot, _mp3):
        return None

    fake_module.api_get_dino_voice = fake_api_get_dino_voice  # type: ignore[attr-defined]
    fake_module._play = fake_play  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "robot_teacher", fake_module)

    flow._default_refusal_recovery_cue(FakeRobotController(), escalated=False)
    flow._default_refusal_recovery_cue(FakeRobotController(), escalated=True)

    assert fetched == [
        flow._REFUSAL_RECOVERY_SOFT_TEXT,
        flow._REFUSAL_RECOVERY_ESCALATED_TEXT,
    ]


def test_default_refusal_recovery_cue_swallows_tts_failure(monkeypatch):
    """A TTS outage on the refusal cue must not raise — the recovery
    path is best-effort, never fatal to the session."""
    import kids_teacher_flow as flow

    fake_module = types.ModuleType("robot_teacher")

    def boom(_text: str) -> bytes:
        raise ConnectionError("simulated TTS outage")

    fake_module.api_get_dino_voice = boom  # type: ignore[attr-defined]
    fake_module._play = lambda r, b: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "robot_teacher", fake_module)

    flow._default_refusal_recovery_cue(  # must not raise
        FakeRobotController(), escalated=True
    )
