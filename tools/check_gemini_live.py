#!/usr/bin/env python3
"""Smoke test for the Gemini Live API.

Opens a real Live session with the configured key/model and asks for a
one-word reply. Use this to verify GEMINI_API_KEY and
KIDS_TEACHER_GEMINI_MODEL before running the full kids-teacher stack.

Usage
-----
    python tools/check_gemini_live.py
    python tools/check_gemini_live.py --prompt "say hi"
    python tools/check_gemini_live.py --timeout 30

Reads GEMINI_API_KEY from the environment (or repo-root .env) and
KIDS_TEACHER_GEMINI_MODEL if set, defaulting to the AI Studio preview
model used by kids_teacher_gemini_backend.

Exit codes:
    0  reply received
    1  connected but no reply / timeout / runtime error
    2  configuration error (missing key, invalid model, SDK missing)

The raw exception from the SDK is printed on failure so credit/quota/
model errors surface verbatim — e.g. "1011 ... prepayment credits are
depleted" or "1008 ... model not found for API version v1beta".
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Let the script be run as `python tools/check_gemini_live.py` without
# requiring PYTHONPATH=src; we piggy-back on the existing backend module
# for API-key / model resolution instead of re-implementing it.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from env_loader import load_project_dotenv  # noqa: E402
from kids_teacher_backend import BackendConfigError  # noqa: E402
from kids_teacher_gemini_backend import (  # noqa: E402
    GEMINI_API_KEY_ENV_VAR,
    resolve_gemini_model,
)


async def probe(prompt: str, timeout: float) -> int:
    load_project_dotenv()

    api_key = os.environ.get(GEMINI_API_KEY_ENV_VAR)
    if not api_key:
        print(f"FAIL: {GEMINI_API_KEY_ENV_VAR} is not set (check .env).")
        return 2
    print(f"OK:   {GEMINI_API_KEY_ENV_VAR} present (length={len(api_key)}).")

    try:
        model = resolve_gemini_model()
    except BackendConfigError as exc:
        print(f"FAIL: model config invalid: {exc}")
        return 2
    print(f"OK:   model = {model}")

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as exc:
        print(f"FAIL: google-genai SDK not importable: {exc}")
        return 2

    client = genai.Client(api_key=api_key)
    config = genai_types.LiveConnectConfig(
        response_modalities=["TEXT"],
        system_instruction="You are a terse helper. Reply with exactly one word.",
    )

    print(f"...   opening Live session (timeout={timeout}s)...")
    reply_parts: list[str] = []

    async def run_session() -> None:
        async with client.aio.live.connect(model=model, config=config) as session:
            print("OK:   Live session opened.")
            await session.send_client_content(
                turns=genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=prompt)],
                ),
                turn_complete=True,
            )
            print(f"...   sent prompt: {prompt!r}")
            async for message in session.receive():
                server_content = getattr(message, "server_content", None)
                if server_content is None:
                    continue
                model_turn = getattr(server_content, "model_turn", None)
                if model_turn is not None:
                    for part in getattr(model_turn, "parts", None) or []:
                        text = getattr(part, "text", None)
                        if text:
                            reply_parts.append(text)
                if getattr(server_content, "turn_complete", False):
                    break

    try:
        await asyncio.wait_for(run_session(), timeout=timeout)
    except asyncio.TimeoutError:
        print(f"FAIL: no response within {timeout}s.")
        return 1
    except Exception as exc:
        print(f"FAIL: Live session error: {exc!r}")
        return 1

    reply = "".join(reply_parts).strip()
    if not reply:
        print("FAIL: session completed but no text came back.")
        return 1
    print(f"OK:   reply = {reply!r}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Probe the Gemini Live API.")
    p.add_argument(
        "--prompt",
        default="Say the word 'hello' and nothing else.",
        help="Text prompt to send (default asks for a one-word reply).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Overall wall-clock timeout for the connect+reply cycle (seconds).",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(asyncio.run(probe(args.prompt, args.timeout)))
