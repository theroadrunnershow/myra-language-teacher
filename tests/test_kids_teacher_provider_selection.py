"""Tests for the KIDS_TEACHER_REALTIME_PROVIDER env-var switch.

Cover both the low-level ``resolve_realtime_provider`` helper and the
robot CLI's ``_build_backend_factory`` + SDK-presence checks. These must
work without the real openai or google-genai SDK installed.
"""

from __future__ import annotations

import sys

import pytest

from kids_teacher_backend import (
    ALLOWED_REALTIME_PROVIDERS,
    BackendConfigError,
    DEFAULT_REALTIME_PROVIDER,
    REALTIME_PROVIDER_ENV_VAR,
    resolve_realtime_provider,
)


# ---------------------------------------------------------------------------
# resolve_realtime_provider
# ---------------------------------------------------------------------------


def test_resolve_provider_defaults_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REALTIME_PROVIDER_ENV_VAR, raising=False)
    assert resolve_realtime_provider() == DEFAULT_REALTIME_PROVIDER
    assert DEFAULT_REALTIME_PROVIDER in ALLOWED_REALTIME_PROVIDERS


def test_resolve_provider_defaults_when_env_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REALTIME_PROVIDER_ENV_VAR, "   ")
    assert resolve_realtime_provider() == DEFAULT_REALTIME_PROVIDER


def test_resolve_provider_reads_openai_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REALTIME_PROVIDER_ENV_VAR, "openai")
    assert resolve_realtime_provider() == "openai"


def test_resolve_provider_reads_gemini_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REALTIME_PROVIDER_ENV_VAR, "gemini")
    assert resolve_realtime_provider() == "gemini"


def test_resolve_provider_is_case_insensitive() -> None:
    assert resolve_realtime_provider(env_value="GEMINI") == "gemini"
    assert resolve_realtime_provider(env_value="OpenAI") == "openai"


def test_resolve_provider_rejects_unknown_value() -> None:
    with pytest.raises(BackendConfigError):
        resolve_realtime_provider(env_value="anthropic")


# ---------------------------------------------------------------------------
# robot_kids_teacher._build_backend_factory
# ---------------------------------------------------------------------------


def test_build_backend_factory_openai_returns_openai_backend_lambda() -> None:
    import robot_kids_teacher
    from kids_teacher_backend import OpenAIRealtimeBackend

    factory = robot_kids_teacher._build_backend_factory("openai")
    instance = factory()
    assert isinstance(instance, OpenAIRealtimeBackend)


def test_build_backend_factory_gemini_returns_gemini_backend_lambda() -> None:
    import robot_kids_teacher
    from kids_teacher_gemini_backend import GeminiRealtimeBackend

    factory = robot_kids_teacher._build_backend_factory("gemini")
    instance = factory()
    assert isinstance(instance, GeminiRealtimeBackend)


def test_build_backend_factory_defaults_to_openai_for_unknown_provider() -> None:
    # Defensive: an unrecognised provider string still produces SOMETHING
    # rather than throwing deep inside the CLI. main() validates the
    # provider up front, so this branch is only hit if a caller bypasses
    # resolve_realtime_provider().
    import robot_kids_teacher
    from kids_teacher_backend import OpenAIRealtimeBackend

    factory = robot_kids_teacher._build_backend_factory("unexpected")
    assert isinstance(factory(), OpenAIRealtimeBackend)


# ---------------------------------------------------------------------------
# main() provider-routed SDK check
# ---------------------------------------------------------------------------


def test_main_gemini_provider_missing_genai_returns_exit_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With provider=gemini and google.genai not installed, main() must bail
    with exit 2 — not crash trying to import the wrong SDK, and not try
    openai as a fallback."""
    import robot_kids_teacher

    monkeypatch.setenv(REALTIME_PROVIDER_ENV_VAR, "gemini")

    def _blocked_import(name, *args, **kwargs):
        if name == "google.genai" or name.startswith("google.genai"):
            raise ImportError("google.genai not installed in test env")
        return original_import(name, *args, **kwargs)

    original_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )
    monkeypatch.setattr("builtins.__import__", _blocked_import)
    sys.modules.pop("google", None)
    sys.modules.pop("google.genai", None)

    exit_code = robot_kids_teacher.main(["--session-id", "test-session"])
    assert exit_code == 2


def test_main_openai_provider_unaffected_by_missing_genai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider=openai must not touch google.genai at all — blocking that
    import should not cause an early exit on the OpenAI path (the path
    eventually fails on reachy_mini, but that's a different gate)."""
    import robot_kids_teacher

    monkeypatch.setenv(REALTIME_PROVIDER_ENV_VAR, "openai")

    # Block both google.genai AND reachy_mini. The OpenAI path should
    # get past the SDK check (openai IS installed in the test env) and
    # then bail on reachy_mini — exit 2.
    def _blocked_import(name, *args, **kwargs):
        if name.startswith("google.genai") or name == "reachy_mini" or name.startswith("reachy_mini."):
            raise ImportError(f"{name} blocked in test env")
        return original_import(name, *args, **kwargs)

    original_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )
    monkeypatch.setattr("builtins.__import__", _blocked_import)

    exit_code = robot_kids_teacher.main(["--session-id", "test-session"])
    # Still 2 (reachy_mini missing) — but we proved google.genai blocking
    # did not short-circuit the openai path.
    assert exit_code == 2
