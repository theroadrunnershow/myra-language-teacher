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

from env_loader import load_project_dotenv
from kids_teacher_backend import BackendConfigError
from kids_teacher_types import ALLOWED_GEMINI_MODELS
from memory_file import ALLOWED_KEYS as _MEMORY_ALLOWED_KEYS
from memory_file import set_key as set_memory_key
from memory_reconciler import add_note as reconcile_add_note

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

_SET_ABOUT_TOOL_NAME = "set_about"
_ADD_NOTE_TOOL_NAME = "add_note"
_MEMORY_TOOL_NAMES = (_SET_ABOUT_TOOL_NAME, _ADD_NOTE_TOOL_NAME)

_MEMORY_TOOL_PROMPT_APPENDIX = f"""
# Memory tools
You have two tools for remembering things about the child. Use them only when
the child or parent explicitly asks you to remember something.

- `set_about(key, value)` — for a single-valued fact about the child.
  Allowed keys: {", ".join(_MEMORY_ALLOWED_KEYS)}.
  Setting a key replaces any previous value for it.
  Example: child says "my name is Aanya" → call `set_about(key="name", value="Aanya")`.

- `add_note(text)` — for a free-form observation that doesn't fit a key.
  Use one short, third-person sentence.
  Example: parent says "she loves dinosaurs" → call `add_note(text="She loves dinosaurs")`.

After calling either tool, briefly say "Got it, I'll remember!"

Never store an address, phone number, school name, password, login info, or
medical ID in memory.
""".strip()


def _gemini_model_supports_non_blocking_tools(model: str) -> bool:
    """Return whether this Gemini Live model supports async function calling."""
    return model.strip() != "gemini-3.1-flash-live-preview"


def _build_memory_tool(types_module: Any, *, non_blocking: bool) -> Any:
    set_about_kwargs: dict[str, Any] = {
        "name": _SET_ABOUT_TOOL_NAME,
        "description": (
            "Replace a single-valued fact about the child (name, age, parent "
            "names, favourites). Setting a key supersedes any previous value."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": list(_MEMORY_ALLOWED_KEYS),
                    "description": "Which fact to set.",
                },
                "value": {
                    "type": "string",
                    "description": (
                        "The new value, e.g. 'Aanya' for name or 'blue' for "
                        "favourite_colour."
                    ),
                },
            },
            "required": ["key", "value"],
            "additionalProperties": False,
        },
    }
    add_note_kwargs: dict[str, Any] = {
        "name": _ADD_NOTE_TOOL_NAME,
        "description": (
            "Append a free-form observation about the child that doesn't fit "
            "a fixed key. One short, third-person sentence."
        ),
        "parameters_json_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": (
                        "One short third-person sentence, e.g. 'She loves "
                        "dinosaurs.'"
                    ),
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    }
    if non_blocking:
        set_about_kwargs["behavior"] = "NON_BLOCKING"
        add_note_kwargs["behavior"] = "NON_BLOCKING"
    return types_module.Tool(
        function_declarations=[
            types_module.FunctionDeclaration(**set_about_kwargs),
            types_module.FunctionDeclaration(**add_note_kwargs),
        ]
    )


def _append_memory_tool_instructions(instructions: str) -> str:
    base = (instructions or "").strip()
    if not base:
        return _MEMORY_TOOL_PROMPT_APPENDIX
    return f"{base}\n\n{_MEMORY_TOOL_PROMPT_APPENDIX}"


def _extract_args(function_call: Any) -> dict[str, Any]:
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
        system_instruction=_append_memory_tool_instructions(
            session_payload.get("instructions") or ""
        ),
        speech_config=speech_config,
        input_audio_transcription=types_module.AudioTranscriptionConfig(),
        output_audio_transcription=types_module.AudioTranscriptionConfig(),
        tools=[_build_memory_tool(types_module, non_blocking=tool_supports_non_blocking)],
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
        set_key_fn: Optional[Callable[[str, str, Optional[str]], None]] = None,
        add_note_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._model = model or resolve_gemini_model()
        self._client_factory = client_factory
        self._types_module = types_module
        self._memory_file_path = memory_file_path
        self._set_key = set_key_fn or set_memory_key
        self._add_note = add_note_fn or reconcile_add_note
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
        scheduled_writes: list[tuple[str, dict[str, Any]]] = []

        for function_call in function_calls:
            name = _attr(function_call, "name") or ""
            call_id = _attr(function_call, "id")
            args = _extract_args(function_call)

            if name == _SET_ABOUT_TOOL_NAME:
                key = (args.get("key") or "").strip()
                value = " ".join((args.get("value") or "").strip().split())
                if key not in _MEMORY_ALLOWED_KEYS or not value:
                    logger.warning(
                        "[kids_teacher_gemini_backend] set_about call rejected: key=%r value=%r",
                        key,
                        value,
                    )
                    function_responses.append(
                        _build_function_response(
                            self._types_module,
                            call_id=call_id,
                            name=name,
                            response={"output": {"status": "ignored"}},
                            non_blocking=non_blocking,
                        )
                    )
                    continue
                function_responses.append(
                    _build_function_response(
                        self._types_module,
                        call_id=call_id,
                        name=name,
                        response={"output": {"status": "scheduled"}},
                        non_blocking=non_blocking,
                    )
                )
                scheduled_writes.append((name, {"key": key, "value": value}))
                continue

            if name == _ADD_NOTE_TOOL_NAME:
                text = " ".join((args.get("text") or "").strip().split())
                if not text:
                    logger.warning(
                        "[kids_teacher_gemini_backend] add_note call rejected: empty text"
                    )
                    function_responses.append(
                        _build_function_response(
                            self._types_module,
                            call_id=call_id,
                            name=name,
                            response={"output": {"status": "ignored"}},
                            non_blocking=non_blocking,
                        )
                    )
                    continue
                function_responses.append(
                    _build_function_response(
                        self._types_module,
                        call_id=call_id,
                        name=name,
                        response={"output": {"status": "scheduled"}},
                        non_blocking=non_blocking,
                    )
                )
                scheduled_writes.append((name, {"text": text}))
                continue

            logger.warning(
                "[kids_teacher_gemini_backend] unknown tool call name=%r ignored",
                name,
            )
            function_responses.append(
                _build_function_response(
                    self._types_module,
                    call_id=call_id,
                    name=name or "unknown",
                    response={"output": {"status": "ignored"}},
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
            for tool_name, payload in scheduled_writes:
                self._schedule_memory_write(tool_name, payload)

    def _schedule_memory_write(self, tool_name: str, payload: dict[str, Any]) -> None:
        task = asyncio.create_task(self._memory_write_async(tool_name, payload))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _memory_write_async(
        self, tool_name: str, payload: dict[str, Any]
    ) -> None:
        try:
            if tool_name == _SET_ABOUT_TOOL_NAME:
                await asyncio.to_thread(
                    self._set_key,
                    payload["key"],
                    payload["value"],
                    self._memory_file_path,
                )
            elif tool_name == _ADD_NOTE_TOOL_NAME:
                await asyncio.to_thread(
                    self._add_note,
                    payload["text"],
                    path=self._memory_file_path,
                )
            else:
                return
        except Exception:
            logger.warning(
                "[kids_teacher_gemini_backend] %s write failed for payload=%r",
                tool_name,
                payload,
                exc_info=True,
            )
            return
        logger.info(
            "[kids_teacher_gemini_backend] %s write succeeded for payload=%r",
            tool_name,
            payload,
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
