"""Robot/headless session orchestrator for kids-teacher mode.

Real robot audio bridge is wired in a separate Phase 5 change. This module
provides the transport-agnostic orchestration: it glues profile + backend +
realtime handler + runtime hooks + review store behind a single
``run_kids_teacher_session()`` entry point, fully testable against the
scripted :class:`FakeRealtimeBackend`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from kids_review_store import KidsReviewStore
from kids_safety import (
    TopicDecision,
    classify_topic,
    family_safe_response,
    redirect_response,
    refusal_response,
    validate_output,
)
from kids_teacher_backend import RealtimeBackend
from kids_teacher_profile import load_profile
from kids_teacher_realtime import KidsTeacherRealtimeHandler
from kids_teacher_types import (
    KidsStatusEvent,
    KidsTeacherProfile,
    KidsTeacherRuntimeHooks,
    KidsTeacherSessionConfig,
    KidsTranscriptEvent,
    SessionStatus,
    Speaker,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hook helpers
# ---------------------------------------------------------------------------


class NullRuntimeHooks:
    """No-op :class:`KidsTeacherRuntimeHooks` implementation for tests."""

    def start_assistant_playback(self, audio_chunk: bytes) -> None:
        return None

    def stop_assistant_playback(self) -> None:
        return None

    def publish_transcript(self, event: KidsTranscriptEvent) -> None:
        return None

    def publish_status(self, event: KidsStatusEvent) -> None:
        return None

    def persist_artifact(
        self,
        event: KidsTranscriptEvent,
        audio: Optional[bytes] = None,
    ) -> None:
        return None


class RecordingRuntimeHooks:
    """Recording hook implementation so tests can assert on call order."""

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


def build_robot_hooks_stub(robot_controller: object) -> KidsTeacherRuntimeHooks:
    """Deprecated shim. Use :func:`build_robot_hooks` instead.

    Retained so the original stub contract (raises ``NotImplementedError``)
    is preserved for any test or caller that still imports this name. New
    code should call :func:`build_robot_hooks`, which returns a real
    :class:`KidsTeacherRobotHooks` instance.
    """
    raise NotImplementedError(
        "build_robot_hooks_stub is deprecated; call build_robot_hooks() "
        "to get a real KidsTeacherRobotHooks for the Reachy Mini bridge."
    )


def build_robot_hooks(robot_controller: object) -> KidsTeacherRuntimeHooks:
    """Build the real robot audio bridge hooks for a running session.

    Imports :mod:`kids_teacher_robot_bridge` lazily so this module stays
    importable when the robot SDK (or its optional audio deps) is missing.
    Callers are responsible for ``.start()``/``.stop()`` on the returned
    hooks — the flow wires that lifecycle around ``run_kids_teacher_session``.
    """
    from kids_teacher_robot_bridge import KidsTeacherRobotHooks

    return KidsTeacherRobotHooks(robot_controller=robot_controller)


# ---------------------------------------------------------------------------
# Flow deps + entry point
# ---------------------------------------------------------------------------


@dataclass
class KidsTeacherFlowDeps:
    """Injected dependencies so tests can substitute fakes freely."""

    backend_factory: Callable[[], RealtimeBackend]
    hooks_factory: Callable[[], KidsTeacherRuntimeHooks]
    profile_loader: Callable[[], KidsTeacherProfile] = field(
        default_factory=lambda: load_profile
    )
    review_store: Optional[KidsReviewStore] = None
    clock: Callable[[], float] = field(default_factory=lambda: time.time)


async def run_kids_teacher_session(
    *,
    config: KidsTeacherSessionConfig,
    deps: KidsTeacherFlowDeps,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Full session lifecycle.

    1. Emit LISTENING status via hooks (via handler.start()).
    2. If review_store: review_store.start_session(config.session_id, ...).
    3. Build the realtime handler with injected backend + hooks.
    4. await handler.start().
    5. await handler.run() until stop_event is set OR handler emits ENDED.
    6. If review_store: flush and end_session(config.session_id).
    7. Emit ENDED status (handler already emits on clean stop; we ensure it).

    Never raises on backend errors — they surface as ERROR status events.
    """
    backend = deps.backend_factory()
    hooks = deps.hooks_factory()
    review_store = deps.review_store

    if review_store is not None:
        try:
            review_store.start_session(
                config.session_id,
                metadata={
                    "model": config.model,
                    "profile": config.profile.name,
                    "default_explanation_language": config.default_explanation_language,
                    "enabled_languages": list(config.enabled_languages),
                },
            )
        except Exception as exc:
            logger.warning(
                "[kids_teacher_flow] review_store.start_session raised: %s", exc
            )

    # Wrap hooks so transcripts + assistant audio can be piped through the
    # review store when enabled. This keeps the handler's hook interface
    # single-responsibility while still letting the flow layer persist.
    effective_hooks = _wrap_hooks_with_review(hooks, review_store, config)
    # Safety wrapper sits outermost: it sees every transcript event before
    # review persistence and can replace unsafe assistant output.
    safety_hooks = _SafetyHooks(effective_hooks, config)
    effective_hooks = safety_hooks

    handler = KidsTeacherRealtimeHandler(
        config=config,
        backend=backend,
        hooks=effective_hooks,
        clock=deps.clock,
    )
    # Let the safety layer stop an in-flight assistant response when it
    # detects unsafe child input. Done after handler construction to avoid
    # the hooks <-> handler circular dependency.
    safety_hooks.set_interrupt(handler.interrupt)

    try:
        await handler.start()
        run_task = asyncio.create_task(handler.run())

        if stop_event is None:
            await run_task
        else:
            stop_waiter = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                {run_task, stop_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # If stop_event won, cancel the run task and close the handler.
            if stop_waiter in done and not run_task.done():
                await handler.stop()
                try:
                    await run_task
                except (asyncio.CancelledError, Exception):
                    pass
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
    except Exception as exc:
        # Handler already publishes ERROR status for backend failures; we
        # catch here only to make sure review-store cleanup still runs.
        logger.exception("[kids_teacher_flow] session crashed: %s", exc)

    if review_store is not None:
        try:
            review_store.end_session(config.session_id)
            review_store.flush_if_needed(force=True)
            if review_store.sync_to_gcs_policy == "session_end":
                review_store.sync_to_object_store(force=False)
        except Exception as exc:
            logger.warning("[kids_teacher_flow] review_store teardown: %s", exc)


# ---------------------------------------------------------------------------
# Internal: hook wrapper that feeds the review store
# ---------------------------------------------------------------------------


class _ReviewStoreHooks:
    """Delegating hook wrapper that also feeds transcripts into a review store.

    When ``review_store`` is ``None``, this class is not used — the caller's
    hooks go straight to the handler unchanged.
    """

    def __init__(
        self,
        inner: KidsTeacherRuntimeHooks,
        review_store: KidsReviewStore,
        config: KidsTeacherSessionConfig,
    ) -> None:
        self._inner = inner
        self._review_store = review_store
        self._config = config

    def start_assistant_playback(self, audio_chunk: bytes) -> None:
        self._inner.start_assistant_playback(audio_chunk)

    def stop_assistant_playback(self) -> None:
        self._inner.stop_assistant_playback()

    def publish_transcript(self, event: KidsTranscriptEvent) -> None:
        self._inner.publish_transcript(event)
        # Only persist finals to keep the review log readable and small.
        if not event.is_partial:
            try:
                self._review_store.record_transcript(event)
            except Exception as exc:
                logger.warning(
                    "[kids_teacher_flow] review_store.record_transcript: %s", exc
                )

    def publish_status(self, event: KidsStatusEvent) -> None:
        self._inner.publish_status(event)

    def persist_artifact(
        self,
        event: KidsTranscriptEvent,
        audio: Optional[bytes] = None,
    ) -> None:
        self._inner.persist_artifact(event, audio)
        if audio:
            try:
                self._review_store.record_audio(
                    session_id=event.session_id,
                    speaker=event.speaker,
                    audio_bytes=audio,
                    timestamp_ms=event.timestamp_ms,
                    language=event.language,
                )
            except Exception as exc:
                logger.warning(
                    "[kids_teacher_flow] review_store.record_audio: %s", exc
                )


def _wrap_hooks_with_review(
    inner: KidsTeacherRuntimeHooks,
    review_store: Optional[KidsReviewStore],
    config: KidsTeacherSessionConfig,
) -> KidsTeacherRuntimeHooks:
    if review_store is None or not review_store.is_enabled:
        return inner
    return _ReviewStoreHooks(inner, review_store, config)


# ---------------------------------------------------------------------------
# Internal: safety wrapper
# ---------------------------------------------------------------------------


class _SafetyHooks:
    """Intercepts transcript events to enforce the safety policy.

    For child finals:
      - classify via :func:`classify_topic` with the session admin policy
      - on non-ALLOW, publish the child event and inject a safe assistant
        final transcript with the appropriate fallback line

    For assistant finals:
      - run :func:`validate_output`; if the output was replaced, publish the
        replaced text instead of the original

    Backend cancellation is Phase 5 work — this wrapper only controls what the
    UI/review layer sees. It never drops the original child transcript, so the
    review log still shows what was asked.
    """

    def __init__(
        self,
        inner: KidsTeacherRuntimeHooks,
        config: KidsTeacherSessionConfig,
    ) -> None:
        self._inner = inner
        self._config = config
        self._interrupt: Optional[Callable[[], Any]] = None

    def set_interrupt(self, interrupt_coro: Callable[[], Any]) -> None:
        """Register an async callable invoked on unsafe child input.

        Expected to be ``handler.interrupt`` — flushes queued assistant audio
        and cancels the in-flight backend response.
        """
        self._interrupt = interrupt_coro

    def start_assistant_playback(self, audio_chunk: bytes) -> None:
        self._inner.start_assistant_playback(audio_chunk)

    def stop_assistant_playback(self) -> None:
        self._inner.stop_assistant_playback()

    def publish_status(self, event: KidsStatusEvent) -> None:
        self._inner.publish_status(event)

    def persist_artifact(
        self,
        event: KidsTranscriptEvent,
        audio: Optional[bytes] = None,
    ) -> None:
        self._inner.persist_artifact(event, audio)

    def publish_transcript(self, event: KidsTranscriptEvent) -> None:
        if event.is_partial:
            self._inner.publish_transcript(event)
            return

        if event.speaker == Speaker.CHILD:
            self._inner.publish_transcript(event)
            self._handle_child_final(event)
            return

        if event.speaker == Speaker.ASSISTANT:
            validated, replaced = validate_output(
                event.text,
                language=event.language or "english",
            )
            if replaced:
                logger.warning(
                    "[kids_teacher_flow] assistant output replaced by safety policy"
                )
                safe_event = KidsTranscriptEvent(
                    speaker=Speaker.ASSISTANT,
                    text=validated,
                    is_partial=False,
                    timestamp_ms=event.timestamp_ms,
                    session_id=event.session_id,
                    language=event.language,
                )
                self._inner.publish_transcript(safe_event)
                return

        self._inner.publish_transcript(event)

    def _handle_child_final(self, event: KidsTranscriptEvent) -> None:
        classification = classify_topic(
            event.text,
            admin_policy=self._config.admin_policy,
        )
        if classification.decision == TopicDecision.ALLOW:
            return

        language = event.language or self._config.default_explanation_language
        if classification.decision == TopicDecision.REFUSE:
            safe_text = refusal_response(language=language)
        elif classification.decision == TopicDecision.FAMILY_SAFE_ANSWER:
            safe_text = family_safe_response(classification.category, language=language)
        else:  # REDIRECT
            safe_text = redirect_response(
                language=language,
                redirect_to=self._config.admin_policy.redirect_to,
            )

        logger.info(
            "[kids_teacher_flow] safety decision=%s category=%s",
            classification.decision.value,
            classification.category,
        )
        # Stop any in-flight assistant response the backend has already
        # started based on the child's audio, before we emit the safe line.
        if self._interrupt is not None:
            try:
                coro = self._interrupt()
                if asyncio.iscoroutine(coro):
                    asyncio.get_running_loop().create_task(coro)
            except RuntimeError:
                # No running loop — can't schedule. Falls back to transcript-only.
                logger.warning("[kids_teacher_flow] no event loop for safety interrupt")
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("[kids_teacher_flow] safety interrupt failed: %s", exc)
        safe_event = KidsTranscriptEvent(
            speaker=Speaker.ASSISTANT,
            text=safe_text,
            is_partial=False,
            timestamp_ms=event.timestamp_ms,
            session_id=event.session_id,
            language=language,
        )
        self._inner.publish_transcript(safe_event)
