"""OpenAI Realtime backend adapter for kids-teacher mode.

This module is the ONLY place in the codebase that touches the OpenAI SDK
directly. Nothing above this layer should ``import openai``; the realtime
handler talks to a :class:`RealtimeBackend` protocol instead. Model
selection is driven by the ``KIDS_TEACHER_REALTIME_MODEL`` env var and
constrained to :data:`ALLOWED_REALTIME_MODELS`.

The concrete :class:`OpenAIRealtimeBackend` lazy-imports ``openai`` inside
``connect()`` so tests (and lightweight CI images) never require the real
SDK or an API key.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import logging
import os
from typing import Any, AsyncIterator, Callable, Optional, Protocol

from env_loader import load_project_dotenv
from kids_teacher_types import (
    ALLOWED_REALTIME_MODELS,
    KidsTeacherSessionConfig,
)

load_project_dotenv()

logger = logging.getLogger(__name__)


DEFAULT_REALTIME_MODEL = "gpt-realtime"
REALTIME_MODEL_ENV_VAR = "KIDS_TEACHER_REALTIME_MODEL"

# Transcription model used by OpenAI Realtime for input transcripts.
# Kept as a module-level constant so it is easy to update or override in
# one place; not currently env-driven because the transcript model is not
# user-facing.
_INPUT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


class BackendConfigError(Exception):
    """Raised when realtime backend config (e.g. model selection) is invalid."""


def _decode_audio_delta(delta: Any) -> bytes:
    """Decode an OpenAI Realtime audio delta field to raw PCM16 bytes.

    The SDK returns a base64 string; older/test paths may pass raw bytes.
    Invalid base64 returns ``b""`` so a bad payload does not crash the
    reader loop.
    """
    if isinstance(delta, bytes):
        return delta
    if not delta:
        return b""
    try:
        return base64.b64decode(delta)
    except (binascii.Error, ValueError):
        logger.warning("[kids_teacher_backend] invalid base64 audio delta; dropping chunk")
        return b""


def _encode_audio_chunk(chunk: bytes) -> str:
    """Encode outbound PCM16 bytes for OpenAI ``input_audio_buffer.append``."""
    return base64.b64encode(chunk).decode("ascii")


def resolve_realtime_model(env_value: Optional[str] = None) -> str:
    """Return the realtime model from env, defaulting to gpt-realtime.

    - ``env_value=None`` reads :data:`REALTIME_MODEL_ENV_VAR` from the
      process environment.
    - Blank/unset values fall back to :data:`DEFAULT_REALTIME_MODEL`.
    - Values outside :data:`ALLOWED_REALTIME_MODELS` raise
      :class:`BackendConfigError`.
    """
    raw = env_value if env_value is not None else os.environ.get(REALTIME_MODEL_ENV_VAR)
    if raw is None:
        return DEFAULT_REALTIME_MODEL
    candidate = raw.strip()
    if not candidate:
        return DEFAULT_REALTIME_MODEL
    if candidate not in ALLOWED_REALTIME_MODELS:
        raise BackendConfigError(
            f"Invalid realtime model {candidate!r}. Must be one of "
            f"{sorted(ALLOWED_REALTIME_MODELS)}."
        )
    return candidate


def build_session_payload(
    config: KidsTeacherSessionConfig,
    *,
    modalities: tuple[str, ...] = ("audio", "text"),
) -> dict:
    """Build the JSON-serializable ``session.update`` payload.

    Pulls instructions, voice, and the tool allowlist out of
    ``config.profile``. V1 uses server-side VAD and OpenAI's
    ``gpt-4o-mini-transcribe`` for input transcripts.
    """
    profile = config.profile
    tools = [
        # Tool-spec lookup is deliberately NOT part of V1 — this is a
        # minimal stub so the backend can wire up allowlisted names.
        # Intern 2 / integration phase will replace this with real tool
        # specs when any tool is actually enabled.
        {"type": "function", "name": name}
        for name in profile.allowed_tools
    ]
    return {
        "instructions": profile.instructions,
        "voice": profile.voice,
        "modalities": list(modalities),
        "input_audio_transcription": {"model": _INPUT_TRANSCRIPTION_MODEL},
        "turn_detection": {"type": "server_vad"},
        "tools": tools,
        "tool_choice": "auto",
    }


class RealtimeBackend(Protocol):
    """Abstract async backend that the realtime handler talks to.

    The real OpenAI implementation and the test fake both implement this
    shape. The handler depends only on this protocol.
    """

    async def connect(self, session_payload: dict) -> None: ...

    async def send_audio(self, chunk: bytes) -> None: ...

    async def send_text(self, text: str) -> None: ...

    async def cancel_response(self) -> None: ...

    async def close(self) -> None: ...

    def events(self) -> AsyncIterator[dict]:
        """Yield backend events as normalized dicts.

        V1 event types (each dict has ``type`` plus the fields below):

          * ``input.speech_started``     ``{}``                              (server VAD detected child speech)
          * ``input.speech_stopped``     ``{}``                              (server VAD detected end of child speech)
          * ``input_transcript.delta``   ``{"text": str, "language": Optional[str]}``
          * ``input_transcript.final``   ``{"text": str, "language": Optional[str]}``
          * ``assistant_transcript.delta`` ``{"text": str}``
          * ``assistant_transcript.final`` ``{"text": str, "language": Optional[str]}``
          * ``audio.chunk``              ``{"audio": bytes}`` (raw PCM16)
          * ``response.done``            ``{}``
          * ``error``                    ``{"message": str}``
        """
        ...


# ---------------------------------------------------------------------------
# Concrete OpenAI backend
# ---------------------------------------------------------------------------


class OpenAIRealtimeBackend:
    """Concrete :class:`RealtimeBackend` that wraps OpenAI Realtime.

    ``openai`` is imported lazily inside :meth:`connect` so the module can
    be imported without the SDK installed. ``client_factory`` may be
    injected to override the default ``openai.AsyncOpenAI()`` construction
    in tests.
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._model = model or resolve_realtime_model()
        self._client_factory = client_factory
        self._client: Any = None
        self._connection: Any = None
        self._closed = False
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None

    @property
    def model(self) -> str:
        return self._model

    async def connect(self, session_payload: dict) -> None:
        """Open the realtime connection and start pumping raw SDK events."""
        client_factory = self._client_factory
        if client_factory is None:
            # Lazy import: never touch the SDK at module load time.
            import openai  # type: ignore

            client_factory = openai.AsyncOpenAI  # type: ignore[attr-defined]

        self._client = client_factory()
        try:
            self._connection = await self._open_connection(session_payload)
        except Exception as exc:  # pragma: no cover - hit only in integration
            logger.warning("[kids_teacher_backend] connect failed: %s", exc)
            raise
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _open_connection(self, session_payload: dict) -> Any:
        """Open the SDK-level connection and send the session.update payload.

        Isolated in its own method so the SDK call shape can be stubbed by
        subclasses without rewriting :meth:`connect`.
        """
        # openai>=1.59 exposes realtime under client.beta.realtime; later
        # stable releases promote it to client.realtime. Support both.
        realtime_ns = getattr(self._client, "realtime", None)
        if realtime_ns is None:
            realtime_ns = self._client.beta.realtime  # type: ignore[attr-defined]
        # SDK versions differ here:
        # - some return an awaitable connection directly
        # - openai==1.61 returns an AsyncRealtimeConnectionManager whose
        #   supported non-context-manager flow is ``await ...connect().enter()``
        connection = await self._enter_connection(
            realtime_ns.connect(model=self._model)  # type: ignore[attr-defined]
        )
        try:
            await connection.session.update(session=session_payload)  # type: ignore[attr-defined]
        except Exception:
            await connection.close()  # type: ignore[attr-defined]
            raise
        return connection

    @staticmethod
    async def _enter_connection(connection_or_manager: Any) -> Any:
        """Normalize SDK connection helpers across OpenAI client versions."""
        enter = getattr(connection_or_manager, "enter", None)
        if callable(enter):
            return await enter()
        if inspect.isawaitable(connection_or_manager):
            return await connection_or_manager
        aenter = getattr(connection_or_manager, "__aenter__", None)
        if callable(aenter):
            return await aenter()
        return connection_or_manager

    async def _reader_loop(self) -> None:
        """Translate raw SDK events into the normalized dict shape."""
        assert self._connection is not None
        try:
            async for raw_event in self._connection:  # type: ignore[attr-defined]
                normalized = self._normalize_event(raw_event)
                if normalized is not None:
                    await self._event_queue.put(normalized)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - integration path
            logger.warning("[kids_teacher_backend] reader loop error: %s", exc)
            await self._event_queue.put({"type": "error", "message": str(exc)})

    @staticmethod
    def _normalize_event(raw_event: Any) -> Optional[dict]:
        """Map a raw OpenAI Realtime event to our normalized dict shape.

        Event names follow the public OpenAI Realtime API. Acknowledgement
        events (``session.created``, ``session.updated``, ``response.created``,
        etc.) are silently dropped — the handler does not act on them.
        """
        raw_type = getattr(raw_event, "type", None) or (
            raw_event.get("type") if isinstance(raw_event, dict) else None
        )
        if raw_type is None:
            return None

        def _field(name: str, default: Any = None) -> Any:
            if isinstance(raw_event, dict):
                return raw_event.get(name, default)
            return getattr(raw_event, name, default)

        if raw_type == "input_audio_buffer.speech_started":
            return {"type": "input.speech_started"}
        if raw_type == "input_audio_buffer.speech_stopped":
            return {"type": "input.speech_stopped"}
        if raw_type == "conversation.item.input_audio_transcription.delta":
            return {
                "type": "input_transcript.delta",
                "text": _field("delta", ""),
                "language": _field("language"),
            }
        if raw_type == "conversation.item.input_audio_transcription.completed":
            return {
                "type": "input_transcript.final",
                "text": _field("transcript", ""),
                "language": _field("language"),
            }
        if raw_type == "response.audio_transcript.delta":
            return {
                "type": "assistant_transcript.delta",
                "text": _field("delta", ""),
            }
        if raw_type == "response.audio_transcript.done":
            return {
                "type": "assistant_transcript.final",
                "text": _field("transcript", ""),
                "language": _field("language"),
            }
        if raw_type == "response.audio.delta":
            # OpenAI returns audio chunks base64-encoded.
            return {
                "type": "audio.chunk",
                "audio": _decode_audio_delta(_field("delta", "")),
            }
        if raw_type == "response.done":
            return {"type": "response.done"}
        if raw_type == "error":
            raw_error = _field("error")
            if isinstance(raw_error, dict):
                message = raw_error.get("message") or "unknown error"
            else:
                message = raw_error or _field("message") or "unknown error"
            return {"type": "error", "message": str(message)}
        return None

    async def send_audio(self, chunk: bytes) -> None:
        if self._connection is None or not chunk:
            return
        try:
            await self._connection.input_audio_buffer.append(  # type: ignore[attr-defined]
                audio=_encode_audio_chunk(chunk)
            )
        except Exception as exc:  # pragma: no cover - integration path
            logger.warning("[kids_teacher_backend] send_audio failed: %s", exc)
            await self._event_queue.put({"type": "error", "message": str(exc)})

    async def send_text(self, text: str) -> None:
        if self._connection is None:
            return
        try:
            await self._connection.conversation.item.create(  # type: ignore[attr-defined]
                item={"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
            )
            await self._connection.response.create()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - integration path
            logger.warning("[kids_teacher_backend] send_text failed: %s", exc)
            await self._event_queue.put({"type": "error", "message": str(exc)})

    async def cancel_response(self) -> None:
        if self._connection is None:
            return
        try:
            await self._connection.response.cancel()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - integration path
            logger.warning("[kids_teacher_backend] cancel_response failed: %s", exc)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._connection is not None:
            try:
                await self._connection.close()  # type: ignore[attr-defined]
            except Exception as exc:  # pragma: no cover - integration path
                logger.warning("[kids_teacher_backend] close failed: %s", exc)

    async def events(self) -> AsyncIterator[dict]:
        while True:
            event = await self._event_queue.get()
            yield event
            if event.get("type") == "response.done" and self._closed:
                return
