"""Tests for src/kids_teacher_gemini_backend.py.

These tests must NOT require the real google-genai SDK or a real API key.
The concrete backend lazy-imports google.genai inside connect(), so
instantiating it (or calling resolve_gemini_model / map_openai_voice_to_gemini
/ build_gemini_live_config) must work on a lightweight image where
google-genai is not installed.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest

import memory_file
from kids_teacher_backend import BackendConfigError
from kids_teacher_gemini_backend import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_API_KEY_ENV_VAR,
    GEMINI_INPUT_MIME,
    GEMINI_MODEL_ENV_VAR,
    GeminiRealtimeBackend,
    build_gemini_live_config,
    map_openai_voice_to_gemini,
    resolve_gemini_model,
)
from kids_teacher_types import ALLOWED_GEMINI_MODELS


# ---------------------------------------------------------------------------
# Fake google.genai.types stand-ins
#
# We only need these to round-trip through build_gemini_live_config and
# into GeminiRealtimeBackend without installing the real SDK. They record
# constructor kwargs so we can assert on them.
# ---------------------------------------------------------------------------


class _Record:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeTypes:
    Blob = _Record
    Content = _Record
    Part = _Record
    FunctionDeclaration = _Record
    FunctionResponse = _Record
    PrebuiltVoiceConfig = _Record
    VoiceConfig = _Record
    SpeechConfig = _Record
    Tool = _Record
    AudioTranscriptionConfig = _Record
    LiveConnectConfig = _Record


# ---------------------------------------------------------------------------
# resolve_gemini_model
# ---------------------------------------------------------------------------


def test_resolve_gemini_model_defaults_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(GEMINI_MODEL_ENV_VAR, raising=False)
    assert resolve_gemini_model() == DEFAULT_GEMINI_MODEL
    assert DEFAULT_GEMINI_MODEL in ALLOWED_GEMINI_MODELS


def test_resolve_gemini_model_defaults_when_env_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEMINI_MODEL_ENV_VAR, "   ")
    assert resolve_gemini_model() == DEFAULT_GEMINI_MODEL


def test_resolve_gemini_model_honors_explicit_value() -> None:
    assert (
        resolve_gemini_model(env_value="gemini-live-2.5-flash-native-audio")
        == "gemini-live-2.5-flash-native-audio"
    )


def test_resolve_gemini_model_rejects_preview_variants() -> None:
    with pytest.raises(BackendConfigError):
        resolve_gemini_model(
            env_value="gemini-2.5-flash-preview-native-audio-09-2025"
        )


def test_resolve_gemini_model_rejects_flash_lite() -> None:
    # Flash-Lite is not supported on Live API; reject it loudly.
    with pytest.raises(BackendConfigError):
        resolve_gemini_model(env_value="gemini-2.5-flash-lite")


# ---------------------------------------------------------------------------
# map_openai_voice_to_gemini
# ---------------------------------------------------------------------------


def test_map_openai_voice_alloy_to_kore() -> None:
    assert map_openai_voice_to_gemini("alloy") == "Kore"


def test_map_openai_voice_case_insensitive() -> None:
    assert map_openai_voice_to_gemini("ALLOY") == "Kore"


def test_map_openai_voice_unknown_falls_back_to_default() -> None:
    # Any unmapped name → Kore (the default child-friendly voice).
    assert map_openai_voice_to_gemini("some-new-openai-voice") == "Kore"


def test_map_openai_voice_empty_or_none_uses_default() -> None:
    assert map_openai_voice_to_gemini(None) == "Kore"
    assert map_openai_voice_to_gemini("") == "Kore"


# ---------------------------------------------------------------------------
# build_gemini_live_config
# ---------------------------------------------------------------------------


def test_build_live_config_maps_instructions_voice_and_transcription() -> None:
    payload = {
        "instructions": "Only safe preschool topics.",
        "voice": "alloy",
        "modalities": ["audio", "text"],
        "input_audio_transcription": {"model": "gpt-4o-mini-transcribe"},
        "turn_detection": {"type": "server_vad"},
        "tools": [],
    }

    config = build_gemini_live_config(payload, _FakeTypes)

    assert "Only safe preschool topics." in config.system_instruction
    assert "remember" in config.system_instruction
    # Only audio modality survives — text-only replies would break the robot.
    assert config.response_modalities == ["AUDIO"]
    # Voice translated to Gemini name.
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    # Transcription is opt-in on Gemini; the backend MUST enable both
    # directions so the safety layer keeps working.
    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None
    # Chunk G: ``remember_face`` and ``forget_face`` were added alongside
    # ``remember``. The first tool is still the persistent-memory remember
    # tool; the face tools are asserted separately below.
    assert len(config.tools) == 3
    declaration = config.tools[0].function_declarations[0]
    assert declaration.name == "remember"
    assert declaration.behavior == "NON_BLOCKING"


def test_build_live_config_defaults_to_audio_when_modalities_missing() -> None:
    payload = {"instructions": "hi", "voice": "alloy"}
    config = build_gemini_live_config(payload, _FakeTypes)
    assert config.response_modalities == ["AUDIO"]


def test_build_live_config_empty_instructions_still_adds_memory_tool_prompt() -> None:
    payload = {"voice": "alloy"}
    config = build_gemini_live_config(payload, _FakeTypes)
    assert "remember" in config.system_instruction


def test_build_live_config_omits_non_blocking_behavior_on_gemini_3_1() -> None:
    payload = {"instructions": "hi", "voice": "alloy"}
    config = build_gemini_live_config(
        payload,
        _FakeTypes,
        model="gemini-3.1-flash-live-preview",
    )
    declaration = config.tools[0].function_declarations[0]
    assert getattr(declaration, "behavior", None) is None


# ---------------------------------------------------------------------------
# Lazy-import guarantees
# ---------------------------------------------------------------------------


def test_gemini_backend_does_not_import_google_genai_at_module_load() -> None:
    backend = GeminiRealtimeBackend(model=DEFAULT_GEMINI_MODEL)
    assert backend.model == DEFAULT_GEMINI_MODEL

    import kids_teacher_gemini_backend as ktg

    assert not hasattr(ktg, "google"), (
        "kids_teacher_gemini_backend must not import google.genai at module level"
    )


def test_gemini_backend_module_importable_without_genai_installed() -> None:
    assert "kids_teacher_gemini_backend" in sys.modules


def test_gemini_backend_construct_does_not_call_factory() -> None:
    calls: list[str] = []

    def factory() -> object:
        calls.append("factory")
        return object()

    GeminiRealtimeBackend(client_factory=factory, types_module=_FakeTypes)
    assert calls == []


# ---------------------------------------------------------------------------
# Connect / reader loop (scripted fake)
# ---------------------------------------------------------------------------


class _FakeSession:
    """A fake google.genai.live session with scripted receive() output."""

    def __init__(self, messages: list[Any]) -> None:
        self._messages = list(messages)
        self.sent_audio: list[Any] = []
        self.sent_text: list[Any] = []
        self.cancelled_with: list[bool] = []
        self.tool_responses: list[Any] = []

    async def receive(self):
        for msg in self._messages:
            yield msg

    async def send_realtime_input(
        self,
        *,
        audio: Any | None = None,
        audio_stream_end: bool | None = None,
    ) -> None:
        if audio is not None:
            self.sent_audio.append(audio)
        if audio_stream_end is not None:
            self.cancelled_with.append(audio_stream_end)

    async def send_client_content(self, *, turns: Any, turn_complete: bool) -> None:
        self.sent_text.append((turns, turn_complete))

    async def send_tool_response(self, *, function_responses: Any) -> None:
        self.tool_responses.append(function_responses)


class _FakeConnectionCM:
    def __init__(self, session: Any) -> None:
        self._session = session
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> Any:
        self.entered = True
        return self._session

    async def __aexit__(self, *exc: Any) -> None:
        self.exited = True


class _MultiTurnFakeSession:
    """Fake session that yields one turn's messages per receive() call.

    Matches the real Gemini SDK behavior: ``session.receive()`` exits on
    every ``turn_complete``; callers must re-invoke it to get the next
    turn. After all scripted turns are exhausted, ``receive()`` blocks
    forever — simulating an idle-but-alive session that only unblocks
    when the reader task is cancelled (via ``backend.close()``).
    """

    def __init__(self, turns: list[list[Any]]) -> None:
        self._turns = list(turns)
        self._turn_index = 0
        self.sent_audio: list[Any] = []
        self.sent_text: list[Any] = []
        self.cancelled_with: list[bool] = []
        self.tool_responses: list[Any] = []
        self.receive_calls: int = 0

    async def receive(self):
        self.receive_calls += 1
        if self._turn_index >= len(self._turns):
            # Block until cancelled — real SDK blocks on the WebSocket.
            await asyncio.Future()
            return
        messages = self._turns[self._turn_index]
        self._turn_index += 1
        for msg in messages:
            yield msg

    async def send_realtime_input(
        self,
        *,
        audio: Any | None = None,
        audio_stream_end: bool | None = None,
    ) -> None:
        if audio is not None:
            self.sent_audio.append(audio)
        if audio_stream_end is not None:
            self.cancelled_with.append(audio_stream_end)

    async def send_client_content(self, *, turns: Any, turn_complete: bool) -> None:
        self.sent_text.append((turns, turn_complete))

    async def send_tool_response(self, *, function_responses: Any) -> None:
        self.tool_responses.append(function_responses)


class _FakeLive:
    def __init__(self, manager: _FakeConnectionCM) -> None:
        self._manager = manager
        self.connect_calls: list[dict] = []

    def connect(self, *, model: str, config: Any) -> _FakeConnectionCM:
        self.connect_calls.append({"model": model, "config": config})
        return self._manager


class _FakeAio:
    def __init__(self, live: _FakeLive) -> None:
        self.live = live


class _FakeClient:
    def __init__(self, manager: _FakeConnectionCM) -> None:
        self._live = _FakeLive(manager)
        self.aio = _FakeAio(self._live)


def _build_server_message(
    *,
    input_text: str | None = None,
    input_finished: bool = False,
    output_text: str | None = None,
    output_finished: bool = False,
    audio_bytes: bytes | None = None,
    turn_complete: bool = False,
) -> Any:
    server_content: dict[str, Any] = {}
    if input_text is not None:
        server_content["input_transcription"] = SimpleNamespace(
            text=input_text,
            finished=input_finished,
            language_code="en-US",
        )
    if output_text is not None:
        server_content["output_transcription"] = SimpleNamespace(
            text=output_text,
            finished=output_finished,
            language_code=None,
        )
    if audio_bytes is not None:
        part = SimpleNamespace(
            inline_data=SimpleNamespace(data=audio_bytes, mime_type="audio/pcm")
        )
        server_content["model_turn"] = SimpleNamespace(parts=[part])
    if turn_complete:
        server_content["turn_complete"] = True
    return SimpleNamespace(server_content=server_content)


def _build_tool_call_message(*, fact: Any, call_id: str = "call-1") -> Any:
    function_call = SimpleNamespace(
        id=call_id,
        name="remember",
        args={"fact": fact} if fact is not None else {},
    )
    return SimpleNamespace(tool_call=SimpleNamespace(function_calls=[function_call]))


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_connect_uses_injected_factory_and_enters_cm() -> None:
    session = _FakeSession(messages=[])
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)

    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})
    await backend.close()

    assert manager.entered is True
    assert manager.exited is True
    assert client._live.connect_calls[0]["model"] == DEFAULT_GEMINI_MODEL


@pytest.mark.asyncio
async def test_connect_raises_when_api_key_missing_and_no_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no client_factory injected, the backend tries to build a real
    # client. Without GEMINI_API_KEY that must raise BackendConfigError
    # before the lazy import of google.genai even runs.
    monkeypatch.delenv(GEMINI_API_KEY_ENV_VAR, raising=False)
    backend = GeminiRealtimeBackend(model=DEFAULT_GEMINI_MODEL)

    with pytest.raises(BackendConfigError):
        await backend.connect({"instructions": "hi", "voice": "alloy"})


# ---------------------------------------------------------------------------
# Event normalization — synthesized speech_started / stopped
# ---------------------------------------------------------------------------


def _new_backend() -> GeminiRealtimeBackend:
    return GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: object(),
        types_module=_FakeTypes,
    )


def test_normalize_input_transcript_partial_emits_speech_started_then_delta() -> None:
    backend = _new_backend()
    msg = _build_server_message(input_text="why is", input_finished=False)
    events = backend._normalize_message(msg)
    assert events[0] == {"type": "input.speech_started"}
    assert events[1]["type"] == "input_transcript.delta"
    assert events[1]["text"] == "why is"
    assert events[1]["language"] == "en-US"


def test_normalize_input_transcript_second_delta_does_not_re_emit_started() -> None:
    backend = _new_backend()
    # First delta arms speech_started.
    backend._normalize_message(_build_server_message(input_text="why", input_finished=False))
    # Second delta on the same turn — no new speech_started.
    events = backend._normalize_message(
        _build_server_message(input_text="why is the sky", input_finished=False)
    )
    types = [e["type"] for e in events]
    assert "input.speech_started" not in types
    assert "input_transcript.delta" in types


def test_normalize_input_transcript_final_emits_final_and_stopped() -> None:
    backend = _new_backend()
    # Prime speech_started.
    backend._normalize_message(_build_server_message(input_text="why", input_finished=False))
    # Final.
    events = backend._normalize_message(
        _build_server_message(input_text="why is the sky blue", input_finished=True)
    )
    types = [e["type"] for e in events]
    assert "input_transcript.final" in types
    assert "input.speech_stopped" in types


def test_normalize_output_transcript_delta_and_final() -> None:
    backend = _new_backend()
    delta = backend._normalize_message(
        _build_server_message(output_text="The sky is", output_finished=False)
    )
    assert delta == [{"type": "assistant_transcript.delta", "text": "The sky is"}]

    final = backend._normalize_message(
        _build_server_message(output_text="The sky is blue.", output_finished=True)
    )
    assert final[0]["type"] == "assistant_transcript.final"
    assert final[0]["text"] == "The sky is blue."


def test_normalize_audio_chunk_passes_raw_bytes_through() -> None:
    backend = _new_backend()
    events = backend._normalize_message(
        _build_server_message(audio_bytes=b"\x01\x02\x03\x04")
    )
    assert events == [{"type": "audio.chunk", "audio": b"\x01\x02\x03\x04"}]


def test_normalize_audio_chunk_decodes_base64_string_defensively() -> None:
    import base64

    backend = _new_backend()
    payload = b"\x10\x20\x30"
    encoded = base64.b64encode(payload).decode("ascii")
    msg = _build_server_message(audio_bytes=None)
    # Manually inject a base64 string (older SDK variants may do this).
    msg.server_content["model_turn"] = SimpleNamespace(
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=encoded))]
    )
    events = backend._normalize_message(msg)
    assert events == [{"type": "audio.chunk", "audio": payload}]


def test_normalize_turn_complete_emits_response_done() -> None:
    backend = _new_backend()
    events = backend._normalize_message(_build_server_message(turn_complete=True))
    assert events == [{"type": "response.done"}]


def test_normalize_turn_complete_flushes_dangling_speech_stopped() -> None:
    backend = _new_backend()
    # Speech started but never finished — turn_complete should flush.
    backend._normalize_message(_build_server_message(input_text="uh", input_finished=False))
    events = backend._normalize_message(_build_server_message(turn_complete=True))
    types = [e["type"] for e in events]
    assert "input.speech_stopped" in types
    assert "response.done" in types


def test_normalize_message_without_server_content_returns_empty() -> None:
    backend = _new_backend()
    assert backend._normalize_message(SimpleNamespace(server_content=None)) == []


# ---------------------------------------------------------------------------
# server_content.interrupted → barge-in trigger
#
# Regression for the on-device 2026-04-24 session where the child said
# "stop" mid-assistant-response, Gemini sent server_content.interrupted=True,
# and the robot kept talking until the output audio queue drained naturally.
# The backend used to only log the signal; it must surface it as
# input.speech_started so the handler can flush.
# ---------------------------------------------------------------------------


def test_normalize_interrupted_emits_speech_started() -> None:
    backend = _new_backend()
    msg = SimpleNamespace(server_content=SimpleNamespace(interrupted=True))
    events = backend._normalize_message(msg)
    assert {"type": "input.speech_started"} in events


def test_normalize_interrupted_with_turn_complete_orders_speech_started_first() -> None:
    """Gemini often ships interrupted=True alongside turn_complete.

    The barge-in event must be yielded before response.done, because the
    handler's cancel path is gated on _assistant_active — and response.done
    clears that flag. If the order flipped, the flush would never run.
    """
    backend = _new_backend()
    msg = SimpleNamespace(
        server_content=SimpleNamespace(interrupted=True, turn_complete=True)
    )
    events = backend._normalize_message(msg)
    types = [e["type"] for e in events]
    assert "input.speech_started" in types
    assert "response.done" in types
    assert types.index("input.speech_started") < types.index("response.done")


def test_normalize_no_interrupted_field_does_not_emit_speech_started() -> None:
    backend = _new_backend()
    msg = _build_server_message(audio_bytes=b"\x01\x02")
    events = backend._normalize_message(msg)
    types = [e["type"] for e in events]
    assert "input.speech_started" not in types


# ---------------------------------------------------------------------------
# send_audio / send_text / cancel_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_audio_wraps_bytes_in_pcm_blob() -> None:
    session = _FakeSession(messages=[])
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})
    await backend.send_audio(b"\x00\x01\x02")
    await backend.close()

    assert len(session.sent_audio) == 1
    blob = session.sent_audio[0]
    assert blob.data == b"\x00\x01\x02"
    assert blob.mime_type == GEMINI_INPUT_MIME


@pytest.mark.asyncio
async def test_send_audio_ignores_empty_chunks() -> None:
    session = _FakeSession(messages=[])
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})
    await backend.send_audio(b"")
    await backend.close()

    assert session.sent_audio == []


@pytest.mark.asyncio
async def test_send_text_forwards_as_user_content() -> None:
    session = _FakeSession(messages=[])
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})
    await backend.send_text("hello")
    await backend.close()

    assert len(session.sent_text) == 1
    turns, turn_complete = session.sent_text[0]
    assert turn_complete is True
    # Our _FakeTypes.Content records kwargs; check role and parts were set.
    assert turns.role == "user"
    assert turns.parts[0].text == "hello"


@pytest.mark.asyncio
async def test_cancel_response_sends_audio_stream_end() -> None:
    session = _FakeSession(messages=[])
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})
    await backend.cancel_response()
    await backend.close()

    assert session.cancelled_with == [True]


# ---------------------------------------------------------------------------
# Full reader-loop round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_loop_yields_audio_and_response_done() -> None:
    session = _FakeSession(
        messages=[
            _build_server_message(audio_bytes=b"\x01\x02"),
            _build_server_message(turn_complete=True),
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    # Pull a bounded number of events.
    received: list[dict] = []
    gen = backend.events()
    try:
        for _ in range(2):
            received.append(await asyncio.wait_for(gen.__anext__(), timeout=1.0))
    finally:
        await backend.close()

    types = [e["type"] for e in received]
    assert "audio.chunk" in types
    assert "response.done" in types


@pytest.mark.asyncio
async def test_reader_loop_handles_multiple_turns_on_single_session() -> None:
    """Regression test for the post-turn-1 hang.

    Gemini's ``session.receive()`` iterator exits on ``turn_complete``;
    the reader loop must re-enter ``receive()`` so the SAME WebSocket
    serves multiple turns. Two scripted turns should produce two
    ``response.done`` events without reconnecting.
    """
    session = _MultiTurnFakeSession(
        turns=[
            [
                _build_server_message(audio_bytes=b"\x01"),
                _build_server_message(turn_complete=True),
            ],
            [
                _build_server_message(audio_bytes=b"\x02"),
                _build_server_message(turn_complete=True),
            ],
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    received: list[dict] = []
    gen = backend.events()
    try:
        # 4 events total: 2 audio.chunk + 2 response.done across 2 turns.
        for _ in range(4):
            received.append(await asyncio.wait_for(gen.__anext__(), timeout=1.0))
    finally:
        await backend.close()

    types = [e["type"] for e in received]
    assert types.count("response.done") == 2, (
        f"expected two response.done events across two turns, got types={types}"
    )
    assert types.count("audio.chunk") == 2
    # Both scripted turns were consumed, plus a third receive() call that
    # blocked on the idle-session Future until close() cancelled it.
    assert session.receive_calls >= 2
    # Session context manager entered once and exited once — NO reconnect.
    assert manager.entered is True
    assert manager.exited is True
    assert len(client._live.connect_calls) == 1


@pytest.mark.asyncio
async def test_tool_call_sends_tool_response_before_background_write_finishes() -> None:
    write_started = threading.Event()
    allow_finish = threading.Event()
    write_finished = threading.Event()

    def slow_append(fact: str, path: str | None) -> None:
        assert fact == "Their name is Aanya"
        write_started.set()
        allow_finish.wait(timeout=1.0)
        write_finished.set()

    session = _MultiTurnFakeSession(
        turns=[[_build_tool_call_message(fact="Their name is Aanya"), _build_server_message(turn_complete=True)]]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        memory_append=slow_append,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        event = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert event["type"] == "response.done"
        await _wait_until(lambda: bool(session.tool_responses))
        assert session.tool_responses[0][0].response == {"output": {"status": "scheduled"}}
        assert write_started.is_set() is True
        assert write_finished.is_set() is False
    finally:
        allow_finish.set()
        await _wait_until(write_finished.is_set)
        await backend.close()


@pytest.mark.asyncio
async def test_tool_call_persists_memory_file_after_background_write(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_file, "_today_iso", lambda: "2026-04-24")
    target = tmp_path / "memory.md"
    session = _MultiTurnFakeSession(
        turns=[[_build_tool_call_message(fact="Their name is Aanya"), _build_server_message(turn_complete=True)]]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        memory_file_path=str(target),
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        event = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert event["type"] == "response.done"
        await _wait_until(lambda: "Their name is Aanya" in memory_file.read(target))
        assert session.tool_responses[0][0].response == {"output": {"status": "scheduled"}}
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_tool_call_logs_warning_when_background_write_fails(
    caplog,
) -> None:
    def failing_append(fact: str, path: str | None) -> None:
        raise OSError("disk full")

    session = _MultiTurnFakeSession(
        turns=[[_build_tool_call_message(fact="Their name is Aanya"), _build_server_message(turn_complete=True)]]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        memory_append=failing_append,
    )

    with caplog.at_level("WARNING"):
        await backend.connect({"instructions": "hi", "voice": "alloy"})
        gen = backend.events()
        try:
            event = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            assert event["type"] == "response.done"
            await _wait_until(lambda: any("remember write failed" in r.message for r in caplog.records))
        finally:
            await backend.close()

    assert session.tool_responses[0][0].response == {"output": {"status": "scheduled"}}
    assert not any(
        "remember write succeeded" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_tool_call_logs_info_when_background_write_succeeds(
    caplog,
) -> None:
    def ok_append(fact: str, path: str | None) -> None:
        return None

    session = _MultiTurnFakeSession(
        turns=[[_build_tool_call_message(fact="Their name is Aanya"), _build_server_message(turn_complete=True)]]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        memory_append=ok_append,
    )

    with caplog.at_level("INFO"):
        await backend.connect({"instructions": "hi", "voice": "alloy"})
        gen = backend.events()
        try:
            event = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            assert event["type"] == "response.done"
            await _wait_until(
                lambda: any(
                    "remember write succeeded" in r.message
                    and "Their name is Aanya" in r.message
                    for r in caplog.records
                )
            )
        finally:
            await backend.close()

    assert not any(
        "remember write failed" in r.message for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Chunk G: remember_face / forget_face tool calls
# ---------------------------------------------------------------------------


def _build_face_tool_call_message(
    *,
    name: str,
    args: dict[str, Any],
    call_id: str = "call-face-1",
) -> Any:
    function_call = SimpleNamespace(id=call_id, name=name, args=args)
    return SimpleNamespace(tool_call=SimpleNamespace(function_calls=[function_call]))


@pytest.mark.asyncio
async def test_remember_face_persists_encoding_with_one_face(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_service

    enroll_calls: list[tuple[str, Any, Any]] = []

    def fake_enroll(name: str, frame: Any, relationship: Any = None) -> Any:
        enroll_calls.append((name, frame, relationship))
        return face_service.EnrollResult.OK

    monkeypatch.setattr(face_service, "enroll_from_frame", fake_enroll)

    memory_appends: list[tuple[str, Any]] = []

    def memory_append(fact: str, path: Any) -> None:
        memory_appends.append((fact, path))

    fake_frame = object()
    session = _MultiTurnFakeSession(
        turns=[
            [
                _build_face_tool_call_message(
                    name="remember_face",
                    args={"name": "Aunt Priya", "relationship": "is Myra's aunt"},
                ),
                _build_server_message(turn_complete=True),
            ]
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        face_frame_provider=lambda: fake_frame,
        memory_append=memory_append,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        event = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert event["type"] == "response.done"
        await _wait_until(lambda: bool(session.tool_responses) and bool(memory_appends))
    finally:
        await backend.close()

    assert enroll_calls == [("Aunt Priya", fake_frame, "is Myra's aunt")]
    assert memory_appends[0][0] == "Aunt Priya is Myra's aunt"
    response = session.tool_responses[0][0].response
    assert response == {"output": {"status": "ok"}}


@pytest.mark.asyncio
async def test_remember_face_refuses_when_zero_faces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_service

    monkeypatch.setattr(
        face_service,
        "enroll_from_frame",
        lambda *a, **kw: face_service.EnrollResult.NO_FACE,
    )

    memory_appends: list[Any] = []

    def memory_append(fact: str, path: Any) -> None:
        memory_appends.append(fact)

    session = _MultiTurnFakeSession(
        turns=[
            [
                _build_face_tool_call_message(
                    name="remember_face",
                    args={"name": "Aunt Priya"},
                ),
                _build_server_message(turn_complete=True),
            ]
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        face_frame_provider=lambda: object(),
        memory_append=memory_append,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await _wait_until(lambda: bool(session.tool_responses))
    finally:
        await backend.close()

    response = session.tool_responses[0][0].response
    assert response == {"output": {"status": "no_face"}}
    assert memory_appends == []


@pytest.mark.asyncio
async def test_remember_face_refuses_when_multiple_faces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_service

    monkeypatch.setattr(
        face_service,
        "enroll_from_frame",
        lambda *a, **kw: face_service.EnrollResult.MULTIPLE_FACES,
    )

    memory_appends: list[Any] = []

    session = _MultiTurnFakeSession(
        turns=[
            [
                _build_face_tool_call_message(
                    name="remember_face",
                    args={"name": "Aunt Priya"},
                ),
                _build_server_message(turn_complete=True),
            ]
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        face_frame_provider=lambda: object(),
        memory_append=lambda fact, path: memory_appends.append(fact),
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await _wait_until(lambda: bool(session.tool_responses))
    finally:
        await backend.close()

    response = session.tool_responses[0][0].response
    assert response == {"output": {"status": "multiple_faces"}}
    assert memory_appends == []


@pytest.mark.asyncio
async def test_remember_face_refuses_when_capacity_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_service

    monkeypatch.setattr(
        face_service,
        "enroll_from_frame",
        lambda *a, **kw: face_service.EnrollResult.CAPACITY_EXCEEDED,
    )

    memory_appends: list[Any] = []

    session = _MultiTurnFakeSession(
        turns=[
            [
                _build_face_tool_call_message(
                    name="remember_face",
                    args={"name": "Aunt Priya"},
                ),
                _build_server_message(turn_complete=True),
            ]
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        face_frame_provider=lambda: object(),
        memory_append=lambda fact, path: memory_appends.append(fact),
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await _wait_until(lambda: bool(session.tool_responses))
    finally:
        await backend.close()

    response = session.tool_responses[0][0].response
    assert response == {"output": {"status": "capacity"}}
    assert memory_appends == []


@pytest.mark.asyncio
async def test_remember_face_refuses_when_library_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_service

    monkeypatch.setattr(
        face_service,
        "enroll_from_frame",
        lambda *a, **kw: face_service.EnrollResult.LIBRARY_MISSING,
    )

    memory_appends: list[Any] = []

    session = _MultiTurnFakeSession(
        turns=[
            [
                _build_face_tool_call_message(
                    name="remember_face",
                    args={"name": "Aunt Priya"},
                ),
                _build_server_message(turn_complete=True),
            ]
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        face_frame_provider=lambda: object(),
        memory_append=lambda fact, path: memory_appends.append(fact),
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await _wait_until(lambda: bool(session.tool_responses))
    finally:
        await backend.close()

    response = session.tool_responses[0][0].response
    assert response == {"output": {"status": "unavailable"}}
    assert memory_appends == []


@pytest.mark.asyncio
async def test_remember_face_refuses_when_no_camera(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When face_frame_provider is None (camera unavailable / OpenAI), the
    handler must short-circuit with a no_face refusal and never call
    face_service.enroll_from_frame.
    """
    import face_service

    enroll_calls: list[Any] = []

    def fake_enroll(*a: Any, **kw: Any) -> Any:
        enroll_calls.append((a, kw))
        return face_service.EnrollResult.OK

    monkeypatch.setattr(face_service, "enroll_from_frame", fake_enroll)

    session = _MultiTurnFakeSession(
        turns=[
            [
                _build_face_tool_call_message(
                    name="remember_face",
                    args={"name": "Aunt Priya"},
                ),
                _build_server_message(turn_complete=True),
            ]
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        face_frame_provider=None,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await _wait_until(lambda: bool(session.tool_responses))
    finally:
        await backend.close()

    response = session.tool_responses[0][0].response
    assert response["output"]["status"] == "no_face"
    assert enroll_calls == []


@pytest.mark.asyncio
async def test_forget_face_removes_encoding_and_memory_line(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_service

    forget_calls: list[str] = []

    def fake_forget(name: str) -> bool:
        forget_calls.append(name)
        return True

    monkeypatch.setattr(face_service, "forget", fake_forget)
    monkeypatch.setattr(memory_file, "_today_iso", lambda: "2026-04-24")

    target = tmp_path / "memory.md"
    target.write_text(
        "# Things to remember about the child\n\n"
        "- Aunt Priya is Myra's aunt _(2026-04-24)_\n"
        "- Their name is Aanya _(2026-04-24)_\n",
        encoding="utf-8",
    )

    session = _MultiTurnFakeSession(
        turns=[
            [
                _build_face_tool_call_message(
                    name="forget_face",
                    args={"name": "Aunt Priya"},
                ),
                _build_server_message(turn_complete=True),
            ]
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        face_frame_provider=lambda: object(),
        memory_file_path=str(target),
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await _wait_until(lambda: bool(session.tool_responses))
    finally:
        await backend.close()

    assert forget_calls == ["Aunt Priya"]
    response = session.tool_responses[0][0].response
    assert response == {"output": {"status": "ok"}}
    text = target.read_text(encoding="utf-8")
    assert "Aunt Priya" not in text
    assert "Their name is Aanya" in text


@pytest.mark.asyncio
async def test_forget_face_returns_not_found_when_unknown(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import face_service

    monkeypatch.setattr(face_service, "forget", lambda name: False)

    # A memory file that does NOT contain the queried name — the helper
    # must therefore not modify it.
    target = tmp_path / "memory.md"
    target.write_text(
        "# Things to remember about the child\n\n"
        "- Their name is Aanya _(2026-04-24)_\n",
        encoding="utf-8",
    )
    original = target.read_text(encoding="utf-8")

    session = _MultiTurnFakeSession(
        turns=[
            [
                _build_face_tool_call_message(
                    name="forget_face",
                    args={"name": "Stranger"},
                ),
                _build_server_message(turn_complete=True),
            ]
        ]
    )
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
        face_frame_provider=lambda: object(),
        memory_file_path=str(target),
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    gen = backend.events()
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        await _wait_until(lambda: bool(session.tool_responses))
    finally:
        await backend.close()

    response = session.tool_responses[0][0].response
    assert response == {"output": {"status": "not_found"}}
    assert target.read_text(encoding="utf-8") == original


def test_face_tools_not_registered_on_openai_backend() -> None:
    """The OpenAI backend builds its session payload from the profile's
    allowed_tools list. ``remember_face`` / ``forget_face`` are Gemini-only
    (FR-KID-23); they must not appear there. The locked kids-teacher
    profile ships an empty tools allowlist so the OpenAI session payload
    cannot accidentally surface them.
    """
    from kids_teacher_profile import load_profile

    profile = load_profile(present_names=[])
    assert "remember_face" not in profile.allowed_tools
    assert "forget_face" not in profile.allowed_tools


def test_remember_face_tool_declaration_present_on_gemini() -> None:
    """The Gemini live config must register both ``remember_face`` and
    ``forget_face`` FunctionDeclarations alongside the existing
    ``remember`` declaration.
    """
    config = build_gemini_live_config(
        {"instructions": "hi", "voice": "alloy"}, _FakeTypes
    )
    declared_names = {
        tool.function_declarations[0].name for tool in config.tools
    }
    assert "remember" in declared_names
    assert "remember_face" in declared_names
    assert "forget_face" in declared_names
