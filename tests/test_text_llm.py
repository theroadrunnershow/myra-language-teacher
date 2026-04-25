"""Tests for src/text_llm.py provider abstraction.

These must NOT require ollama / openai / google-genai to be installed —
the dispatcher reads provider config and only lazy-imports the SDK when
that provider's `_complete_*` is invoked.
"""

from __future__ import annotations

import pytest

import text_llm


class _FakeOllamaModule:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def Client(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


def test_resolve_provider_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(text_llm.PROVIDER_ENV_VAR, raising=False)
    assert text_llm.resolve_provider() == "ollama"


def test_resolve_provider_blank_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(text_llm.PROVIDER_ENV_VAR, "   ")
    assert text_llm.resolve_provider() == "ollama"


def test_resolve_provider_explicit() -> None:
    assert text_llm.resolve_provider(env_value="GEMINI") == "gemini"
    assert text_llm.resolve_provider(env_value="openai") == "openai"


def test_resolve_provider_rejects_unknown() -> None:
    with pytest.raises(text_llm.TextLLMError):
        text_llm.resolve_provider(env_value="anthropic")


def test_resolve_model_uses_provider_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(text_llm.MODEL_ENV_VAR, raising=False)
    assert text_llm.resolve_model("ollama") == "llama3.2:3b"
    assert text_llm.resolve_model("gemini") == "gemini-2.5-flash"
    assert text_llm.resolve_model("openai") == "gpt-4o-mini"


def test_resolve_model_honors_explicit_override() -> None:
    assert text_llm.resolve_model("ollama", env_value="qwen2.5:7b") == "qwen2.5:7b"


def test_create_ollama_client_uses_plain_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(text_llm.OLLAMA_HOST_ENV_VAR, raising=False)
    monkeypatch.delenv(text_llm.OLLAMA_API_KEY_ENV_VAR, raising=False)
    fake = _FakeOllamaModule()

    client = text_llm._create_ollama_client(fake, timeout_seconds=12.5)

    assert client == {"timeout": 12.5}


def test_create_ollama_client_uses_direct_cloud_when_api_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(text_llm.OLLAMA_HOST_ENV_VAR, raising=False)
    monkeypatch.setenv(text_llm.OLLAMA_API_KEY_ENV_VAR, "secret-key")
    fake = _FakeOllamaModule()

    client = text_llm._create_ollama_client(fake, timeout_seconds=3.0)

    assert client["timeout"] == 3.0
    assert client["host"] == "https://ollama.com"
    assert client["headers"] == {"Authorization": "Bearer secret-key"}


def test_create_ollama_client_preserves_non_cloud_host_and_never_forwards_api_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(text_llm.OLLAMA_HOST_ENV_VAR, "http://10.0.0.5:11434")
    monkeypatch.setenv(text_llm.OLLAMA_API_KEY_ENV_VAR, "secret-key")
    fake = _FakeOllamaModule()

    with caplog.at_level("WARNING"):
        client = text_llm._create_ollama_client(fake, timeout_seconds=4.0)

    assert client == {"timeout": 4.0, "host": "http://10.0.0.5:11434"}
    assert "ignoring OLLAMA_API_KEY" in caplog.text


def test_create_ollama_client_rejects_insecure_official_cloud_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(text_llm.OLLAMA_HOST_ENV_VAR, "http://ollama.com")
    monkeypatch.setenv(text_llm.OLLAMA_API_KEY_ENV_VAR, "secret-key")
    fake = _FakeOllamaModule()

    with pytest.raises(text_llm.TextLLMError, match="requires OLLAMA_HOST=https://ollama.com"):
        text_llm._create_ollama_client(fake, timeout_seconds=4.0)


def test_complete_dispatches_to_provider_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def fake_provider(**kwargs):
        captured.update(kwargs)
        return "OK"

    monkeypatch.setitem(text_llm._PROVIDERS, "ollama", fake_provider)
    monkeypatch.setenv(text_llm.PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(text_llm.MODEL_ENV_VAR, "test-model")

    out = text_llm.complete(
        system="be terse", user="say hi", json_mode=True, temperature=0.2
    )
    assert out == "OK"
    assert captured["model"] == "test-model"
    assert captured["system"] == "be terse"
    assert captured["user"] == "say hi"
    assert captured["json_mode"] is True
    assert captured["temperature"] == 0.2


def test_complete_explicit_provider_and_model_override_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(text_llm.PROVIDER_ENV_VAR, "ollama")
    monkeypatch.setenv(text_llm.MODEL_ENV_VAR, "ignored")

    captured: dict = {}

    def fake_gemini(**kwargs):
        captured.update(kwargs)
        return "from-gemini"

    monkeypatch.setitem(text_llm._PROVIDERS, "gemini", fake_gemini)
    out = text_llm.complete(
        system="s", user="u", provider="gemini", model="explicit-model"
    )
    assert out == "from-gemini"
    assert captured["model"] == "explicit-model"


def test_ollama_provider_raises_clear_error_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force the import to fail
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "ollama":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(text_llm.TextLLMError, match="ollama python SDK"):
        text_llm._complete_ollama(
            system="s",
            user="u",
            temperature=0.0,
            json_mode=False,
            timeout_seconds=1.0,
            model="x",
        )
