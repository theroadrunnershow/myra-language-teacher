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
    ) -> None:
        self._config = config
        self._backend = backend
        self._hooks = hooks
        self._clock = clock or time.time
        self._memory: Deque[SessionMemoryTurn] = deque(maxlen=max(1, memory_turns))

        self._started = False
        self._stopped = False
        self._connect_failed = False
        self._run_task: Optional[asyncio.Task] = None

        # Track whether an assistant response is actively streaming so we
        # can route barge-in and de-dupe overlapping response streams.
        self._assistant_active = False
        # Pending assistant audio chunks that have not yet been played.
        # Drained on barge-in or stop.
        self._pending_audio: asyncio.Queue[bytes] = asyncio.Queue()

    # ------------------------------------------------------------------
    # Public lifecycle API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect the backend and move the session into LISTENING state."""
        if self._started:
            return
        payload = build_session_payload(self._config)
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

    async def push_audio(self, chunk: bytes) -> None:
        """Forward a child audio chunk to the backend."""
        if self._stopped:
            return
        await self._backend.send_audio(chunk)

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
            # VAD end-of-speech signal — no action needed; the final
            # transcript event drives downstream behavior.
            pass
        elif event_type == "input_transcript.delta":
            await self._on_input_delta(event)
        elif event_type == "input_transcript.final":
            self._on_input_final(event)
        elif event_type == "assistant_transcript.delta":
            self._on_assistant_delta(event)
        elif event_type == "assistant_transcript.final":
            self._on_assistant_final(event)
        elif event_type == "audio.chunk":
            self._on_audio_chunk(event)
        elif event_type == "response.done":
            self._on_response_done()
        elif event_type == "error":
            self._on_error(event)
        else:
            logger.debug("[kids_teacher_realtime] ignoring event type=%s", event_type)

    async def _on_speech_started(self) -> None:
        """Earliest barge-in signal from server-side VAD."""
        if self._assistant_active:
            await self._cancel_active_response(reason="input.speech_started")

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

    def _on_assistant_delta(self, event: dict) -> None:
        # Starting (or continuing) an assistant response. On the first delta
        # we flip state to SPEAKING.
        if not self._assistant_active:
            self._assistant_active = True
            logger.info("[kids_teacher_realtime] assistant response started")
            self._publish_status(SessionStatus.SPEAKING)
        text = event.get("text", "") or ""
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
        # Forward to playback. We also track chunks in the pending queue so
        # interrupt() can confirm a drain happened — but the queue is
        # non-authoritative, playback is driven by hooks.
        try:
            self._pending_audio.put_nowait(audio)
        except asyncio.QueueFull:  # pragma: no cover - unbounded queue
            pass
        try:
            self._hooks.start_assistant_playback(audio)
        except Exception as exc:
            logger.warning("[kids_teacher_realtime] playback hook raised: %s", exc)

    def _on_response_done(self) -> None:
        logger.info("[kids_teacher_realtime] response.done — turn complete")
        self._assistant_active = False
        self._drain_pending_audio()
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

