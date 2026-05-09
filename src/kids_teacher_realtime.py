"""Shared streaming conversation handler for kids-teacher mode.

Owns session lifecycle, routes backend events to runtime hooks, enforces
response ordering, handles barge-in, and surfaces safe fallback on
failure. Pure transport + lifecycle — safety policy, admin precedence,
and output validation live elsewhere (``kids_safety.py``).

The scripted :class:`FakeRealtimeBackend` used by tests lives in
``kids_teacher_fakes`` and is re-exported here for ergonomic import.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Deque, Optional

from kids_teacher_backend import RealtimeBackend, build_session_payload
from kids_teacher_fakes import FakeRealtimeBackend  # noqa: F401 (re-export)
from kids_teacher_refusal import is_refusal
from kids_teacher_types import (
    KidsStatusEvent,
    KidsTeacherRuntimeHooks,
    KidsTeacherSessionConfig,
    KidsTranscriptEvent,
    SessionStatus,
    Speaker,
)

logger = logging.getLogger(__name__)


_FALLBACK_ASSISTANT_LINE = "Let me try that again in a moment."

# A second refusal within this window upgrades the bridge line from
# "let me think for a second" (soft) to "ask a grown-up" (escalated).
# Two minutes covers the typical recovery -> next-turn span without
# making the escalation sticky for a whole session.
_REFUSAL_ESCALATION_WINDOW_SECONDS = 120.0


@dataclass(frozen=True)
class SessionMemoryTurn:
    speaker: Speaker
    text: str
    timestamp_ms: int
    language: Optional[str]


class KidsTeacherRealtimeHandler:
    """Streaming conversation handler for one kids-teacher session.

    The handler does not own the network connection; it consumes normalized
    events from any :class:`RealtimeBackend` implementation and translates
    them into transcript/status events + audio playback hooks.
    """

    def __init__(
        self,
        *,
        config: KidsTeacherSessionConfig,
        backend: RealtimeBackend,
        hooks: KidsTeacherRuntimeHooks,
        clock: Optional[Callable[[], float]] = None,
        memory_turns: int = 5,
        greeting_prompt: Optional[str] = None,
    ) -> None:
        self._config = config
        self._backend = backend
        self._hooks = hooks
        self._clock = clock or time.time
        self._memory: Deque[SessionMemoryTurn] = deque(maxlen=max(1, memory_turns))
        self._greeting_prompt = greeting_prompt

        self._started = False
        self._stopped = False
        self._connect_failed = False
        self._run_task: Optional[asyncio.Task] = None

        # Track whether an assistant response is actively streaming so we
        # can route barge-in and de-dupe overlapping response streams.
        self._assistant_active = False
        # SPEAKING status is published exactly once per turn, on the first
        # audio chunk — *not* on the first transcript delta. Gemini Live
        # often ships transcript text 200–500 ms before the matching audio,
        # and tying motion to text makes the head start nodding before the
        # voice plays. Reset on response.done / cancel / reconnect.
        self._speaking_published = False
        # Pending assistant audio chunks that have not yet been played.
        # Drained on barge-in or stop.
        self._pending_audio: asyncio.Queue[bytes] = asyncio.Queue()

        # Per-turn accumulated assistant transcript and a one-shot guard
        # so the refusal detector fires at most once per turn (otherwise
        # every subsequent partial after the first match would re-trigger
        # the intercept). Both reset on response.done / reconnect /
        # cancellation. ``_last_refusal_at`` is monotonic — wallclock
        # drift doesn't matter, only the window math.
        self._assistant_partial_buffer: str = ""
        self._refusal_handled_this_turn: bool = False
        self._last_refusal_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Public lifecycle API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect the backend and move the session into LISTENING state."""
        if self._started:
            return
        extra_tools = self._collect_additional_tool_specs()
        extra_instructions = self._collect_additional_instructions()
        payload = build_session_payload(
            self._config,
            additional_tools=extra_tools,
            additional_instructions=extra_instructions,
        )
        try:
            await self._backend.connect(payload)
        except Exception as exc:
            self._connect_failed = True
            logger.warning("[kids_teacher_realtime] backend.connect raised: %s", exc)
            self._publish_status(SessionStatus.ERROR, detail=str(exc))
            return
        self._connect_failed = False
        self._started = True
        self._publish_status(SessionStatus.LISTENING)
        if self._greeting_prompt:
            try:
                await self._backend.send_text(self._greeting_prompt)
            except Exception as exc:
                logger.warning(
                    "[kids_teacher_realtime] greeting send_text raised: %s", exc
                )

    async def push_audio(self, chunk: bytes) -> None:
        """Forward a child audio chunk to the backend."""
        if self._stopped:
            return
        await self._backend.send_audio(chunk)

    async def push_video(self, jpeg_bytes: bytes) -> None:
        """Forward one JPEG video frame to the backend.

        Pre-session and post-teardown frames are dropped silently so the
        video sender loop never has to know the handler's lifecycle state.
        Mirrors :meth:`push_audio`.
        """
        if not self.session_active:
            return
        await self._backend.send_video(jpeg_bytes)

    async def push_text(self, text: str) -> None:
        """Inject a system-style text turn mid-session.

        Used by the on-demand face-rec loop to announce new arrivals
        (FR-KID-16) without waiting for the next session boot. Pre-connect
        and post-teardown messages are dropped silently — same lifecycle
        gating as :meth:`push_video`.
        """
        if not self.session_active or not text:
            return
        await self._backend.send_text(text)

    @property
    def session_active(self) -> bool:
        """True between a successful ``start()`` and ``stop()``.

        The video sender loop polls this every tick so frames are only
        sent while the backend session is open. Pre-connect, post-teardown,
        and connect-failed states all evaluate to False.
        """
        return self._started and not self._stopped and not self._connect_failed

    async def interrupt(self) -> None:
        """User-initiated barge-in: flush assistant audio and cancel response."""
        await self._cancel_active_response(reason="external_interrupt")

    async def stop(self) -> None:
        """Close the backend and emit an ENDED status event."""
        if self._stopped:
            return
        self._stopped = True
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self._backend.close()
        except Exception as exc:
            logger.warning("[kids_teacher_realtime] backend.close raised: %s", exc)
        self._publish_status(SessionStatus.ENDED)

    async def run(self) -> None:
        """Main loop: consume backend events until stop or response stream ends."""
        if self._connect_failed:
            self._publish_status(SessionStatus.ENDED)
            return
        try:
            await self._event_loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[kids_teacher_realtime] event loop crashed: %s", exc)
            self._publish_status(SessionStatus.ERROR, detail=str(exc))
        finally:
            if not self._stopped:
                # Stream ended without an explicit stop() call — surface
                # an ENDED status so callers know the session is done.
                self._publish_status(SessionStatus.ENDED)

    @property
    def recent_turns(self) -> tuple[SessionMemoryTurn, ...]:
        return tuple(self._memory)

    # ------------------------------------------------------------------
    # Internal event pump
    # ------------------------------------------------------------------

    async def _event_loop(self) -> None:
        events: AsyncIterator[dict] = self._backend.events()
        async for event in events:
            if self._stopped:
                return
            await self._dispatch(event)

    async def _dispatch(self, event: dict) -> None:
        event_type = event.get("type", "")
        if event_type == "input.speech_started":
            await self._on_speech_started()
        elif event_type == "input.speech_stopped":
            # VAD end-of-speech signal — no action needed for the handler;
            # forward the edge so motion-director hooks can update gaze gain.
            self._notify_hook("on_speech_stopped")
        elif event_type == "input_transcript.delta":
            await self._on_input_delta(event)
        elif event_type == "input_transcript.final":
            self._on_input_final(event)
        elif event_type == "assistant_transcript.delta":
            await self._on_assistant_delta(event)
        elif event_type == "assistant_transcript.final":
            self._on_assistant_final(event)
        elif event_type == "audio.chunk":
            self._on_audio_chunk(event)
        elif event_type == "tool.call":
            await self._on_tool_call(event)
        elif event_type == "response.done":
            self._on_response_done()
        elif event_type == "session.reconnecting":
            self._on_reconnecting(event)
        elif event_type == "session.reconnected":
            self._on_reconnected(event)
        elif event_type == "error":
            self._on_error(event)
        else:
            logger.debug("[kids_teacher_realtime] ignoring event type=%s", event_type)

    async def _on_speech_started(self) -> None:
        """Earliest barge-in signal from server-side VAD."""
        if self._assistant_active:
            await self._cancel_active_response(reason="input.speech_started")
        self._notify_hook("on_speech_started")

    async def _on_input_delta(self, event: dict) -> None:
        text = event.get("text", "") or ""
        language = event.get("language")
        # Barge-in: if the assistant is speaking and the child just started
        # producing transcript text, cancel the in-flight response.
        if self._assistant_active:
            await self._cancel_active_response(reason="input_transcript.delta")
        self._publish_transcript(
            Speaker.CHILD, text, is_partial=True, language=language
        )

    def _on_input_final(self, event: dict) -> None:
        text = event.get("text", "") or ""
        language = event.get("language")
        self._publish_transcript(
            Speaker.CHILD, text, is_partial=False, language=language
        )
        self._remember(Speaker.CHILD, text, language)

    async def _on_assistant_delta(self, event: dict) -> None:
        # Open the barge-in gate so a child speech_started in this window
        # cancels properly. Do NOT publish SPEAKING here — that would start
        # the robot's speak animation before any audio is queued, since
        # Gemini Live can ship transcript text well ahead of the matching
        # audio. SPEAKING is published from _on_audio_chunk so motion and
        # voice start together.
        if not self._assistant_active:
            self._assistant_active = True
            logger.info("[kids_teacher_realtime] assistant response started")
        text = event.get("text", "") or ""
        # Refusal intercept runs BEFORE we publish the partial so we can
        # short-circuit before downstream consumers see the canned
        # "I'm just a language model" text. The transcript stream leads
        # audio by 200-500ms, so detecting on a partial gives the bridge
        # time to flush the speaker pipeline before the audio plays.
        # Once the intercept has fired in a turn, every subsequent
        # delta is also dropped — the turn has been cancelled and any
        # follow-on text would just leak refusal fragments into the
        # transcript stream.
        if self._refusal_handled_this_turn:
            return
        if text:
            self._assistant_partial_buffer += text
            if is_refusal(self._assistant_partial_buffer):
                await self._handle_refusal_intercept()
                return
        self._publish_transcript(Speaker.ASSISTANT, text, is_partial=True)

    def _on_assistant_final(self, event: dict) -> None:
        text = event.get("text", "") or ""
        language = event.get("language")
        self._publish_transcript(
            Speaker.ASSISTANT, text, is_partial=False, language=language
        )
        self._remember(Speaker.ASSISTANT, text, language)

    def _on_audio_chunk(self, event: dict) -> None:
        audio = event.get("audio")
        if not audio:
            return
        # Open the barge-in gate if audio leads transcript. Gemini's native-audio
        # path can ship audio chunks before any assistant transcript delta; if
        # we only flipped _assistant_active on transcript, a speech_started in
        # that window would no-op and audio would keep playing past interrupt.
        if not self._assistant_active:
            self._assistant_active = True
            logger.info(
                "[kids_teacher_realtime] assistant response started (audio-first)"
            )
        # Hand the chunk to playback BEFORE publishing SPEAKING so the speaker
        # pipeline starts decoding in parallel with the robot's speak animation
        # — both are triggered off this same chunk, but queueing first lets
        # the playback thread make progress even if the bridge's status hook
        # spends a few hundred ms wiring up the motion. The pending queue is
        # non-authoritative; it just lets interrupt() confirm a drain happened.
        try:
            self._pending_audio.put_nowait(audio)
        except asyncio.QueueFull:  # pragma: no cover - unbounded queue
            pass
        try:
            self._hooks.start_assistant_playback(audio)
        except Exception as exc:
            logger.warning("[kids_teacher_realtime] playback hook raised: %s", exc)
        if not self._speaking_published:
            self._speaking_published = True
            self._publish_status(SessionStatus.SPEAKING)

    async def _on_tool_call(self, event: dict) -> None:
        """Route a function tool call to the runtime hook and ack the model.

        The hook may run synchronously (gesture dispatch is non-blocking
        — just a deque + composer.play_clip) or return an awaitable for
        I/O-bound tools (the tools-framework registry's GCS-backed
        location store). If the result is a coroutine we await it before
        shipping the ack so async tools don't drop their reply on the
        floor.
        """
        handler = getattr(self._hooks, "handle_tool_call", None)
        if handler is None:
            logger.debug(
                "[kids_teacher_realtime] tool.call ignored: hooks expose no handle_tool_call"
            )
            return
        call_id = str(event.get("call_id") or "")
        name = str(event.get("name") or "")
        arguments = event.get("arguments", "")
        if not isinstance(arguments, str):
            arguments = ""
        try:
            output = handler(call_id, name, arguments)
            if asyncio.iscoroutine(output):
                output = await output
        except Exception as exc:
            logger.warning("[kids_teacher_realtime] handle_tool_call raised: %s", exc)
            return
        if not call_id or output is None:
            return
        try:
            await self._backend.send_tool_response(call_id, output)
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] send_tool_response raised: %s", exc
            )

    def _on_response_done(self) -> None:
        logger.info("[kids_teacher_realtime] response.done — turn complete")
        self._assistant_active = False
        self._speaking_published = False
        self._assistant_partial_buffer = ""
        self._refusal_handled_this_turn = False
        self._drain_pending_audio()
        self._publish_status(SessionStatus.LISTENING)

    def _on_reconnecting(self, event: dict) -> None:
        """Backend is rebuilding a dropped Gemini Live session.

        Drop any in-flight assistant playback (the chunks belong to the
        dead socket) and surface a RECONNECTING status. The robot bridge
        uses this to play a short "one moment" cue instead of going silent
        — to a 4-year-old, 1–2s of dead air after a turn registers as
        "robot is broken".
        """
        detail = event.get("message")
        logger.info("[kids_teacher_realtime] backend reconnecting (%s)", detail or "")
        self._assistant_active = False
        self._speaking_published = False
        self._assistant_partial_buffer = ""
        self._refusal_handled_this_turn = False
        self._drain_pending_audio()
        try:
            self._hooks.stop_assistant_playback()
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] stop_assistant_playback raised during reconnect: %s",
                exc,
            )
        self._publish_status(
            SessionStatus.RECONNECTING, detail=str(detail) if detail else None
        )

    def _on_reconnected(self, event: dict) -> None:
        """Backend successfully rebuilt the session — return to LISTENING."""
        detail = event.get("message")
        logger.info("[kids_teacher_realtime] backend reconnected (%s)", detail or "")
        self._publish_status(SessionStatus.LISTENING)

    def _on_error(self, event: dict) -> None:
        # Fallback: surface a short safe line and return to listening. We
        # deliberately keep the session alive per FR8/NFR2.
        detail = event.get("message") or "backend error"
        self._publish_status(SessionStatus.ERROR, detail=str(detail))
        self._publish_transcript(
            Speaker.ASSISTANT,
            _FALLBACK_ASSISTANT_LINE,
            is_partial=False,
            language=self._config.default_explanation_language,
        )
        self._assistant_active = False
        self._drain_pending_audio()
        self._publish_status(SessionStatus.LISTENING)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _handle_refusal_intercept(self) -> None:
        """Hard-cut the in-flight refusal and force a fresh backend session.

        Triggered by :meth:`_on_assistant_delta` when the accumulated
        assistant transcript trips :func:`is_refusal`. Three jobs:

        1. Suppress what's already been queued for the speaker pipeline
           — Gemini Live's transcript stream leads audio by 200-500ms so
           the refusal text usually arrives before the matching audio
           chunk; flushing here means the child hears at most a syllable.
        2. Notify the bridge so it plays the recovery cue line. The tier
           (soft vs escalated) depends on whether a previous refusal
           landed within :data:`_REFUSAL_ESCALATION_WINDOW_SECONDS` —
           the second-strike case promotes the cue from "let me think"
           to "ask a grown-up", matching the design discussion on issue
           #52.
        3. Drop the backend's resumption handle and reconnect so the
           server-side context no longer carries the refusal in its
           assistant-history window. Without the handle drop the next
           turn would mirror the refusal again on its own.

        Marked one-shot per turn via :attr:`_refusal_handled_this_turn`
        so a refusal that spans multiple deltas only triggers the
        intercept once.
        """
        self._refusal_handled_this_turn = True
        logger.info(
            "[kids_teacher_realtime] refusal intercept fired (buffer=%r)",
            self._assistant_partial_buffer[:80],
        )

        # Hard-cut local playback first so any audio already pushed to
        # the speaker pipeline stops before the next chunk plays.
        try:
            self._hooks.stop_assistant_playback()
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] stop_assistant_playback raised during refusal intercept: %s",
                exc,
            )
        self._assistant_active = False
        self._speaking_published = False
        self._drain_pending_audio()

        # Tier the recovery cue. Use the handler's clock so tests with a
        # monotonic fake clock can drive both branches deterministically.
        now = self._clock()
        escalated = (
            self._last_refusal_at is not None
            and (now - self._last_refusal_at) <= _REFUSAL_ESCALATION_WINDOW_SECONDS
        )
        self._last_refusal_at = now

        # Notify the bridge before kicking the reconnect — the cue plays
        # in parallel with the reconnect dance, filling what would
        # otherwise be silent reconnect time.
        notify = getattr(self._hooks, "on_refusal_recovery", None)
        if notify is not None:
            try:
                notify(escalated=escalated)
            except Exception as exc:
                logger.warning(
                    "[kids_teacher_realtime] on_refusal_recovery raised: %s", exc
                )

        # Drop the resumption handle and reconnect. The Gemini backend
        # turns this into a fresh-handle reconnect that emits its own
        # session.reconnecting / session.reconnected events; the
        # OpenAI backend no-ops.
        try:
            await self._backend.reset_session()
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] reset_session raised during refusal intercept: %s",
                exc,
            )

    async def _cancel_active_response(self, *, reason: str = "unspecified") -> None:
        """Cancel the in-flight assistant response and flush queued audio.

        Design decision: a fresh assistant response while one is active is
        treated as a barge-in — we cancel the previous response and let
        the new one take over. This keeps ordering deterministic: at any
        point the handler is tracking at most one active assistant stream.
        """
        if self._assistant_active:
            logger.info(
                "[kids_teacher_realtime] cancelling active assistant response (reason=%s)",
                reason,
            )
            try:
                await self._backend.cancel_response()
            except Exception as exc:
                logger.warning(
                    "[kids_teacher_realtime] cancel_response raised: %s", exc
                )
            self._assistant_active = False
        else:
            logger.debug(
                "[kids_teacher_realtime] _cancel_active_response no-op (reason=%s, not assistant_active)",
                reason,
            )
        self._speaking_published = False
        self._assistant_partial_buffer = ""
        self._refusal_handled_this_turn = False
        self._drain_pending_audio()
        try:
            self._hooks.stop_assistant_playback()
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] stop_assistant_playback raised: %s", exc
            )

    def _drain_pending_audio(self) -> None:
        while True:
            try:
                self._pending_audio.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _publish_transcript(
        self,
        speaker: Speaker,
        text: str,
        *,
        is_partial: bool,
        language: Optional[str] = None,
    ) -> None:
        event = KidsTranscriptEvent(
            speaker=speaker,
            text=text,
            is_partial=is_partial,
            timestamp_ms=self._now_ms(),
            session_id=self._config.session_id,
            language=language,
        )
        try:
            self._hooks.publish_transcript(event)
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] publish_transcript raised: %s", exc
            )

    def _collect_additional_tool_specs(self) -> Optional[list]:
        """Collect optional extra tool specs from the hooks (e.g. motion-director).

        Hooks that don't provide tools simply omit the method; we fall back
        to ``None`` so ``build_session_payload`` keeps the existing behaviour.
        """
        method = getattr(self._hooks, "additional_tool_specs", None)
        if method is None:
            return None
        try:
            specs = method()
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] additional_tool_specs raised: %s", exc
            )
            return None
        if not specs:
            return None
        return list(specs)

    def _collect_additional_instructions(self) -> Optional[str]:
        """Optional system-prompt extension provided by the hooks layer."""
        method = getattr(self._hooks, "additional_instructions", None)
        if method is None:
            return None
        try:
            text = method()
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] additional_instructions raised: %s", exc
            )
            return None
        if not text:
            return None
        return str(text)

    def _notify_hook(self, method_name: str) -> None:
        """Call an optional zero-arg hook method, swallowing any exception."""
        method = getattr(self._hooks, method_name, None)
        if method is None:
            return
        try:
            method()
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] %s raised: %s", method_name, exc
            )

    def _publish_status(
        self, status: SessionStatus, *, detail: Optional[str] = None
    ) -> None:
        event = KidsStatusEvent(
            status=status,
            session_id=self._config.session_id,
            timestamp_ms=self._now_ms(),
            detail=detail,
        )
        try:
            self._hooks.publish_status(event)
        except Exception as exc:
            logger.warning(
                "[kids_teacher_realtime] publish_status raised: %s", exc
            )

    def _remember(
        self, speaker: Speaker, text: str, language: Optional[str]
    ) -> None:
        if not text:
            return
        self._memory.append(
            SessionMemoryTurn(
                speaker=speaker,
                text=text,
                timestamp_ms=self._now_ms(),
                language=language,
            )
        )

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

