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


async def test_speech_started_triggers_barge_in() -> None:
    """VAD speech_started event cancels an in-flight assistant response."""
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    # Assistant starts speaking.
    await backend.push_event(
        {"type": "assistant_transcript.delta", "text": "The sky..."}
    )
    await asyncio.sleep(0.01)

    # Server VAD detects the child is speaking — earliest possible barge-in.
    await backend.push_event({"type": "input.speech_started"})
    await asyncio.sleep(0.01)

    assert backend.cancel_calls == 1

    await backend.end_stream()
    await run_task


async def test_audio_chunk_before_transcript_opens_barge_in_gate() -> None:
    """Regression: Gemini's native-audio path can ship audio.chunk before any
    assistant_transcript.delta. The barge-in gate (_assistant_active) must
    still open on audio alone, otherwise a speech_started arriving in that
    window no-ops and the robot keeps talking past the interrupt."""
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    # Audio arrives first — no transcript delta yet.
    await backend.push_event({"type": "audio.chunk", "audio": b"\x00\x01"})
    await asyncio.sleep(0.01)

    # Server VAD now reports the child started speaking. Before the fix this
    # was a no-op because _assistant_active was still False.
    await backend.push_event({"type": "input.speech_started"})
    await asyncio.sleep(0.01)

    assert backend.cancel_calls == 1
    assert hooks.stop_playback_calls >= 1

    await backend.end_stream()
    await run_task


async def test_assistant_transcript_delta_does_not_publish_speaking() -> None:
    """Regression: SPEAKING used to fire on the first transcript delta, but
    Gemini Live can ship transcript text 200–500 ms before the matching
    audio. Tying motion to text made the head start nodding before the voice
    played. The status must now stay LISTENING until an audio chunk lands."""
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    await backend.push_event(
        {"type": "assistant_transcript.delta", "text": "The sky..."}
    )
    await asyncio.sleep(0.01)

    speaking = [s for s in hooks.statuses if s.status == SessionStatus.SPEAKING]
    assert speaking == []  # text alone must not trigger motion
    # The barge-in gate still opens on text — covered by other tests; here we
    # just confirm motion stays gated.
    assert hooks.playback_chunks == []

    await backend.end_stream()
    await run_task


async def test_audio_chunk_publishes_speaking_once_per_turn() -> None:
    """First audio chunk publishes SPEAKING; subsequent chunks of the same
    turn don't re-publish (avoid restart-of-animation thrash)."""
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    await backend.push_event({"type": "audio.chunk", "audio": b"\x00\x01"})
    await backend.push_event({"type": "audio.chunk", "audio": b"\x02\x03"})
    await backend.push_event({"type": "audio.chunk", "audio": b"\x04\x05"})
    await asyncio.sleep(0.01)

    speaking = [s for s in hooks.statuses if s.status == SessionStatus.SPEAKING]
    assert len(speaking) == 1
    assert hooks.playback_chunks == [b"\x00\x01", b"\x02\x03", b"\x04\x05"]

    await backend.end_stream()
    await run_task


async def test_audio_queued_before_speaking_status_published() -> None:
    """On the first audio chunk, ``start_assistant_playback`` must be
    invoked before SPEAKING is published, so the speaker pipeline can begin
    decoding in parallel with the robot's speak animation rather than after
    a synchronous motion dispatch returns."""

    class OrderingHooks(FakeHooks):
        """Record every hook call into one ordered list so we can assert
        the sequencing between audio queueing and status publication."""

        def __init__(self) -> None:
            super().__init__()
            self.event_log: List[str] = []

        def start_assistant_playback(self, audio_chunk: bytes) -> None:
            self.event_log.append("start_assistant_playback")
            super().start_assistant_playback(audio_chunk)

        def publish_status(self, event: KidsStatusEvent) -> None:
            self.event_log.append(f"publish_status:{event.status.value}")
            super().publish_status(event)

    backend = FakeRealtimeBackend()
    hooks = OrderingHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    await backend.push_event({"type": "audio.chunk", "audio": b"\x00\x01"})
    await asyncio.sleep(0.01)

    # Find the first audio handover and the first SPEAKING publish — the
    # playback hand-off must precede the motion trigger.
    play_idx = hooks.event_log.index("start_assistant_playback")
    speak_idx = hooks.event_log.index(
        f"publish_status:{SessionStatus.SPEAKING.value}"
    )
    assert play_idx < speak_idx

    await backend.end_stream()
    await run_task


