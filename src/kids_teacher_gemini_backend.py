"""Gemini Flash Live backend adapter for kids-teacher mode.

This module is the ONLY place in the codebase that touches the
``google-genai`` SDK directly. Nothing above this layer should import
``google.genai``; the realtime handler talks to the
:class:`RealtimeBackend` protocol defined in :mod:`kids_teacher_backend`
instead.

Model selection is driven by :data:`GEMINI_MODEL_ENV_VAR`
(``KIDS_TEACHER_GEMINI_MODEL``) and constrained to
:data:`kids_teacher_types.ALLOWED_GEMINI_MODELS`. The concrete
:class:`GeminiRealtimeBackend` lazy-imports ``google.genai`` inside
:meth:`connect` so tests (and lightweight CI images) never require the
real SDK or an API key.

Translation responsibilities kept in this file:

* OpenAI-shaped ``session_payload`` (the dict produced by
  :func:`kids_teacher_backend.build_session_payload`) → Gemini
  ``LiveConnectConfig`` fields.
* OpenAI voice names (e.g. ``alloy``) → Gemini prebuilt voice names
  (e.g. ``Kore``).
* Raw Gemini ``LiveServerMessage`` attributes → the 9 normalized event
  types the realtime handler consumes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator, Callable, Optional

import face_service
from env_loader import load_project_dotenv
from kids_teacher_backend import BackendConfigError
from kids_teacher_types import ALLOWED_GEMINI_MODELS
from memory_file import append as append_memory_file
from memory_file import remove_lines_matching_substring as memory_remove_substring

load_project_dotenv()

logger = logging.getLogger(__name__)


# Default targets AI Studio (api_key auth) since that's the path users
# hit with a free-tier key. Vertex users should override via
# KIDS_TEACHER_GEMINI_MODEL=gemini-live-2.5-flash-native-audio (the
# Vertex GA id, which is not available on AI Studio's v1beta endpoint).
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
GEMINI_MODEL_ENV_VAR = "KIDS_TEACHER_GEMINI_MODEL"
GEMINI_API_KEY_ENV_VAR = "GEMINI_API_KEY"

# Gemini Live expects raw PCM16 LE at 16 kHz mono on input. Output is
# PCM16 LE at 24 kHz mono (same as OpenAI Realtime — robot playback path
# is unchanged).
GEMINI_INPUT_SAMPLE_RATE = 16000
GEMINI_INPUT_MIME = f"audio/pcm;rate={GEMINI_INPUT_SAMPLE_RATE}"

# OpenAI voice names → Gemini prebuilt voice names. Kept tiny and
# explicit; unknown voices fall back to ``_DEFAULT_GEMINI_VOICE``.
# ``Kore`` is a warm female voice recommended for child-facing use per
# the migration research.
_DEFAULT_GEMINI_VOICE = "Kore"
_OPENAI_TO_GEMINI_VOICE: dict[str, str] = {
    "alloy": "Kore",
    "echo": "Puck",
    "shimmer": "Aoede",
    "ash": "Charon",
    "ballad": "Leda",
    "coral": "Aoede",
    "sage": "Kore",
    "verse": "Puck",
}

_REMEMBER_TOOL_NAME = "remember"
_REMEMBER_FACE_TOOL_NAME = "remember_face"
_FORGET_FACE_TOOL_NAME = "forget_face"

_REMEMBER_TOOL_PROMPT_APPENDIX = """
# Memory tool
- If the child or parent explicitly asks you to remember something about the child, call the `remember` tool with one short, third-person sentence about the child.
- If the child answers your earlier name question, call `remember` with `Their name is ...`.
- After calling `remember`, briefly say "Got it, I'll remember!"
- Never store an address, phone number, school name, password, login info, or medical ID in memory.

