"""Tests for src/kids_teacher_backend.py.

These tests must NOT require the real openai SDK or a real API key. The
concrete OpenAI backend lazy-imports openai inside connect(), so simply
instantiating it (or calling resolve_realtime_model / build_session_payload)
must work on a lightweight image where openai is not installed.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from kids_teacher_backend import (
    ALLOWED_REALTIME_MODELS,
    BackendConfigError,
    DEFAULT_REALTIME_MODEL,
    OpenAIRealtimeBackend,
    REALTIME_MODEL_ENV_VAR,
    build_session_payload,
    resolve_realtime_model,
)
from kids_teacher_types import (
    KidsTeacherProfile,
    KidsTeacherSessionConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(
    *,
    tools: tuple[str, ...] = (),
    voice: str = "alloy",
    instructions: str = "Be a warm preschool teacher.",
) -> KidsTeacherProfile:
    return KidsTeacherProfile(
        name="kids_teacher",
        instructions=instructions,
        voice=voice,
        allowed_tools=tools,
        locked=True,
    )


def _make_config(profile: KidsTeacherProfile) -> KidsTeacherSessionConfig:
    return KidsTeacherSessionConfig(
        session_id="test-session",
        model="gpt-realtime-mini",
        profile=profile,
        enabled_languages=("english", "telugu"),
        default_explanation_language="english",
    )


# ---------------------------------------------------------------------------
# resolve_realtime_model
# ---------------------------------------------------------------------------


def test_resolve_realtime_model_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REALTIME_MODEL_ENV_VAR, raising=False)
    assert resolve_realtime_model() == DEFAULT_REALTIME_MODEL
    assert DEFAULT_REALTIME_MODEL in ALLOWED_REALTIME_MODELS


def test_resolve_realtime_model_defaults_when_env_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REALTIME_MODEL_ENV_VAR, "   ")
    assert resolve_realtime_model() == DEFAULT_REALTIME_MODEL


def test_resolve_realtime_model_honors_explicit_value() -> None:
    assert resolve_realtime_model(env_value="gpt-realtime") == "gpt-realtime"


def test_resolve_realtime_model_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REALTIME_MODEL_ENV_VAR, "gpt-realtime")
    assert resolve_realtime_model() == "gpt-realtime"


def test_resolve_realtime_model_rejects_gpt_4() -> None:
    with pytest.raises(BackendConfigError):
        resolve_realtime_model(env_value="gpt-4")


def test_resolve_realtime_model_rejects_gpt_realtime_ultra() -> None:
    with pytest.raises(BackendConfigError):
        resolve_realtime_model(env_value="gpt-realtime-ultra")


# ---------------------------------------------------------------------------
# build_session_payload
# ---------------------------------------------------------------------------


def test_build_session_payload_includes_profile_fields_and_vad() -> None:
    profile = _make_profile(voice="verse", instructions="Only safe topics.")
    payload = build_session_payload(_make_config(profile))

    assert payload["instructions"] == "Only safe topics."
    assert payload["voice"] == "verse"
    assert payload["modalities"] == ["audio", "text"]
    assert payload["input_audio_transcription"] == {"model": "gpt-4o-mini-transcribe"}
    assert payload["turn_detection"] == {"type": "server_vad"}
    assert payload["tool_choice"] == "auto"


def test_build_session_payload_empty_tools_when_allowlist_empty() -> None:
    profile = _make_profile(tools=())
    payload = build_session_payload(_make_config(profile))
    assert payload["tools"] == []


def test_build_session_payload_stubs_tool_specs_when_allowlist_nonempty() -> None:
    profile = _make_profile(tools=("wave_hello", "nod_head"))
    payload = build_session_payload(_make_config(profile))
    assert payload["tools"] == [
        {"type": "function", "name": "wave_hello"},
        {"type": "function", "name": "nod_head"},
    ]


def test_build_session_payload_honors_custom_modalities() -> None:
    profile = _make_profile()
    payload = build_session_payload(_make_config(profile), modalities=("text",))
    assert payload["modalities"] == ["text"]


# ---------------------------------------------------------------------------
# OpenAIRealtimeBackend
# ---------------------------------------------------------------------------


def test_openai_backend_does_not_import_openai_at_module_load() -> None:
    # Constructing the backend must not pull in the real openai SDK.
    # We verify by constructing an instance and asserting openai is not
    # now a live module attribute on the backend module.
    backend = OpenAIRealtimeBackend(model="gpt-realtime-mini")
    assert backend.model == "gpt-realtime-mini"

    import kids_teacher_backend as ktb

    assert not hasattr(ktb, "openai"), (
        "kids_teacher_backend must not import openai at module level"
    )


def test_openai_backend_module_importable_without_openai_installed() -> None:
    # The module should already be imported; confirm it's loaded and
    # does not rely on a top-level 'openai' being present in sys.modules.
    assert "kids_teacher_backend" in sys.modules


def test_openai_backend_uses_injected_client_factory_model_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REALTIME_MODEL_ENV_VAR, raising=False)
    calls = []

    def factory() -> object:
        calls.append("factory")
        return object()

    backend = OpenAIRealtimeBackend(client_factory=factory)
    assert backend.model == DEFAULT_REALTIME_MODEL
    # Constructing does NOT call the factory yet — only connect() does.
    assert calls == []


class _StubSession:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def update(self, *, session: dict) -> None:
        self.updates.append(session)


class _StubConnection:
    def __init__(self) -> None:
        self.session = _StubSession()
        self.closed = False

    def __aiter__(self) -> "_StubConnection":
        return self

    async def __anext__(self) -> dict:
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True


class _StubConnectionManager:
    def __init__(self, connection: _StubConnection) -> None:
        self._connection = connection
        self.enter_calls = 0

    async def enter(self) -> _StubConnection:
        self.enter_calls += 1
        return self._connection


class _StubRealtimeNamespace:
    def __init__(self, manager: _StubConnectionManager) -> None:
        self._manager = manager
        self.models: list[str] = []

    def connect(self, *, model: str) -> _StubConnectionManager:
        self.models.append(model)
        return self._manager


class _FailingConnectionManager:
    async def enter(self) -> _StubConnection:
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_openai_backend_connect_supports_connection_manager_shape() -> None:
    connection = _StubConnection()
    manager = _StubConnectionManager(connection)
    realtime = _StubRealtimeNamespace(manager)
    client = SimpleNamespace(beta=SimpleNamespace(realtime=realtime))
    backend = OpenAIRealtimeBackend(
        model="gpt-realtime-mini",
        client_factory=lambda: client,
    )

    payload = {"instructions": "Only safe topics."}
    await backend.connect(payload)
    await backend.close()

    assert realtime.models == ["gpt-realtime-mini"]
    assert manager.enter_calls == 1
    assert connection.session.updates == [payload]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_openai_backend_connect_raises_when_connection_open_fails() -> None:
    client = SimpleNamespace(
        beta=SimpleNamespace(realtime=SimpleNamespace(connect=lambda *, model: _FailingConnectionManager()))
    )
    backend = OpenAIRealtimeBackend(client_factory=lambda: client)

    with pytest.raises(RuntimeError, match="boom"):
        await backend.connect({"instructions": "Only safe topics."})


# ---------------------------------------------------------------------------
# _normalize_event — translation of raw OpenAI Realtime events
# ---------------------------------------------------------------------------


import base64  # noqa: E402


def test_normalize_speech_started() -> None:
    event = OpenAIRealtimeBackend._normalize_event(
        {"type": "input_audio_buffer.speech_started"}
    )
    assert event == {"type": "input.speech_started"}


def test_normalize_speech_stopped() -> None:
    event = OpenAIRealtimeBackend._normalize_event(
        {"type": "input_audio_buffer.speech_stopped"}
    )
    assert event == {"type": "input.speech_stopped"}


def test_normalize_input_transcript_delta() -> None:
    event = OpenAIRealtimeBackend._normalize_event(
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "delta": "why is",
        }
    )
    assert event == {"type": "input_transcript.delta", "text": "why is", "language": None}


def test_normalize_input_transcript_completed() -> None:
    event = OpenAIRealtimeBackend._normalize_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "why is the sky blue",
        }
    )
    assert event == {
        "type": "input_transcript.final",
        "text": "why is the sky blue",
        "language": None,
    }


def test_normalize_assistant_transcript_done() -> None:
    event = OpenAIRealtimeBackend._normalize_event(
        {
            "type": "response.audio_transcript.done",
            "transcript": "The sky is blue.",
        }
    )
    assert event == {
        "type": "assistant_transcript.final",
        "text": "The sky is blue.",
        "language": None,
    }


def test_normalize_audio_delta_decodes_base64() -> None:
    payload = b"\x00\x01\x02\x03"
    b64 = base64.b64encode(payload).decode("ascii")
    event = OpenAIRealtimeBackend._normalize_event(
        {"type": "response.audio.delta", "delta": b64}
    )
    assert event == {"type": "audio.chunk", "audio": payload}


def test_normalize_audio_delta_invalid_base64_drops_chunk() -> None:
    event = OpenAIRealtimeBackend._normalize_event(
        {"type": "response.audio.delta", "delta": "!!!not-base64!!!"}
    )
    assert event == {"type": "audio.chunk", "audio": b""}


def test_normalize_audio_delta_accepts_raw_bytes() -> None:
    # Back-compat / scripted-fake path: if the event already has bytes, pass through.
    event = OpenAIRealtimeBackend._normalize_event(
        {"type": "response.audio.delta", "delta": b"\x10\x20"}
    )
    assert event == {"type": "audio.chunk", "audio": b"\x10\x20"}


def test_normalize_error_with_dict_payload() -> None:
    event = OpenAIRealtimeBackend._normalize_event(
        {"type": "error", "error": {"message": "rate limited", "code": "429"}}
    )
    assert event == {"type": "error", "message": "rate limited"}


def test_normalize_response_done() -> None:
    assert OpenAIRealtimeBackend._normalize_event({"type": "response.done"}) == {
        "type": "response.done"
    }


def test_normalize_unknown_event_returns_none() -> None:
    # Acknowledgements and other unhandled events are silently dropped.
    assert OpenAIRealtimeBackend._normalize_event({"type": "session.created"}) is None
    assert OpenAIRealtimeBackend._normalize_event({"type": "session.updated"}) is None
    assert OpenAIRealtimeBackend._normalize_event({"type": "response.created"}) is None


def test_normalize_event_without_type_returns_none() -> None:
    assert OpenAIRealtimeBackend._normalize_event({}) is None
    assert OpenAIRealtimeBackend._normalize_event(object()) is None


# ---------------------------------------------------------------------------
# Audio encoding (outbound)
# ---------------------------------------------------------------------------


def test_encode_audio_chunk_round_trip() -> None:
    from kids_teacher_backend import _encode_audio_chunk, _decode_audio_delta

    payload = b"\x01\x02\x03\x04\x05"
    encoded = _encode_audio_chunk(payload)
    # Encoded value is a base64 ASCII string.
    assert isinstance(encoded, str)
    assert _decode_audio_delta(encoded) == payload