async def test_response_done_resets_speaking_gate_so_next_turn_publishes_again() -> None:
    """The once-per-turn SPEAKING gate must reset on response.done; otherwise
    the second assistant turn would never re-trigger the speak animation."""
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    # Turn 1: audio chunk → SPEAKING fires once → response.done → LISTENING.
    await backend.push_event({"type": "audio.chunk", "audio": b"\x00\x01"})
    await backend.push_event({"type": "response.done"})
    await asyncio.sleep(0.01)

    # Turn 2: a fresh audio chunk must publish SPEAKING again.
    await backend.push_event({"type": "audio.chunk", "audio": b"\x02\x03"})
    await asyncio.sleep(0.01)

    speaking = [s for s in hooks.statuses if s.status == SessionStatus.SPEAKING]
    assert len(speaking) == 2

    await backend.end_stream()
    await run_task


async def test_speech_stopped_does_not_cancel() -> None:
    """VAD speech_stopped is an informational marker — no barge-in."""
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    await backend.push_event(
        {"type": "assistant_transcript.delta", "text": "Hi"}
    )
    await asyncio.sleep(0.01)
    await backend.push_event({"type": "input.speech_stopped"})
    await asyncio.sleep(0.01)

    assert backend.cancel_calls == 0

    await backend.end_stream()
    await run_task


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


async def test_run_after_connect_failure_exits_cleanly() -> None:
    backend = FakeRealtimeBackend(connect_error=RuntimeError("boom"))
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    await asyncio.wait_for(handler.run(), timeout=1.0)

    statuses = [s.status for s in hooks.statuses]
    assert SessionStatus.ERROR in statuses
    assert statuses[-1] == SessionStatus.ENDED


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


# ---------------------------------------------------------------------------
# session.reconnecting / session.reconnected (E)
#
# When the Gemini backend rebuilds a dropped session, the realtime handler
# must surface RECONNECTING (so the bridge plays a recovery cue) followed
# by LISTENING — and crucially MUST NOT push the generic ERROR fallback
# line, which is what made the on-device 2026-04-27 incident look broken.
# ---------------------------------------------------------------------------


async def test_session_reconnecting_publishes_reconnecting_status_no_fallback():
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())

    # Simulate an active assistant response in flight, then a disconnect.
    await backend.push_event(
        {"type": "assistant_transcript.delta", "text": "scattering "}
    )
    await backend.push_event({"type": "audio.chunk", "audio": b"\x01"})
    await asyncio.sleep(0.01)

    stop_calls_before = hooks.stop_playback_calls
    await backend.push_event({"type": "session.reconnecting"})
    await asyncio.sleep(0.01)

    statuses = [s.status for s in hooks.statuses]
    assert SessionStatus.RECONNECTING in statuses
    # The in-flight playback must be flushed so we don't keep speaking
    # chunks that belong to the dead socket.
    assert hooks.stop_playback_calls == stop_calls_before + 1
    # Crucially: the generic ERROR fallback line MUST NOT have been
    # surfaced — otherwise we're back to the on-device behavior the user
    # reported (robot says "Let me try that again in a moment." while the
    # bridge spams the same line every 3s).
    fallback_lines = [
        t
        for t in hooks.transcripts
        if t.speaker == Speaker.ASSISTANT
        and t.text == "Let me try that again in a moment."
    ]
    assert fallback_lines == []
    assert all(s.status != SessionStatus.ERROR for s in hooks.statuses)

    await backend.push_event({"type": "session.reconnected"})
    await asyncio.sleep(0.01)
    statuses_after = [s.status for s in hooks.statuses]
    # LISTENING must reappear after reconnect so the mic pump knows the
    # session is healthy again.
    assert statuses_after.count(SessionStatus.LISTENING) >= 2

    await backend.end_stream()
    await run_task


# ---------------------------------------------------------------------------
# Behavior 12: tool.call dispatch (motion-director plumbing)
# ---------------------------------------------------------------------------


class _ToolHooks(FakeHooks):
    """FakeHooks + a recording handle_tool_call."""

    def __init__(self, *, return_value: Optional[str] = '{"ok": true}',
                 raise_exc: Optional[Exception] = None) -> None:
        super().__init__()
        self.tool_calls: List[tuple] = []
        self._return_value = return_value
        self._raise_exc = raise_exc

    def handle_tool_call(
        self, call_id: str, name: str, arguments: str
    ) -> Optional[str]:
        self.tool_calls.append((call_id, name, arguments))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._return_value


async def test_tool_call_event_dispatched_to_hook_and_acked() -> None:
    backend = FakeRealtimeBackend()
    hooks = _ToolHooks(return_value='{"ok": true, "detail": "playing"}')
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event(
        {
            "type": "tool.call",
            "call_id": "call_xyz",
            "name": "play_gesture",
            "arguments": '{"name": "nod_encourage"}',
        }
    )
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    assert hooks.tool_calls == [
        ("call_xyz", "play_gesture", '{"name": "nod_encourage"}')
    ]
    assert backend.tool_responses == [
        ("call_xyz", '{"ok": true, "detail": "playing"}')
    ]


