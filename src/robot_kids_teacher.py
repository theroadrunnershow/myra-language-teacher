"""Thin CLI entry point for running the kids-teacher flow headlessly.

Keeps the existing ``robot_teacher.py`` flow untouched. V1 does not force
``openai`` to be installed in the repo — if the SDK is unavailable this
entry point prints a clear message and exits with status 2. Tests import
this module without ever triggering the SDK import.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from typing import Optional

from kids_teacher_backend import resolve_realtime_model
from kids_teacher_flow import KidsTeacherFlowDeps, NullRuntimeHooks, run_kids_teacher_session
from kids_teacher_profile import load_profile
from kids_teacher_types import KidsTeacherSessionConfig

logger = logging.getLogger(__name__)


def _build_config(session_id: str, max_seconds: Optional[int]) -> KidsTeacherSessionConfig:
    profile = load_profile()
    enabled_raw = os.environ.get("KIDS_ENABLED_LANGUAGES", "english,telugu")
    enabled = tuple(p.strip() for p in enabled_raw.split(",") if p.strip()) or ("english",)
    default_lang = os.environ.get("KIDS_DEFAULT_EXPLANATION_LANGUAGE", "").strip()
    if not default_lang or default_lang not in enabled:
        default_lang = enabled[0]
    return KidsTeacherSessionConfig(
        session_id=session_id,
        model=resolve_realtime_model(),
        profile=profile,
        enabled_languages=enabled,
        default_explanation_language=default_lang,
        max_session_seconds=max_seconds,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="robot_kids_teacher",
        description="Run the kids-teacher realtime session (V1 headless stub).",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Session id. Defaults to a freshly generated UUID.",
    )
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=None,
        help="Optional max session length in seconds.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point. Returns a unix-style exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    session_id = args.session_id or str(uuid.uuid4())

    try:
        # Lazy import keeps ``openai`` off the import graph of this module.
        from kids_teacher_backend import OpenAIRealtimeBackend
    except Exception as exc:  # pragma: no cover - backend module is local
        print(f"error: failed to import realtime backend: {exc}", file=sys.stderr)
        return 2

    def _backend_factory():
        try:
            return OpenAIRealtimeBackend()
        except Exception as exc:
            print(
                "error: OpenAI SDK is not available; install `openai` to run the "
                f"kids-teacher realtime session ({exc}).",
                file=sys.stderr,
            )
            raise SystemExit(2)

    # Extra guard: verify the SDK can actually be imported BEFORE we try to
    # start an event loop. This keeps --help and dry import paths clean.
    try:
        import openai  # type: ignore  # noqa: F401
    except ImportError:
        print(
            "error: the `openai` package is not installed. Install it to run the "
            "kids-teacher realtime session on the robot. V1 does not require "
            "`openai` for tests or the rest of the app.",
            file=sys.stderr,
        )
        return 2

    config = _build_config(session_id=session_id, max_seconds=args.max_seconds)
    deps = KidsTeacherFlowDeps(
        backend_factory=_backend_factory,
        hooks_factory=NullRuntimeHooks,
    )

    try:
        asyncio.run(run_kids_teacher_session(config=config, deps=deps))
    except KeyboardInterrupt:
        logger.info("[robot_kids_teacher] interrupted by user")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
