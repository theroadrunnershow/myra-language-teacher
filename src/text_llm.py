"""Provider-agnostic text-completion abstraction for non-realtime LLM calls.

The realtime path (Gemini Live) is intentionally NOT routed through here —
this module is for short, synchronous, batch-style completions like the
memory reconciler. Default provider is Ollama (local on the robot or
remote via ``OLLAMA_HOST``); flip via ``MYRA_TEXT_LLM_PROVIDER``.

Surface kept tiny on purpose: a single ``complete()`` call with a system
prompt, a user prompt, and an optional JSON mode flag.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

logger = logging.getLogger(__name__)

PROVIDER_ENV_VAR = "MYRA_TEXT_LLM_PROVIDER"
MODEL_ENV_VAR = "MYRA_TEXT_LLM_MODEL"

DEFAULT_PROVIDER = "ollama"

_DEFAULT_MODEL_BY_PROVIDER = {
    "ollama": "llama3.2:3b",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
}


class TextLLMError(RuntimeError):
    """Raised when a text-LLM call fails or is misconfigured."""


def resolve_provider(env_value: str | None = None) -> str:
    raw = env_value if env_value is not None else os.environ.get(PROVIDER_ENV_VAR)
    candidate = (raw or "").strip().lower() or DEFAULT_PROVIDER
    if candidate not in _DEFAULT_MODEL_BY_PROVIDER:
        raise TextLLMError(
            f"Unknown text-LLM provider {candidate!r}. "
            f"Allowed: {sorted(_DEFAULT_MODEL_BY_PROVIDER)}."
        )
    return candidate


def resolve_model(provider: str, env_value: str | None = None) -> str:
    raw = env_value if env_value is not None else os.environ.get(MODEL_ENV_VAR)
    candidate = (raw or "").strip()
    return candidate or _DEFAULT_MODEL_BY_PROVIDER[provider]


def complete(
    *,
    system: str,
    user: str,
    temperature: float = 0.0,
    json_mode: bool = False,
    timeout_seconds: float = 30.0,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Run a synchronous text completion and return the model's output."""
    chosen_provider = provider or resolve_provider()
    chosen_model = model or resolve_model(chosen_provider)
    impl = _PROVIDERS[chosen_provider]
    return impl(
        system=system,
        user=user,
        temperature=temperature,
        json_mode=json_mode,
        timeout_seconds=timeout_seconds,
        model=chosen_model,
    )


# ---------------------------------------------------------------------------
# Provider implementations (lazy-import inside)
# ---------------------------------------------------------------------------


def _complete_ollama(
    *,
    system: str,
    user: str,
    temperature: float,
    json_mode: bool,
    timeout_seconds: float,
    model: str,
) -> str:
    try:
        import ollama  # type: ignore
    except ImportError as exc:
        raise TextLLMError(
            "ollama python SDK is not installed. "
            "Install it (`pip install ollama`) or set "
            f"{PROVIDER_ENV_VAR}=gemini|openai."
        ) from exc

    options: dict[str, float] = {"temperature": temperature}
    kwargs: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": options,
    }
    if json_mode:
        kwargs["format"] = "json"

    client = ollama.Client(timeout=timeout_seconds)
    try:
        response = client.chat(**kwargs)
    except Exception as exc:
        raise TextLLMError(f"ollama chat failed: {exc}") from exc
    message = response.get("message") if isinstance(response, dict) else getattr(response, "message", None)
    if message is None:
        raise TextLLMError(f"ollama response missing 'message': {response!r}")
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if not isinstance(content, str):
        raise TextLLMError(f"ollama response missing string content: {message!r}")
    return content


def _complete_gemini(
    *,
    system: str,
    user: str,
    temperature: float,
    json_mode: bool,
    timeout_seconds: float,
    model: str,
) -> str:
    try:
        from google import genai  # type: ignore
        from google.genai import types as genai_types  # type: ignore
    except ImportError as exc:
        raise TextLLMError("google-genai is not installed.") from exc

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise TextLLMError("GEMINI_API_KEY is not set.")
    client = genai.Client(api_key=api_key)
    config_kwargs: dict[str, object] = {
        "system_instruction": system,
        "temperature": temperature,
    }
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
    try:
        response = client.models.generate_content(
            model=model,
            contents=user,
            config=genai_types.GenerateContentConfig(**config_kwargs),
        )
    except Exception as exc:
        raise TextLLMError(f"gemini generate_content failed: {exc}") from exc
    text = getattr(response, "text", None)
    if not isinstance(text, str):
        raise TextLLMError(f"gemini response missing .text: {response!r}")
    return text


def _complete_openai(
    *,
    system: str,
    user: str,
    temperature: float,
    json_mode: bool,
    timeout_seconds: float,
    model: str,
) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise TextLLMError("openai SDK is not installed.") from exc

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise TextLLMError("OPENAI_API_KEY is not set.")
    client = OpenAI(api_key=api_key, timeout=timeout_seconds)
    kwargs: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        raise TextLLMError(f"openai chat.completions failed: {exc}") from exc
    choice = response.choices[0] if response.choices else None
    if choice is None or choice.message is None or choice.message.content is None:
        raise TextLLMError(f"openai response missing content: {response!r}")
    return choice.message.content


_PROVIDERS: dict[str, Callable[..., str]] = {
    "ollama": _complete_ollama,
    "gemini": _complete_gemini,
    "openai": _complete_openai,
}