async def test_tool_call_without_handle_tool_call_hook_is_ignored() -> None:
    """Hooks that don't expose handle_tool_call must not crash the handler."""
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()  # no handle_tool_call
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event(
        {
            "type": "tool.call",
            "call_id": "call_xyz",
            "name": "play_gesture",
            "arguments": "{}",
        }
    )
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    assert backend.tool_responses == []


async def test_tool_call_hook_returning_none_skips_ack() -> None:
    backend = FakeRealtimeBackend()
    hooks = _ToolHooks(return_value=None)
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event(
        {
            "type": "tool.call",
            "call_id": "call_xyz",
            "name": "play_gesture",
            "arguments": "{}",
        }
    )
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    assert hooks.tool_calls == [("call_xyz", "play_gesture", "{}")]
    assert backend.tool_responses == []


async def test_tool_call_hook_exception_is_swallowed() -> None:
    backend = FakeRealtimeBackend()
    hooks = _ToolHooks(raise_exc=RuntimeError("hook boom"))
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event(
        {
            "type": "tool.call",
            "call_id": "call_xyz",
            "name": "play_gesture",
            "arguments": "{}",
        }
    )
    await asyncio.sleep(0.01)

    # Subsequent events should still be processed — the handler didn't die.
    await backend.push_event(
        {"type": "input.speech_started"}
    )
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    # No ack was sent (hook raised before returning a payload).
    assert backend.tool_responses == []


async def test_tool_call_event_with_blank_call_id_skips_ack() -> None:
    backend = FakeRealtimeBackend()
    hooks = _ToolHooks(return_value='{"ok": true}')
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event(
        {
            "type": "tool.call",
            "call_id": "",
            "name": "play_gesture",
            "arguments": "{}",
        }
    )
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    # Hook still fires (so the side-effect happens) but no ack is shipped.
    assert hooks.tool_calls == [("", "play_gesture", "{}")]
    assert backend.tool_responses == []


async def test_tool_call_event_with_non_string_arguments_normalizes_to_empty() -> None:
    """Defensive: if the backend ever ships non-string args, hand them along
    as an empty string rather than letting a TypeError reach the model."""
    backend = FakeRealtimeBackend()
    hooks = _ToolHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event(
        {
            "type": "tool.call",
            "call_id": "call_xyz",
            "name": "play_gesture",
            "arguments": {"name": "nod_encourage"},  # dict instead of str
        }
    )
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    assert hooks.tool_calls == [("call_xyz", "play_gesture", "")]


# ---------------------------------------------------------------------------
# Behavior 13: VAD edges forwarded to optional hooks (motion-director)
# ---------------------------------------------------------------------------


class _VadHooks(FakeHooks):
    def __init__(self, *, raise_on_started: bool = False) -> None:
        super().__init__()
        self.speech_started_calls = 0
        self.speech_stopped_calls = 0
        self._raise_on_started = raise_on_started

    def on_speech_started(self) -> None:
        self.speech_started_calls += 1
        if self._raise_on_started:
            raise RuntimeError("hook boom")

    def on_speech_stopped(self) -> None:
        self.speech_stopped_calls += 1


async def test_speech_started_event_forwards_to_hook() -> None:
    backend = FakeRealtimeBackend()
    hooks = _VadHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event({"type": "input.speech_started"})
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    assert hooks.speech_started_calls == 1


async def test_speech_stopped_event_forwards_to_hook() -> None:
    backend = FakeRealtimeBackend()
    hooks = _VadHooks()
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event({"type": "input.speech_stopped"})
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    assert hooks.speech_stopped_calls == 1


async def test_vad_edges_no_op_when_hook_missing() -> None:
    """Hooks without on_speech_started/stopped must not crash the loop."""
    backend = FakeRealtimeBackend()
    hooks = FakeHooks()  # no VAD methods
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event({"type": "input.speech_started"})
    await backend.push_event({"type": "input.speech_stopped"})
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task


async def test_vad_hook_exception_is_swallowed() -> None:
    backend = FakeRealtimeBackend()
    hooks = _VadHooks(raise_on_started=True)
    handler = _handler(backend, hooks)

    await handler.start()
    run_task = asyncio.create_task(handler.run())
    await asyncio.sleep(0.01)

    await backend.push_event({"type": "input.speech_started"})
    await asyncio.sleep(0.01)
    # The handler must still process subsequent events.
    await backend.push_event({"type": "input.speech_stopped"})
    await asyncio.sleep(0.01)
    await backend.end_stream()
    await run_task

    assert hooks.speech_started_calls == 1
    assert hooks.speech_stopped_calls == 1
