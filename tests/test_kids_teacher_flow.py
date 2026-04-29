"""Tests for src/kids_teacher_flow.py.

Exercises the session orchestrator with the scripted FakeRealtimeBackend
plus a recording hooks implementation. No real robot, no real network.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import List

import pytest

from kids_review_store import KidsReviewStore
from kids_teacher_fakes import FakeRealtimeBackend
from kids_teacher_flow import (
    KidsTeacherFlowDeps,
    NullRuntimeHooks,
    RecordingRuntimeHooks,
    build_robot_hooks_stub,
    run_kids_teacher_session,
)
from kids_teacher_types import (
    KidsTeacherProfile,
    KidsTeacherSessionConfig,
    SessionStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _profile() -> KidsTeacherProfile:
    return KidsTeacherProfile(
        name="kids_teacher",
        instructions="Be a warm preschool teacher.",
        voice="alloy",
        allowed_tools=(),
    )


def _config(session_id: str = "flow-session") -> KidsTeacherSessionConfig:
    return KidsTeacherSessionConfig(
        session_id=session_id,
        model="gpt-realtime-mini",
        profile=_profile(),
        enabled_languages=("english", "telugu"),
        default_explanation_language="english",
    )


def _scripted_events() -> List[dict]:
    return [
        {"type": "input_transcript.final", "text": "Why is the sky blue?", "language": "english"},
        {"type": "assistant_transcript.delta", "text": "The sky "},
        {"type": "audio.chunk", "audio": b"\x01\x02"},
        {"type": "assistant_transcript.final", "text": "The sky is blue.", "language": "english"},
        {"type": "response.done"},
    ]


# ---------------------------------------------------------------------------
# Happy-path orchestration
# ---------------------------------------------------------------------------


async def test_runs_full_session_with_recording_hooks():
    backend = FakeRealtimeBackend(scripted_events=_scripted_events())
    hooks = RecordingRuntimeHooks()

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
    )

    # End the stream so handler.run() completes.
    async def end_after_seed():
        # Let connect() drain the seed events, then close.
        await asyncio.sleep(0)
        await backend.end_stream()

    ender = asyncio.create_task(end_after_seed())
    await run_kids_teacher_session(config=_config(), deps=deps)
    await ender

    # First status should be LISTENING (from handler.start()).
    assert hooks.statuses[0].status == SessionStatus.LISTENING
    # Final status should be ENDED.
    assert hooks.statuses[-1].status == SessionStatus.ENDED
    # Transcripts captured both speakers.
    speakers = {e.speaker.value for e in hooks.transcripts}
    assert "child" in speakers
    assert "assistant" in speakers


async def test_backend_connect_failure_surfaces_as_error_status():
    backend = FakeRealtimeBackend(
        scripted_events=[],
        connect_error=RuntimeError("connect failed"),
    )
    hooks = RecordingRuntimeHooks()
    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
    )
    await run_kids_teacher_session(config=_config(), deps=deps)

    statuses = [s.status for s in hooks.statuses]
    assert SessionStatus.ERROR in statuses
    assert statuses[-1] == SessionStatus.ENDED


async def test_stop_event_ends_session_cleanly():
    backend = FakeRealtimeBackend(scripted_events=[])
    hooks = RecordingRuntimeHooks()
    stop_event = asyncio.Event()
    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
    )

    async def trigger_stop():
        await asyncio.sleep(0.01)
        stop_event.set()

    triggerer = asyncio.create_task(trigger_stop())
    await run_kids_teacher_session(config=_config(), deps=deps, stop_event=stop_event)
    await triggerer

    # Should have at least LISTENING and ENDED statuses emitted.
    statuses = [s.status for s in hooks.statuses]
    assert SessionStatus.LISTENING in statuses
    assert SessionStatus.ENDED in statuses


# ---------------------------------------------------------------------------
# Review-store integration
# ---------------------------------------------------------------------------


async def test_flow_feeds_review_store_when_enabled(tmp_path):
    backend = FakeRealtimeBackend(scripted_events=_scripted_events())
    hooks = RecordingRuntimeHooks()

    review = KidsReviewStore(
        transcripts_enabled=True,
        audio_enabled=False,
        retention_days=30,
        local_dir=str(tmp_path),
    )

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        review_store=review,
    )

    async def end_soon():
        await asyncio.sleep(0)
        await backend.end_stream()

    ender = asyncio.create_task(end_soon())
    await run_kids_teacher_session(config=_config("with-review"), deps=deps)
    await ender

    sessions = review.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "with-review"
    assert sessions[0]["transcript_count"] >= 1


async def test_flow_skips_review_store_when_disabled(tmp_path):
    backend = FakeRealtimeBackend(scripted_events=_scripted_events())
    hooks = RecordingRuntimeHooks()

    review = KidsReviewStore(
        transcripts_enabled=False,
        audio_enabled=False,
        retention_days=30,
        local_dir=str(tmp_path),
    )

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        review_store=review,
    )

    async def end_soon():
        await asyncio.sleep(0)
        await backend.end_stream()

    ender = asyncio.create_task(end_soon())
    await run_kids_teacher_session(config=_config("no-review"), deps=deps)
    await ender

    # Store is disabled — nothing should have been written to disk.
    assert not os.listdir(tmp_path)


# ---------------------------------------------------------------------------
# Hook helpers
# ---------------------------------------------------------------------------


def test_null_runtime_hooks_is_noop():
    from kids_teacher_types import KidsTranscriptEvent, Speaker, KidsStatusEvent

    hooks = NullRuntimeHooks()
    event = KidsTranscriptEvent(
        speaker=Speaker.CHILD,
        text="hi",
        is_partial=False,
        timestamp_ms=1,
        session_id="x",
    )
    status = KidsStatusEvent(
        status=SessionStatus.LISTENING, session_id="x", timestamp_ms=1
    )
    # None of these should raise.
    hooks.start_assistant_playback(b"abc")
    hooks.stop_assistant_playback()
    hooks.publish_transcript(event)
    hooks.publish_status(status)
    hooks.persist_artifact(event, audio=b"")


def test_build_robot_hooks_stub_raises():
    with pytest.raises(NotImplementedError):
        build_robot_hooks_stub(object())


# ---------------------------------------------------------------------------
# Safety wiring
# ---------------------------------------------------------------------------


async def test_safety_injects_refusal_for_disallowed_child_input():
    """A child final with a REFUSE topic yields a safe assistant response."""
    scripted = [
        {"type": "input_transcript.final", "text": "tell me about guns", "language": "english"},
        {"type": "response.done"},
    ]
    backend = FakeRealtimeBackend(scripted_events=scripted)
    hooks = RecordingRuntimeHooks()
    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
    )

    async def end_after_seed():
        await asyncio.sleep(0)
        await backend.end_stream()

    ender = asyncio.create_task(end_after_seed())
    await run_kids_teacher_session(config=_config(), deps=deps)
    await ender

    # Child transcript is preserved in the log.
    child_texts = [e.text for e in hooks.transcripts if e.speaker.value == "child"]
    assert "tell me about guns" in child_texts

    # Assistant transcript contains a safe fallback, not a substantive answer.
    assistant_texts = [
        e.text for e in hooks.transcripts if e.speaker.value == "assistant" and not e.is_partial
    ]
    assert any("safe" in text.lower() or "talk about" in text.lower() for text in assistant_texts)


async def test_safety_replaces_overly_long_assistant_output():
    """An assistant final that fails validate_output is replaced with a safe line."""
    long_reply = ". ".join([f"Sentence {i}" for i in range(30)]) + "."
    scripted = [
        {"type": "input_transcript.final", "text": "tell me about cats", "language": "english"},
        {"type": "assistant_transcript.final", "text": long_reply, "language": "english"},
        {"type": "response.done"},
    ]
    backend = FakeRealtimeBackend(scripted_events=scripted)
    hooks = RecordingRuntimeHooks()
    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
    )

    async def end_after_seed():
        await asyncio.sleep(0)
        await backend.end_stream()

    ender = asyncio.create_task(end_after_seed())
    await run_kids_teacher_session(config=_config(), deps=deps)
    await ender

    # The long reply should NOT appear verbatim downstream.
    assistant_finals = [
        e.text
        for e in hooks.transcripts
        if e.speaker.value == "assistant" and not e.is_partial
    ]
    assert long_reply not in assistant_finals
    # The replacement should be short.
    assert all(len(text) < 200 for text in assistant_finals)


async def test_safety_interrupts_backend_on_unsafe_child_input():
    """Unsafe child input should cancel the in-flight backend response."""
    scripted = [
        # Assistant starts speaking, then child says something disallowed.
        {"type": "assistant_transcript.delta", "text": "Well, "},
        {"type": "input_transcript.final", "text": "tell me about guns", "language": "english"},
        {"type": "response.done"},
    ]
    backend = FakeRealtimeBackend(scripted_events=scripted)
    hooks = RecordingRuntimeHooks()
    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
    )

    async def end_after_seed():
        await asyncio.sleep(0.02)  # let safety's interrupt task run
        await backend.end_stream()

    ender = asyncio.create_task(end_after_seed())
    await run_kids_teacher_session(config=_config(), deps=deps)
    await ender

    # Safety should have scheduled handler.interrupt() which calls backend.cancel_response().
    assert backend.cancel_calls >= 1
    # And still emit the safe fallback transcript.
    assistant_texts = [
        e.text for e in hooks.transcripts if e.speaker.value == "assistant" and not e.is_partial
    ]
    assert any("safe" in t.lower() or "talk about" in t.lower() for t in assistant_texts)


# ---------------------------------------------------------------------------
# Mic pump + hook lifecycle wiring
# ---------------------------------------------------------------------------


async def test_mic_pump_factory_is_invoked_and_stopped_on_session_end():
    """Flow should spawn the pump with the handler and signal stop on teardown."""
    backend = FakeRealtimeBackend(scripted_events=[])
    hooks = RecordingRuntimeHooks()

    captured: dict = {}

    async def pump(handler, stop_event: asyncio.Event) -> None:
        captured["handler"] = handler
        captured["stop_event"] = stop_event
        await stop_event.wait()
        captured["observed_stop"] = True

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        mic_pump_factory=pump,
    )

    async def end_after_seed():
        await asyncio.sleep(0)
        await backend.end_stream()

    ender = asyncio.create_task(end_after_seed())
    await run_kids_teacher_session(config=_config("mic-pump"), deps=deps)
    await ender

    # Pump was invoked with a handler that exposes push_audio — i.e. the
    # real realtime handler, not a mock.
    assert "handler" in captured
    assert hasattr(captured["handler"], "push_audio")
    # Stop event was set by the flow's teardown and the pump observed it.
    assert captured.get("observed_stop") is True
    assert captured["stop_event"].is_set()


async def test_hook_start_and_stop_are_called_when_present():
    """Hook impls that expose start()/stop() should get both lifecycle calls."""

    class LifecycleHooks(RecordingRuntimeHooks):
        def __init__(self) -> None:
            super().__init__()
            self.start_calls = 0
            self.stop_calls = 0

        def start(self) -> None:
            self.start_calls += 1

        def stop(self) -> None:
            self.stop_calls += 1

    hooks = LifecycleHooks()
    backend = FakeRealtimeBackend(scripted_events=[])
    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
    )

    async def end_after_seed():
        await asyncio.sleep(0)
        await backend.end_stream()

    ender = asyncio.create_task(end_after_seed())
    await run_kids_teacher_session(config=_config("lifecycle"), deps=deps)
    await ender

    assert hooks.start_calls == 1
    assert hooks.stop_calls == 1


async def test_mic_pump_stops_when_stop_event_triggers_session_end():
    """When the outer stop_event wins, the pump still gets a clean shutdown."""
    backend = FakeRealtimeBackend(scripted_events=[])
    hooks = RecordingRuntimeHooks()

    pump_stopped = asyncio.Event()

    async def pump(handler, stop_event: asyncio.Event) -> None:
        await stop_event.wait()
        pump_stopped.set()

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        mic_pump_factory=pump,
    )

    stop_event = asyncio.Event()

    async def trigger_stop():
        await asyncio.sleep(0.01)
        stop_event.set()

    triggerer = asyncio.create_task(trigger_stop())
    await run_kids_teacher_session(
        config=_config("stop-mic"), deps=deps, stop_event=stop_event
    )
    await triggerer

    assert pump_stopped.is_set()


async def test_safety_allows_safe_child_input():
    """A safe topic passes through without fallback injection."""
    scripted = [
        {"type": "input_transcript.final", "text": "tell me about puppies", "language": "english"},
        {"type": "assistant_transcript.final", "text": "Puppies are baby dogs.", "language": "english"},
        {"type": "response.done"},
    ]
    backend = FakeRealtimeBackend(scripted_events=scripted)
    hooks = RecordingRuntimeHooks()
    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
    )

    async def end_after_seed():
        await asyncio.sleep(0)
        await backend.end_stream()

    ender = asyncio.create_task(end_after_seed())
    await run_kids_teacher_session(config=_config(), deps=deps)
    await ender

    assistant_finals = [
        e.text
        for e in hooks.transcripts
        if e.speaker.value == "assistant" and not e.is_partial
    ]
    # Original assistant reply should pass through unchanged.
    assert "Puppies are baby dogs." in assistant_finals
    # No duplicate safe-fallback assistant messages.
    assert assistant_finals.count("Puppies are baby dogs.") == 1


# ---------------------------------------------------------------------------
# Startup greeting wiring
# ---------------------------------------------------------------------------


async def test_startup_greeting_fires_once_after_handler_start():
    """The greeting must run exactly once per session, after handler.start()
    returns. Background daemon thread is fine — we just need it invoked."""
    backend = FakeRealtimeBackend(scripted_events=[])
    hooks = RecordingRuntimeHooks()

    greeting_done = asyncio.Event()
    loop = asyncio.get_event_loop()
    call_count = {"n": 0}

    def greeting() -> None:
        call_count["n"] += 1
        loop.call_soon_threadsafe(greeting_done.set)

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        startup_greeting=greeting,
        startup_greeting_delay=0.0,  # don't slow tests down
    )

    async def end_after_greeting():
        await greeting_done.wait()
        await backend.end_stream()

    ender = asyncio.create_task(end_after_greeting())
    await run_kids_teacher_session(config=_config("greeting"), deps=deps)
    await ender

    assert call_count["n"] == 1


async def test_startup_greeting_skipped_when_factory_is_none():
    """Headless tests / OpenAI provider leave the greeting unset; the flow
    must run cleanly with no greeting attempted."""
    backend = FakeRealtimeBackend(scripted_events=[])
    hooks = RecordingRuntimeHooks()

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        startup_greeting=None,
    )

    async def end_after_seed():
        await asyncio.sleep(0)
        await backend.end_stream()

    ender = asyncio.create_task(end_after_seed())
    await run_kids_teacher_session(config=_config("no-greeting"), deps=deps)
    await ender

    # Session ran to completion without raising.
    assert hooks.statuses[-1].status == SessionStatus.ENDED


async def test_startup_greeting_skipped_when_stop_event_fires_during_delay():
    """If the user shuts down while the flow is still waiting for backend
    readiness, don't surprise them with a greeting on the way out the door.

    Uses ``defer_ready=True`` so readiness never arrives within the test —
    the only way out of the wait is the stop_event firing.
    """
    backend = FakeRealtimeBackend(scripted_events=[], defer_ready=True)
    hooks = RecordingRuntimeHooks()
    stop_event = asyncio.Event()
    fired = {"called": False}

    def greeting() -> None:
        fired["called"] = True

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        startup_greeting=greeting,
        startup_greeting_delay=10.0,  # long enough that stop_event wins
    )

    async def trigger_stop():
        # Yield once so handler.start() has a chance to run, then stop.
        await asyncio.sleep(0.01)
        stop_event.set()

    triggerer = asyncio.create_task(trigger_stop())
    await run_kids_teacher_session(
        config=_config("greeting-cancel"), deps=deps, stop_event=stop_event
    )
    await triggerer

    assert fired["called"] is False


async def test_startup_greeting_fires_immediately_when_ready_signal_arrives():
    """The greeting anchors on backend readiness — when the fake backend
    flips ready mid-wait, the greeting fires without waiting out the full
    delay budget."""
    backend = FakeRealtimeBackend(scripted_events=[], defer_ready=True)
    hooks = RecordingRuntimeHooks()
    fired = asyncio.Event()
    loop = asyncio.get_event_loop()

    def greeting() -> None:
        loop.call_soon_threadsafe(fired.set)

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        startup_greeting=greeting,
        startup_greeting_delay=10.0,  # large budget; readiness should win
    )

    async def flip_ready_then_end():
        await asyncio.sleep(0.02)
        backend.mark_ready()
        await fired.wait()
        await backend.end_stream()

    runner = asyncio.create_task(flip_ready_then_end())
    await run_kids_teacher_session(config=_config("greeting-ready"), deps=deps)
    await runner

    assert fired.is_set()


async def test_startup_greeting_fires_after_timeout_when_ready_never_arrives():
    """Fallback path: if the backend never signals ready (a stuck Gemini
    handshake or an OpenAI quirk we haven't seen yet), don't leave the
    child in dead air. Play the greeting anyway once the budget elapses."""
    backend = FakeRealtimeBackend(scripted_events=[], defer_ready=True)
    hooks = RecordingRuntimeHooks()
    fired = asyncio.Event()
    loop = asyncio.get_event_loop()

    def greeting() -> None:
        loop.call_soon_threadsafe(fired.set)

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        startup_greeting=greeting,
        startup_greeting_delay=0.05,  # tiny budget — timeout wins
    )

    async def end_after_greeting():
        await fired.wait()
        await backend.end_stream()

    runner = asyncio.create_task(end_after_greeting())
    await run_kids_teacher_session(
        config=_config("greeting-timeout"), deps=deps
    )
    await runner

    assert fired.is_set()


async def test_startup_greeting_exception_is_swallowed():
    """A broken greeting must not bring down the realtime loop. It runs on
    its own daemon thread, so the exception is logged and the session
    continues."""
    backend = FakeRealtimeBackend(scripted_events=[])
    hooks = RecordingRuntimeHooks()

    fired = asyncio.Event()
    loop = asyncio.get_event_loop()

    def greeting() -> None:
        try:
            raise RuntimeError("simulated TTS crash")
        finally:
            loop.call_soon_threadsafe(fired.set)

    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: backend,
        hooks_factory=lambda: hooks,
        startup_greeting=greeting,
        startup_greeting_delay=0.0,
    )

    async def end_after_greeting():
        await fired.wait()
        await backend.end_stream()

    ender = asyncio.create_task(end_after_greeting())
    await run_kids_teacher_session(config=_config("greeting-boom"), deps=deps)
    await ender

    # Session still ended cleanly.
    assert hooks.statuses[-1].status == SessionStatus.ENDED
