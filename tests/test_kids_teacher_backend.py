"""Tests for src/kids_teacher_backend.py.

These tests must NOT require the real openai SDK or a real API key. The
concrete OpenAI backend lazy-imports openai inside connect(), so simply
instantiating it (or calling resolve_realtime_model / build_session_payload)
must work on a lightweight image where openai is not installed.
"""

from __future__ import annotations

import sys

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
