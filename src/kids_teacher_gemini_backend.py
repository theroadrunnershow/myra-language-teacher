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
import logging
import os
from typing import Any, AsyncIterator, Callable, Optional

from env_loader import load_project_dotenv
from kids_teacher_backend import BackendConfigError
from kids_teacher_types import ALLOWED_GEMINI_MODELS

load_project_dotenv()

logger = logging.getLogger(__name__)


DEFAULT_GEMINI_MODEL = "gemini-live-2.5-flash-native-audio"
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


def build_gemini_live_config(session_payload: dict, types_module: Any) -> Any:
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

    speech_config = types_module.SpeechConfig(
        voice_config=types_module.VoiceConfig(
            prebuilt_voice_config=types_module.PrebuiltVoiceConfig(
                voice_name=voice_name,
            ),
        ),
    )

    return types_module.LiveConnectConfig(
        response_modalities=response_modalities,
        system_instruction=session_payload.get("instructions") or "",
        speech_config=speech_config,
        input_audio_transcription=types_module.AudioTranscriptionConfig(),
        output_audio_transcription=types_module.AudioTranscriptionConfig(),
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
    ) -> None:
        self._model = model or resolve_gemini_model()
        self._client_factory = client_factory
        self._types_module = types_module
        self._client: Any = None
        self._connection_cm: Any = None
        self._session: Any = None
        self._closed = False
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        # Turn-level state for VAD-style input.speech_started/stopped
        # synthesis. Gemini Live does not publish explicit start/stop
        # events; we emit them around input_transcription arrivals so the
        # realtime handler's barge-in and status logic keep working.
        self._input_speech_active = False

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

        live_config = build_gemini_live_config(session_payload, types_module)
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
        """Translate raw Gemini Live messages into the normalized event shape."""
        assert self._session is not None
        try:
            async for raw_message in self._session.receive():
                for normalized in self._normalize_message(raw_message):
                    await self._event_queue.put(normalized)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - integration path
            logger.warning("[kids_teacher_gemini_backend] reader loop error: %s", exc)
            await self._event_queue.put({"type": "error", "message": str(exc)})

    def _normalize_message(self, raw_message: Any) -> list[dict]:
        """Map one raw ``LiveServerMessage`` to zero-or-more normalized events.

        Gemini collapses several OpenAI event types into a single message
        (one message can carry an audio chunk, an input transcript
        delta, and a turn-complete flag at once), so this returns a list.
        """
        events: list[dict] = []
        server_content = _attr(raw_message, "server_content")
        if server_content is None:
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
                events.append({"type": "input.speech_started"})
            if finished:
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

        # Turn complete → response.done.
        if _attr(server_content, "turn_complete"):
            # Flush any dangling speech_started state on end-of-turn.
            if self._input_speech_active:
                events.append({"type": "input.speech_stopped"})
                self._input_speech_active = False
            events.append({"type": "response.done"})

        # Gemini reports model-interrupted turns via server_content.interrupted.
        # We don't emit a dedicated event; the handler already manages barge-in
        # via input.speech_started. Logged for diagnostic parity only.
        if _attr(server_content, "interrupted"):
            logger.debug("[kids_teacher_gemini_backend] server reported interrupt")

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
            logger.warning("[kids_teacher_gemini_backend] send_audio failed: %s", exc)
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
        try:
            # ``send_realtime_input`` accepts audio_stream_end=True as the
            # flush signal. Fall back gracefully if the SDK shape differs.
            send = self._session.send_realtime_input
            try:
                await send(audio_stream_end=True)
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