# Face tools
- When a grown-up or the child explicitly introduces someone in the room ("this is Aunt Priya", "remember, this is my friend Sara"), call `remember_face` with their name. Pass an optional `relationship` like "is Myra's aunt" so the relationship is also remembered.
- After `remember_face` returns status "ok", say: "Got it — I'll remember <name> next time!"
- If status is "no_face", say: "I can't see them clearly — can they look at me?"
- If status is "multiple_faces", say: "I see more than one face — can just <name> look at me?"
- If status is "capacity", say: "I'm out of room to remember new faces — ask a grown-up to forget someone first."
- If status is "unavailable", say: "I can't remember faces yet — ask a grown-up to set me up."
- When the parent or child says "forget X", call `forget_face` with that name.
- After `forget_face` returns status "ok", say: "Okay, I forgot <name>."
- If status is "not_found", say: "I don't think I remembered <name>."
""".strip()


def _gemini_model_supports_non_blocking_tools(model: str) -> bool:
    """Return whether this Gemini Live model supports async function calling."""
    return model.strip() != "gemini-3.1-flash-live-preview"


def _build_remember_tool(types_module: Any, *, non_blocking: bool) -> Any:
    kwargs: dict[str, Any] = {
        "name": _REMEMBER_TOOL_NAME,
        "description": (
            "Persist a fact the child or parent explicitly asked you to remember. "
            "fact must be one short, third-person sentence about the child. "
            "Do not store addresses, phone numbers, school names, passwords, "
            "login info, or medical IDs."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": (
                        "One short, third-person sentence about the child, "
                        "for example 'Their favourite colour is blue'."
                    ),
                }
            },
            "required": ["fact"],
            "additionalProperties": False,
        },
    }
    if non_blocking:
        kwargs["behavior"] = "NON_BLOCKING"
    declaration = types_module.FunctionDeclaration(**kwargs)
    return types_module.Tool(function_declarations=[declaration])


def _build_remember_face_tool(types_module: Any, *, non_blocking: bool) -> Any:
    kwargs: dict[str, Any] = {
        "name": _REMEMBER_FACE_TOOL_NAME,
        "description": (
            "Persist a face encoding for a person the child or parent introduced. "
            "Call when an adult voice or the child explicitly says 'this is X' or "
            "'remember X'. Pass an optional 'relationship' string like "
            "'is Myra's aunt' so the relationship is also stored as a memory fact."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the person to remember (e.g. 'Aunt Priya').",
                },
                "relationship": {
                    "type": "string",
                    "description": (
                        "Optional short phrase about how this person relates to the "
                        "child, e.g. \"is Myra's aunt\"."
                    ),
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    }
    if non_blocking:
        kwargs["behavior"] = "NON_BLOCKING"
    declaration = types_module.FunctionDeclaration(**kwargs)
    return types_module.Tool(function_declarations=[declaration])


def _build_forget_face_tool(types_module: Any, *, non_blocking: bool) -> Any:
    kwargs: dict[str, Any] = {
        "name": _FORGET_FACE_TOOL_NAME,
        "description": (
            "Forget a previously remembered face. Call when the parent or child "
            "says 'forget X'."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the person to forget.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    }
    if non_blocking:
        kwargs["behavior"] = "NON_BLOCKING"
    declaration = types_module.FunctionDeclaration(**kwargs)
    return types_module.Tool(function_declarations=[declaration])


def _append_remember_tool_instructions(instructions: str) -> str:
    base = (instructions or "").strip()
    if not base:
        return _REMEMBER_TOOL_PROMPT_APPENDIX
    return f"{base}\n\n{_REMEMBER_TOOL_PROMPT_APPENDIX}"


def _extract_remember_fact(function_call: Any) -> Optional[str]:
    args = _attr(function_call, "args")
    if args is None:
        args = _attr(function_call, "arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if not isinstance(args, dict):
        return None
    fact = args.get("fact")
    if not isinstance(fact, str):
        return None
    normalized = " ".join(fact.strip().split())
    return normalized or None


def _extract_call_args(function_call: Any) -> dict[str, Any]:
    args = _attr(function_call, "args")
    if args is None:
        args = _attr(function_call, "arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return {}
    if not isinstance(args, dict):
        return {}
    return args


def _extract_remember_face_args(function_call: Any) -> tuple[Optional[str], Optional[str]]:
    args = _extract_call_args(function_call)
    name = args.get("name")
    relationship = args.get("relationship")
    name_norm = " ".join(name.strip().split()) if isinstance(name, str) else ""
    rel_norm = (
        " ".join(relationship.strip().split())
        if isinstance(relationship, str) and relationship.strip()
        else None
    )
    return (name_norm or None, rel_norm)


def _extract_forget_face_name(function_call: Any) -> Optional[str]:
    args = _extract_call_args(function_call)
    name = args.get("name")
    if not isinstance(name, str):
        return None
    normalized = " ".join(name.strip().split())
    return normalized or None


def _build_function_response(
    types_module: Any,
    *,
    call_id: Any,
    name: str,
    response: dict[str, Any],
    non_blocking: bool,
) -> Any:
    kwargs: dict[str, Any] = {
        "id": call_id,
        "name": name,
        "response": response,
    }
    if non_blocking:
        kwargs["scheduling"] = "SILENT"
    return types_module.FunctionResponse(**kwargs)


def resolve_gemini_model(env_value: Optional[str] = None) -> str:
    """Return the Gemini Live model id from env, defaulting to the GA model.

    - ``env_value=None`` reads :data:`GEMINI_MODEL_ENV_VAR` from the
      process environment.
    - Blank/unset values fall back to :data:`DEFAULT_GEMINI_MODEL`.
    - Values outside :data:`ALLOWED_GEMINI_MODELS` raise
      :class:`BackendConfigError` so preview variants cannot slip into
      production unnoticed.
    """
    raw = env_value if env_value is not None else os.environ.get(GEMINI_MODEL_ENV_VAR)
    if raw is None:
        return DEFAULT_GEMINI_MODEL
    candidate = raw.strip()
    if not candidate:
        return DEFAULT_GEMINI_MODEL
    if candidate not in ALLOWED_GEMINI_MODELS:
        raise BackendConfigError(
            f"Invalid Gemini model {candidate!r}. Must be one of "
            f"{sorted(ALLOWED_GEMINI_MODELS)}."
        )
    return candidate


def map_openai_voice_to_gemini(voice: Optional[str]) -> str:
    """Translate an OpenAI voice name to a Gemini prebuilt voice name.

    Unknown/empty input maps to :data:`_DEFAULT_GEMINI_VOICE`.
    """
    if not voice:
        return _DEFAULT_GEMINI_VOICE
    return _OPENAI_TO_GEMINI_VOICE.get(voice.strip().lower(), _DEFAULT_GEMINI_VOICE)


def build_gemini_live_config(
    session_payload: dict,
    types_module: Any,
    *,
    model: str = DEFAULT_GEMINI_MODEL,
) -> Any:
    """Translate the OpenAI-shaped ``session_payload`` into ``LiveConnectConfig``.

    ``types_module`` is the imported ``google.genai.types`` module, passed
    in so the translation stays testable without the SDK installed.

    Fields mapped:

    * ``instructions`` → ``system_instruction``
    * ``voice`` → ``speech_config.voice_config.prebuilt_voice_config.voice_name``
    * ``modalities`` containing ``"audio"`` → ``response_modalities=["AUDIO"]``
      (kids-teacher is audio-first; text-only replies would break the robot).
    * ``input_audio_transcription``/``output_audio_transcription`` are
      **always enabled** (empty config dicts). The safety layer depends
      on child transcripts; without them topic classification regresses.
    * ``turn_detection`` with ``type == "server_vad"`` → Gemini's
      default automatic activity detection (no explicit config needed).
    """
    voice_name = map_openai_voice_to_gemini(session_payload.get("voice"))
    modalities_raw = session_payload.get("modalities") or ["audio"]
    response_modalities = [m.upper() for m in modalities_raw if m.lower() == "audio"] or ["AUDIO"]
    tool_supports_non_blocking = _gemini_model_supports_non_blocking_tools(model)

    speech_config = types_module.SpeechConfig(
        voice_config=types_module.VoiceConfig(
            prebuilt_voice_config=types_module.PrebuiltVoiceConfig(
                voice_name=voice_name,
            ),
        ),
    )

    return types_module.LiveConnectConfig(
        response_modalities=response_modalities,
        system_instruction=_append_remember_tool_instructions(
            session_payload.get("instructions") or ""
        ),
        speech_config=speech_config,
        input_audio_transcription=types_module.AudioTranscriptionConfig(),
        output_audio_transcription=types_module.AudioTranscriptionConfig(),
        tools=[
            _build_remember_tool(types_module, non_blocking=tool_supports_non_blocking),
            _build_remember_face_tool(types_module, non_blocking=tool_supports_non_blocking),
            _build_forget_face_tool(types_module, non_blocking=tool_supports_non_blocking),
        ],
    )


# ---------------------------------------------------------------------------
# Concrete Gemini backend
# ---------------------------------------------------------------------------


class GeminiRealtimeBackend:
    """Concrete :class:`kids_teacher_backend.RealtimeBackend` for Gemini Live.

    ``google.genai`` is imported lazily inside :meth:`connect` so the
    module can be imported without the SDK installed. ``client_factory``
    and ``types_module`` may be injected to override the default SDK
    construction in tests.
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        client_factory: Optional[Callable[[], Any]] = None,
        types_module: Optional[Any] = None,
        memory_file_path: Optional[str] = None,
        memory_append: Optional[Callable[[str, Optional[str]], None]] = None,
        face_frame_provider: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._model = model or resolve_gemini_model()
        self._client_factory = client_factory
        self._types_module = types_module
        self._memory_file_path = memory_file_path
        self._memory_append = memory_append or append_memory_file
        self._face_frame_provider = face_frame_provider
        self._client: Any = None
        self._connection_cm: Any = None
        self._session: Any = None
        self._closed = False
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._background_tasks: set[asyncio.Task] = set()
        # Turn-level state for VAD-style input.speech_started/stopped
        # synthesis. Gemini Live does not publish explicit start/stop
        # events; we emit them around input_transcription arrivals so the
        # realtime handler's barge-in and status logic keep working.
        self._input_speech_active = False
        # Trace-logging state: distinguishes the first post-connect send
        # failure (likely keepalive timeout) from the subsequent spam.
        self._send_failure_count = 0

    @property
    def model(self) -> str:
        return self._model

    async def connect(self, session_payload: dict) -> None:
        """Open the Gemini Live session and start pumping events."""
        client_factory = self._client_factory
        types_module = self._types_module

        # Check the API key BEFORE importing google.genai so a missing
        # key fails fast even on a test/CI image where the SDK is absent.
        if client_factory is None:
            api_key = os.environ.get(GEMINI_API_KEY_ENV_VAR)
            if not api_key:
                raise BackendConfigError(
                    f"{GEMINI_API_KEY_ENV_VAR} is not set. Export your "
                    "Gemini API key before starting a kids-teacher "
                    "session with provider=gemini."
                )
            # Lazy import: never touch the SDK at module load time.
            from google import genai  # type: ignore
            from google.genai import types as genai_types  # type: ignore

            if types_module is None:
                types_module = genai_types
            client_factory = lambda: genai.Client(api_key=api_key)  # noqa: E731
        elif types_module is None:
            # Factory was injected but types module wasn't — import types
            # alone (still lazy, still avoided at module load).
            from google.genai import types as genai_types  # type: ignore

            types_module = genai_types

        self._client = client_factory()
        self._types_module = types_module

        live_config = build_gemini_live_config(
            session_payload,
            types_module,
            model=self._model,
        )
        self._connection_cm = self._client.aio.live.connect(
            model=self._model, config=live_config
        )
        try:
            self._session = await self._connection_cm.__aenter__()
        except Exception as exc:  # pragma: no cover - integration path
            logger.warning("[kids_teacher_gemini_backend] connect failed: %s", exc)
            raise

        self._reader_task = asyncio.create_task(self._reader_loop())

    async def _reader_loop(self) -> None:
        """Translate raw Gemini Live messages into the normalized event shape.

        ``session.receive()`` yields one turn's messages up to and
        including ``turn_complete``, then the iterator exits — the
        WebSocket stays alive between turns but the iterator is consumed.
        We wrap it in an outer loop so the session spans multiple turns
        on the same connection. See
        ``python-genai/google/genai/live.py::AsyncSession.receive``:
        it explicitly ``break``s the inner yield loop on ``turn_complete``.
        Without this outer loop, turn 2 audio is streamed into a socket
        nobody is reading, and the session dies on the keepalive ping
        ~30s later.
        """
        assert self._session is not None
        turn_index = 0
        try:
            while not self._closed:
                async for raw_message in self._session.receive():
                    await self._handle_tool_call_message(raw_message)
                    for normalized in self._normalize_message(raw_message):
                        await self._event_queue.put(normalized)
                # receive() exited — turn ended. Loop back to receive()
                # for the next turn on the SAME session / WebSocket.
                turn_index += 1
                logger.info(
                    "[kids_teacher_gemini_backend] turn %d ended; awaiting next turn on same session",
                    turn_index,
                )
                # Cooperative yield — guards against a misbehaving fake
                # or server that returns an immediately-empty receive()
                # from pegging the event loop.
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            logger.info("[kids_teacher_gemini_backend] reader loop cancelled")
            raise
        except Exception as exc:  # pragma: no cover - integration path
            logger.warning(
                "[kids_teacher_gemini_backend] reader loop error (session likely dead): %s",
                exc,
            )
            await self._event_queue.put({"type": "error", "message": str(exc)})
        else:
            logger.info(
                "[kids_teacher_gemini_backend] reader loop ended cleanly (close() called)"
            )

    async def _handle_tool_call_message(self, raw_message: Any) -> None:
        tool_call = _attr(raw_message, "tool_call")
        if tool_call is None or self._session is None or self._types_module is None:
            return

        function_calls = _attr(tool_call, "function_calls") or []
        if not function_calls:
            logger.warning("[kids_teacher_gemini_backend] tool_call without function_calls")
            return

        non_blocking = _gemini_model_supports_non_blocking_tools(self._model)
        function_responses: list[Any] = []
        facts_to_persist: list[str] = []

        for function_call in function_calls:
            name = _attr(function_call, "name") or ""
            call_id = _attr(function_call, "id")

            if name == _REMEMBER_TOOL_NAME:
                response, fact = self._handle_remember_call(function_call)
                if fact is not None:
                    facts_to_persist.append(fact)
            elif name == _REMEMBER_FACE_TOOL_NAME:
                response, fact = await self._handle_remember_face_call(function_call)
                if fact is not None:
                    facts_to_persist.append(fact)
            elif name == _FORGET_FACE_TOOL_NAME:
                response = await self._handle_forget_face_call(function_call)
            else:
                logger.warning(
                    "[kids_teacher_gemini_backend] unknown tool call name=%r ignored",
                    name,
                )
                response = {"output": {"status": "ignored"}}

            function_responses.append(
                _build_function_response(
                    self._types_module,
                    call_id=call_id,
                    name=name or _REMEMBER_TOOL_NAME,
                    response=response,
                    non_blocking=non_blocking,
                )
            )

        try:
            await self._session.send_tool_response(
                function_responses=function_responses
            )
        except Exception as exc:
            logger.warning(
                "[kids_teacher_gemini_backend] send_tool_response failed: %s", exc
            )
        finally:
            for fact in facts_to_persist:
                self._schedule_memory_append(fact)

    def _handle_remember_call(
        self, function_call: Any
    ) -> tuple[dict[str, Any], Optional[str]]:
        fact = _extract_remember_fact(function_call)
        if not fact:
            logger.warning(
                "[kids_teacher_gemini_backend] remember tool call missing usable fact"
            )
            return {"output": {"status": "ignored"}}, None
        return {"output": {"status": "scheduled"}}, fact

    async def _handle_remember_face_call(
        self, function_call: Any
    ) -> tuple[dict[str, Any], Optional[str]]:
        name, relationship = _extract_remember_face_args(function_call)
        if not name:
            logger.warning(
                "[kids_teacher_gemini_backend] remember_face missing usable name"
            )
            return {"output": {"status": "ignored"}}, None

        frame = self._face_frame_provider() if self._face_frame_provider else None
        if frame is None:
            logger.info(
                "[kids_teacher_gemini_backend] remember_face: no frame available"
            )
            return {
                "output": {
                    "status": "no_face",
                    "message": "I can't see them clearly — can they look at me?",
                }
            }, None

        try:
            result = await asyncio.to_thread(
                face_service.enroll_from_frame, name, frame, relationship
            )
        except Exception:
            logger.warning(
                "[kids_teacher_gemini_backend] remember_face enroll failed for name=%r",
                name,
                exc_info=True,
            )
            return {"output": {"status": "unavailable"}}, None

        EnrollResult = face_service.EnrollResult  # noqa: N806
        fact_to_persist: Optional[str] = None
        if result == EnrollResult.OK:
            logger.info(
                "[kids_teacher_gemini_backend] remember_face ok name=%r relationship=%r",
                name,
                relationship,
            )
            if relationship:
                fact_to_persist = f"{name} {relationship}"
            return {"output": {"status": "ok"}}, fact_to_persist
        if result == EnrollResult.NO_FACE:
            return {"output": {"status": "no_face"}}, None
        if result == EnrollResult.MULTIPLE_FACES:
            return {"output": {"status": "multiple_faces"}}, None
        if result == EnrollResult.CAPACITY_EXCEEDED:
            return {"output": {"status": "capacity"}}, None
        if result == EnrollResult.LIBRARY_MISSING:
            return {"output": {"status": "unavailable"}}, None
        # Defensive fallback for any unmapped enum.
        return {"output": {"status": "unavailable"}}, None

    async def _handle_forget_face_call(
        self, function_call: Any
    ) -> dict[str, Any]:
        name = _extract_forget_face_name(function_call)
        if not name:
            logger.warning(
                "[kids_teacher_gemini_backend] forget_face missing usable name"
            )
            return {"output": {"status": "ignored"}}
        try:
            removed_face = await asyncio.to_thread(face_service.forget, name)
        except Exception:
            # Symmetric with the memory branch below: log + continue so
            # memory.md still gets cleaned even when faces.pkl is corrupt
            # or unreadable. Returning ``not_found`` here would skip the
            # memory cleanup AND mislead the parent ("I don't think I
            # remembered her") when in fact cleanup failed mid-flight.
            logger.warning(
                "[kids_teacher_gemini_backend] forget_face faces.pkl failed for name=%r",
                name,
                exc_info=True,
            )
            removed_face = False
        try:
            removed_lines = await asyncio.to_thread(
                memory_remove_substring, name, self._memory_file_path
            )
        except Exception:
            logger.warning(
                "[kids_teacher_gemini_backend] forget_face memory cleanup failed for name=%r",
                name,
                exc_info=True,
            )
            removed_lines = 0
        if removed_face or removed_lines:
            logger.info(
                "[kids_teacher_gemini_backend] forget_face ok name=%r faces_removed=%s memory_lines=%d",
                name,
                removed_face,
                removed_lines,
            )
            return {"output": {"status": "ok"}}
        return {"output": {"status": "not_found"}}

    def _schedule_memory_append(self, fact: str) -> None:
        task = asyncio.create_task(self._append_memory_fact_async(fact))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _append_memory_fact_async(self, fact: str) -> None:
        try:
            await asyncio.to_thread(
                self._memory_append,
                fact,
                self._memory_file_path,
            )
        except Exception:
            logger.warning(
                "[kids_teacher_gemini_backend] remember write failed for fact=%r",
                fact,
                exc_info=True,
            )
            return
        logger.info(
            "[kids_teacher_gemini_backend] remember write succeeded for fact=%r",
            fact,
        )

    def _normalize_message(self, raw_message: Any) -> list[dict]:
        """Map one raw ``LiveServerMessage`` to zero-or-more normalized events.

        Gemini collapses several OpenAI event types into a single message
        (one message can carry an audio chunk, an input transcript
        delta, and a turn-complete flag at once), so this returns a list.
        """
        events: list[dict] = []
        server_content = _attr(raw_message, "server_content")
        if server_content is None:
            tool_call = _attr(raw_message, "tool_call")
            if tool_call is not None:
                function_calls = _attr(tool_call, "function_calls") or []
                logger.info(
                    "[kids_teacher_gemini_backend] tool_call received (%d function call(s))",
                    len(function_calls),
                )
                return events
            tool_call_cancellation = _attr(raw_message, "tool_call_cancellation")
            if tool_call_cancellation is not None:
                ids = _attr(tool_call_cancellation, "ids")
                logger.info(
                    "[kids_teacher_gemini_backend] tool_call_cancellation received ids=%r",
                    ids,
                )
                return events
            # Log non-server_content messages with per-type detail so we
            # can see the actual contents of session_resumption_update
            # (new_handle, resumable) and go_away (time_left) — these
            # are what drive the multi-turn reconnect protocol.
            srupdate = _attr(raw_message, "session_resumption_update")
            if srupdate is not None:
                new_handle = _attr(srupdate, "new_handle")
                if isinstance(new_handle, str) and len(new_handle) > 40:
                    handle_preview: Any = f"{new_handle[:40]}...(len={len(new_handle)})"
                else:
                    handle_preview = new_handle
                logger.info(
                    "[kids_teacher_gemini_backend] session_resumption_update: "
                    "new_handle=%r resumable=%s last_consumed_client_message_index=%s",
                    handle_preview,
                    _attr(srupdate, "resumable"),
                    _attr(srupdate, "last_consumed_client_message_index"),
                )
                return events
            goaway = _attr(raw_message, "go_away")
            if goaway is not None:
                logger.info(
                    "[kids_teacher_gemini_backend] go_away: time_left=%s",
                    _attr(goaway, "time_left"),
                )
                return events
            present = _summarize_top_level_fields(raw_message)
            logger.info(
                "[kids_teacher_gemini_backend] non-server_content message: fields=%s",
                present,
            )
            return events

        # Input transcription (child speech). We synthesize
        # ``input.speech_started`` on the first delta of a turn and
        # ``input.speech_stopped`` when the transcription is marked
        # finished — the realtime handler uses these to drive barge-in
        # and status transitions.
        input_tx = _attr(server_content, "input_transcription")
        if input_tx is not None:
            text = _attr(input_tx, "text") or ""
            finished = bool(_attr(input_tx, "finished"))
            if text and not self._input_speech_active:
                self._input_speech_active = True
                logger.info(
                    "[kids_teacher_gemini_backend] input_transcription: first delta of turn text=%r",
                    text[:30],
                )
                events.append({"type": "input.speech_started"})
            if finished:
                logger.info(
                    "[kids_teacher_gemini_backend] input_transcription: finished text=%r",
                    text[:60],
                )
                events.append(
                    {
                        "type": "input_transcript.final",
                        "text": text,
                        "language": _attr(input_tx, "language_code"),
                    }
                )
                if self._input_speech_active:
                    events.append({"type": "input.speech_stopped"})
                    self._input_speech_active = False
            elif text:
                events.append(
                    {
                        "type": "input_transcript.delta",
                        "text": text,
                        "language": _attr(input_tx, "language_code"),
                    }
                )

        # Output transcription (assistant speech).
        output_tx = _attr(server_content, "output_transcription")
        if output_tx is not None:
            text = _attr(output_tx, "text") or ""
            finished = bool(_attr(output_tx, "finished"))
            if finished:
                logger.info(
                    "[kids_teacher_gemini_backend] output_transcription: finished text=%r",
                    text[:60],
                )
                events.append(
                    {
                        "type": "assistant_transcript.final",
                        "text": text,
                        "language": _attr(output_tx, "language_code"),
                    }
                )
            elif text:
                events.append(
                    {
                        "type": "assistant_transcript.delta",
                        "text": text,
                    }
                )

        # Assistant audio chunks arrive inside ``model_turn.parts[*].inline_data.data``.
        model_turn = _attr(server_content, "model_turn")
        if model_turn is not None:
            parts = _attr(model_turn, "parts") or []
            for part in parts:
                inline = _attr(part, "inline_data")
                if inline is None:
                    continue
                data = _attr(inline, "data")
                if not data:
                    continue
                # The SDK surfaces ``data`` as raw bytes (PCM16 LE 24 kHz
                # mono). Handle the defensive case where some versions
                # return base64 strings.
                if isinstance(data, str):
                    try:
                        import base64

                        data = base64.b64decode(data)
                    except Exception:
                        logger.warning(
                            "[kids_teacher_gemini_backend] could not decode inline audio"
                        )
                        continue
                events.append({"type": "audio.chunk", "audio": data})

        # Generation complete is a distinct signal from turn_complete:
        # the model has finished producing this response but the turn is
        # still open for more user audio. Surface it for diagnosis.
        if _attr(server_content, "generation_complete"):
            logger.info("[kids_teacher_gemini_backend] generation_complete received")

        # Gemini's earliest barge-in signal. Must be emitted BEFORE any
        # turn_complete on the same message so the handler can cancel the
        # active response while _assistant_active is still True — otherwise
        # response.done clears the gate first and the flush never runs.
        if _attr(server_content, "interrupted"):
            logger.info(
                "[kids_teacher_gemini_backend] server reported interrupted=True"
            )
            events.append({"type": "input.speech_started"})

        # Turn complete → response.done.
        if _attr(server_content, "turn_complete"):
            logger.info("[kids_teacher_gemini_backend] turn_complete received")
            # Flush any dangling speech_started state on end-of-turn.
            if self._input_speech_active:
                events.append({"type": "input.speech_stopped"})
                self._input_speech_active = False
            events.append({"type": "response.done"})

        return events

    async def send_audio(self, chunk: bytes) -> None:
        if self._session is None or not chunk:
            return
        assert self._types_module is not None
        try:
            await self._session.send_realtime_input(
                audio=self._types_module.Blob(data=chunk, mime_type=GEMINI_INPUT_MIME)
            )
        except Exception as exc:  # pragma: no cover - integration path
            self._send_failure_count += 1
            if self._send_failure_count == 1:
                logger.warning(
                    "[kids_teacher_gemini_backend] Gemini Live session dropped "
                    "(first send failure — likely keepalive timeout or server disconnect): %s",
                    exc,
                )
            else:
                logger.warning(
                    "[kids_teacher_gemini_backend] send_audio failed (#%d, session still dead): %s",
                    self._send_failure_count,
                    exc,
                )
            await self._event_queue.put({"type": "error", "message": str(exc)})

    async def send_video(self, jpeg_bytes: bytes) -> None:
        """Forward one JPEG frame to Gemini Live's video channel.

        Mirrors :meth:`send_audio`: silently no-ops if the session is not
        yet open (or already torn down). Send failures are debug-logged
        and swallowed — never fatal — per design §1 "Error handling".
        Frames are NEVER persisted (FR-KID-8 / §2.4).
        """
        if self._session is None or not jpeg_bytes:
            return
        assert self._types_module is not None
        try:
            await self._session.send_realtime_input(
                video=self._types_module.Blob(
                    data=jpeg_bytes, mime_type="image/jpeg"
                )
            )
        except Exception as exc:  # pragma: no cover - integration path
            logger.debug(
                "[kids_teacher_gemini_backend] send_video failed: %s", exc
            )

    async def send_text(self, text: str) -> None:
        """Send a user text turn.

        Gemini's SDK exposes text turns through ``send_client_content`` with
        a ``types.Content`` wrapper. The realtime handler only uses this
        for optional fallback flows; the primary path is audio-in/audio-out.
        """
        if self._session is None or not text:
            return
        assert self._types_module is not None
        try:
            content = self._types_module.Content(
                role="user",
                parts=[self._types_module.Part(text=text)],
            )
            await self._session.send_client_content(turns=content, turn_complete=True)
        except Exception as exc:  # pragma: no cover - integration path
            logger.warning("[kids_teacher_gemini_backend] send_text failed: %s", exc)
            await self._event_queue.put({"type": "error", "message": str(exc)})

    async def cancel_response(self) -> None:
        """Cancel the in-flight assistant response.

        Gemini Live has no explicit ``response.cancel`` method. The closest
        equivalent is signalling end-of-audio so server VAD interprets the
        current user turn as finished and interrupts the model. The
        realtime handler already clears its local playback queue on
        barge-in, so the caller still sees a prompt stop even if Gemini
        keeps streaming for a beat.
        """
        if self._session is None:
            return
        logger.info("[kids_teacher_gemini_backend] cancel_response invoked")
        try:
            # ``send_realtime_input`` accepts audio_stream_end=True as the
            # flush signal. Fall back gracefully if the SDK shape differs.
            send = self._session.send_realtime_input
            try:
                await send(audio_stream_end=True)
                logger.info(
                    "[kids_teacher_gemini_backend] sent audio_stream_end=True to Gemini Live"
                )
            except TypeError:
                # Older SDK variants do not accept the kwarg; best-effort no-op.
                logger.debug(
                    "[kids_teacher_gemini_backend] cancel_response: audio_stream_end unsupported"
                )
        except Exception as exc:  # pragma: no cover - integration path
            logger.warning("[kids_teacher_gemini_backend] cancel_response failed: %s", exc)

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
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)
        if self._connection_cm is not None:
            try:
                await self._connection_cm.__aexit__(None, None, None)
            except Exception as exc:  # pragma: no cover - integration path
                logger.warning("[kids_teacher_gemini_backend] close failed: %s", exc)

    async def events(self) -> AsyncIterator[dict]:
        while True:
            event = await self._event_queue.get()
            yield event
            if event.get("type") == "response.done" and self._closed:
                return


_TOP_LEVEL_LIVE_MESSAGE_FIELDS: tuple[str, ...] = (
    "setup_complete",
    "server_content",
    "tool_call",
    "tool_call_cancellation",
    "go_away",
    "session_resumption_update",
    "usage_metadata",
)


def _summarize_top_level_fields(raw_message: Any) -> str:
    """Return a short string listing which known top-level fields are set.

    Used only for diagnostic logging of non-server_content Gemini Live
    messages — do not read values, just field presence.
    """
    present = [
        name
        for name in _TOP_LEVEL_LIVE_MESSAGE_FIELDS
        if _attr(raw_message, name) is not None
    ]
    return ",".join(present) if present else "(no known fields)"


def _attr(obj: Any, name: str) -> Any:
    """Read ``name`` from ``obj`` tolerantly (dict-like or attribute-like).

    Gemini SDK versions differ on whether server_content fields are
    pydantic attributes or plain dicts depending on codec path. Accept
    either so the normalizer stays resilient.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
