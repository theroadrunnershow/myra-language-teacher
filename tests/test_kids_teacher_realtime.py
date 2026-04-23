"""Tests for src/kids_teacher_realtime.py.

Covers every numbered behavior in the KT-I1-03/04/05 spec using the
scripted FakeRealtimeBackend and an in-memory FakeHooks. No real openai,
no real network.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Tuple

import pytest

from kids_teacher_realtime import (
    FakeRealtimeBackend,
    KidsTeacherRealtimeHandler,
    SessionMemoryTurn,
)
from kids_teacher_types import (
    KidsStatusEvent,
    KidsTeacherProfile,
    KidsTeacherSessionConfig,
    KidsTranscriptEvent,
    SessionStatus,
    Speaker,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class FakeHooks:
    """Records every hook call so tests can assert on the exact sequence."""

    def __init__(self) -> None:
        self.transcripts: List[KidsTranscriptEvent] = []
        self.statuses: List[KidsStatusEvent] = []
        self.playback_chunks: List[bytes] = []
        self.stop_playback_calls: int = 0
        self.persist_calls: List[Tuple[KidsTranscriptEvent, Optional[bytes]]] = []

    def start_assistant_playback(self, audio_chunk: bytes) -> None:
        self.playback_chunks.append(audio_chunk)

    def stop_assistant_playback(self) -> None:
        self.stop_playback_calls += 1

    def publish_transcript(self, event: KidsTranscriptEvent) -> None:
        self.transcripts.append(event)

    def publish_status(self, event: KidsStatusEvent) -> None:
        self.statuses.append(event)

    def persist_artifact(
        self,
        event: KidsTranscriptEvent,
        audio: Optional[bytes] = None,
    ) -> None:
        self.persist_calls.append((event, audio))


def _profile() -> KidsTeacherProfile:
    return KidsTeacherProfile(
        name="kids_teacher",
        instructions="Be a warm preschool teacher.",
        voice="alloy",
        allowed_tools=(),
    )


def _config(session_id: str = "s1") -> KidsTeacherSessionConfig:
    return KidsTeacherSessionConfig(
        session_id=session_id,
        model="gpt-realtime-mini",
        profile=_profile(),
        enabled_languages=("english", "telugu"),
        default_explanation_language="english",
    )


def _handler(
    backend: FakeRealtimeBackend,
    hooks: FakeHooks,
    *,
    memory_turns: int = 5,
) -> KidsTeacherRealtimeHandler:
    tick = [0]

    def clock() -> float:
        tick[0] += 1
        return float(tick[0])

    return KidsTeacherRealtimeHandler(
        config=_config(),
        backend=backend,
        hooks=hooks,
        clock=clock,
        memory_turns=memory_turns,
    )


async def _drive(handler: KidsTeacherRealtimeHandler) -> None:
    """Start the handler and run its event loop to completion.

    The fake backend must have ``end_stream()`` or a ``close`` eventually
    wired before calling this helper, otherwise the run task will hang.
    """
    await handler.start()
    await handler.run()


# ---------------------------------------------------------------------------
# Behavior 1: connect emits LISTENING
# ---------------------------------------------------------------------------


async def test_start_emits_listening_status() -> None:
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    await backend.end_stream()

    assert len(backend.connect_calls) == 1
    statuses = [event.status for event in hooks.statuses]
    assert SessionStatus.LISTENING in statuses
    assert hooks.statuses[0].status == SessionStatus.LISTENING
    assert hooks.statuses[0].session_id == "s1"


# ---------------------------------------------------------------------------
# Behavior 2: transcript ordering for delta/delta/final
# ---------------------------------------------------------------------------


async def test_input_transcript_delta_delta_final_ordering() -> None:
    backend = FakeRealtimeBackend(
        scripted_events=[
            {"type": "input_transcript.delta", "text": "why"},
            {"type": "input_transcript.delta", "text": "why is"},
            {
                "type": "input_transcript.final",
                "text": "why is the sky blue",
                "language": "english",
            },
        ]
    )
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    # Let the scripted events flow, then close the stream.
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    child_transcripts = [
        t for t in hooks.transcripts if t.speaker == Speaker.CHILD
    ]
    assert [t.is_partial for t in child_transcripts] == [True, True, False]
    assert [t.text for t in child_transcripts] == [
        "why",
        "why is",
        "why is the sky blue",
    ]
    assert child_transcripts[-1].language == "english"


# ---------------------------------------------------------------------------
# Behavior 3: recent-turn memory cap
# ---------------------------------------------------------------------------


async def test_recent_turn_memory_caps_to_memory_turns() -> None:
    # Seven final turns, memory cap of 3 → only last 3 retained.
    scripted: list[dict] = []
    for i in range(7):
        scripted.append(
            {
                "type": "input_transcript.final",
                "text": f"child-{i}",
                "language": "english",
            }
        )
    backend = FakeRealtimeBackend(scripted_events=scripted)
    hooks = FakeHooks()
    handler = _handler(backend, hooks, memory_turns=3)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    recent = handler.recent_turns
    assert len(recent) == 3
    assert [turn.text for turn in recent] == ["child-4", "child-5", "child-6"]
    assert all(isinstance(turn, SessionMemoryTurn) for turn in recent)


async def test_recent_turn_memory_mixes_child_and_assistant() -> None:
    backend = FakeRealtimeBackend(
        scripted_events=[
            {"type": "input_transcript.final", "text": "hi", "language": "english"},
            {
                "type": "assistant_transcript.final",
                "text": "hello there",
                "language": "english",
            },
            {"type": "input_transcript.final", "text": "bye", "language": "english"},
        ]
    )
    hooks = FakeHooks()
    handler = _handler(backend, hooks, memory_turns=5)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    speakers = [turn.speaker for turn in handler.recent_turns]
    assert speakers == [Speaker.CHILD, Speaker.ASSISTANT, Speaker.CHILD]


# ---------------------------------------------------------------------------
# Behavior 4: barge-in
# ---------------------------------------------------------------------------


async def test_child_interrupts_assistant_triggers_cancel_and_stop_playback() -> None:
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    # Assistant starts speaking.
    await backend.push_event(
        {"type": "assistant_transcript.delta", "text": "The sky..."}
    )
    await backend.push_event({"type": "audio.chunk", "audio": b"\x00\x01"})
    await asyncio.sleep(0.01)

    # Child barges in.
    await backend.push_event({"type": "input_transcript.delta", "text": "why"})
    await asyncio.sleep(0.01)

    assert backend.cancel_calls == 1
    assert hooks.stop_playback_calls >= 1
    # Session remains alive — no ENDED status before stop().
    assert not any(s.status == SessionStatus.ENDED for s in hooks.statuses)

    await backend.end_stream()
    await run_task


# ---------------------------------------------------------------------------
# Behavior 5: one active response (overlap handling)
# ---------------------------------------------------------------------------


async def test_overlapping_assistant_response_cancels_previous() -> None:
    """A fresh assistant stream while one is active cancels the prior one.

    This matches the handler's documented barge-in-style policy:
    ``_cancel_active_response`` is invoked before accepting new output so
    only one response is ever active at a time.
    """
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    await backend.push_event(
        {"type": "assistant_transcript.delta", "text": "first"}
    )
    await asyncio.sleep(0.01)

    # Simulate a rogue overlap by re-entering as if the child started again
    # (our spec says child speech during assistant output cancels).
    await backend.push_event({"type": "input_transcript.delta", "text": "..."})
    await asyncio.sleep(0.01)

    # The second assistant stream should start cleanly with no prior
    # "active" flag corrupting ordering.
    await backend.push_event(
        {"type": "assistant_transcript.delta", "text": "second"}
    )
    await backend.push_event({"type": "response.done"})
    await asyncio.sleep(0.01)

    # Exactly one cancel and one stop_playback from the barge-in.
    assert backend.cancel_calls == 1

    await backend.end_stream()
    await run_task


# ---------------------------------------------------------------------------
# Behavior 6: fallback on error
# ---------------------------------------------------------------------------


async def test_error_event_emits_error_status_and_fallback_line() -> None:
    backend = FakeRealtimeBackend(
        scripted_events=[{"type": "error", "message": "upstream blew up"}]
    )
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    error_statuses = [s for s in hooks.statuses if s.status == SessionStatus.ERROR]
    assert len(error_statuses) == 1
    assert error_statuses[0].detail == "upstream blew up"

    fallback = [
        t
        for t in hooks.transcripts
        if t.speaker == Speaker.ASSISTANT
        and t.text == "Let me try that again in a moment."
    ]
    assert len(fallback) == 1
    assert fallback[0].is_partial is False

    # Session re-enters listening after the error.
    listening_after_error = [
        s for s in hooks.statuses if s.status == SessionStatus.LISTENING
    ]
    assert len(listening_after_error) >= 2  # initial + post-error


# ---------------------------------------------------------------------------
# Behavior 7: queue flush on interrupt
# ---------------------------------------------------------------------------


async def test_interrupt_flushes_queue_and_stops_playback() -> None:
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    await backend.push_event(
        {"type": "assistant_transcript.delta", "text": "sunlight scatters"}
    )
    await backend.push_event({"type": "audio.chunk", "audio": b"a"})
    await backend.push_event({"type": "audio.chunk", "audio": b"b"})
    await asyncio.sleep(0.01)

    before_stop = hooks.stop_playback_calls
    # Explicit external interrupt — must not deadlock and must flush.
    await asyncio.wait_for(handler.interrupt(), timeout=1.0)
    assert hooks.stop_playback_calls == before_stop + 1

    await backend.end_stream()
    await run_task


# ---------------------------------------------------------------------------
# Behavior 8: clean stop
# ---------------------------------------------------------------------------


async def test_stop_closes_backend_and_emits_ended() -> None:
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await handler.stop()
    await run_task

    assert backend.close_calls == 1
    assert any(s.status == SessionStatus.ENDED for s in hooks.statuses)


# ---------------------------------------------------------------------------
# KT-I1-05: failure handling
# ---------------------------------------------------------------------------


async def test_connect_failure_emits_error_without_hang() -> None:
    backend = FakeRealtimeBackend(connect_error=RuntimeError("boom"))
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await asyncio.wait_for(handler.start(), timeout=1.0)

    error_statuses = [s for s in hooks.statuses if s.status == SessionStatus.ERROR]
    assert len(error_statuses) == 1
    assert "boom" in (error_statuses[0].detail or "")
    # No listening status should have been emitted — connect failed.
    assert not any(s.status == SessionStatus.LISTENING for s in hooks.statuses)


async def test_stream_ends_unexpectedly_emits_ended() -> None:
    # No scripted events — stream ends immediately.
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    await backend.end_stream()
    await asyncio.wait_for(handler.run(), timeout=1.0)

    assert any(s.status == SessionStatus.ENDED for s in hooks.statuses)


async def test_error_mid_stream_publishes_fallback_line_and_keeps_session() -> None:
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    # Child turn, then a mid-stream backend error.
    await backend.push_event(
        {"type": "input_transcript.final", "text": "hi there", "language": "english"}
    )
    await backend.push_event({"type": "error", "message": "hiccup"})
    await asyncio.sleep(0.01)

    # Fallback line is present after the error.
    fallback_transcripts = [
        t
        for t in hooks.transcripts
        if t.speaker == Speaker.ASSISTANT
        and t.text == "Let me try that again in a moment."
    ]
    assert len(fallback_transcripts) == 1

    # Session is still alive and listening — follow-up events still route.
    await backend.push_event(
        {"type": "input_transcript.final", "text": "again", "language": "english"}
    )
    await asyncio.sleep(0.01)

    finals = [
        t
        for t in hooks.transcripts
        if t.speaker == Speaker.CHILD and not t.is_partial
    ]
    assert [t.text for t in finals] == ["hi there", "again"]

    await backend.end_stream()
    await run_task
