"""Robot entry point for the kids-teacher realtime session.

Opens a :class:`ReachyMini` media context, builds a :class:`RobotController`
plus the robot hook bridge, converts mic frames to PCM16 mono for OpenAI
Realtime, and feeds them into the backend via ``pump_microphone_to_backend``.

Every robot + ML dependency is lazy-imported inside ``main()`` so the module
itself stays cheap to import (tests rely on this to verify the ``openai``
SDK is never pulled in transitively).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from typing import Any, Awaitable, Callable, Optional

from env_loader import load_project_dotenv
from kids_teacher_backend import resolve_realtime_model
from kids_teacher_flow import KidsTeacherFlowDeps, run_kids_teacher_session
from kids_teacher_profile import load_profile
from kids_teacher_types import KidsTeacherSessionConfig

load_project_dotenv()

logger = logging.getLogger(__name__)

# OpenAI Realtime expects mono PCM16 at 24 kHz on input. The robot mic is
# typically 16 kHz but we always resample to this rate before forwarding.
_TARGET_MIC_SAMPLE_RATE = 24000


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
        description="Run the kids-teacher realtime session on Reachy Mini.",
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


def _build_mic_reader(
    mini: Any, mic_rate: int
) -> Callable[[], Optional[bytes]]:
    """Return a sync callable that pulls one PCM16 mono frame from the robot.

    ``mini.media.get_audio_sample()`` returns ``None`` when no frame is
    buffered yet; the pump treats ``None`` as "poll again" so we pass that
    through. Conversion errors are logged and also return ``None`` so a
    single bad frame never ends the session.
    """
    # Lazy — numpy + scipy live in the robot requirement set only.
    import numpy as np
    from robot_teacher import (
        _extract_first_channel,
        _resample_audio,
        _to_float32_audio,
    )

    def _read() -> Optional[bytes]:
        try:
            sample = mini.media.get_audio_sample()
        except Exception as exc:
            logger.warning("[robot_kids_teacher] get_audio_sample raised: %s", exc)
            return None
        if sample is None:
            return None
        try:
            mono = _to_float32_audio(_extract_first_channel(sample))
            mono = _resample_audio(mono, mic_rate, _TARGET_MIC_SAMPLE_RATE)
            mono = np.clip(mono, -1.0, 1.0)
            pcm16 = np.round(mono * 32767).astype(np.int16)
        except Exception as exc:
            logger.warning(
                "[robot_kids_teacher] mic frame conversion failed: %s", exc
            )
            return None
        return pcm16.tobytes()

    return _read


def _make_mic_pump_factory(
    mic_reader: Callable[[], Optional[bytes]],
) -> Callable[[Any, asyncio.Event], Awaitable[None]]:
    from kids_teacher_robot_bridge import pump_microphone_to_backend

    async def _pump(handler: Any, stop_event: asyncio.Event) -> None:
        await pump_microphone_to_backend(
            handler, mic_source=mic_reader, stop_event=stop_event
        )

    return _pump


async def _run_session_async(
    config: KidsTeacherSessionConfig,
    robot_controller: Any,
    mic_reader: Callable[[], Optional[bytes]],
) -> None:
    from kids_teacher_backend import OpenAIRealtimeBackend
    from kids_teacher_flow import build_robot_hooks

    hooks = build_robot_hooks(robot_controller)
    deps = KidsTeacherFlowDeps(
        backend_factory=lambda: OpenAIRealtimeBackend(),
        hooks_factory=lambda: hooks,
        mic_pump_factory=_make_mic_pump_factory(mic_reader),
    )
    await run_kids_teacher_session(config=config, deps=deps)


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point. Returns a unix-style exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    session_id = args.session_id or str(uuid.uuid4())

    # Verify SDKs are present BEFORE opening an event loop or the robot
    # media context. Keeps --help and dry-run paths free of heavy imports.
    try:
        import openai  # type: ignore  # noqa: F401
    except ImportError:
        print(
            "error: the `openai` package is not installed. Install it to run the "
            "kids-teacher realtime session on the robot.",
            file=sys.stderr,
        )
        return 2

    try:
        from reachy_mini import ReachyMini  # type: ignore
    except ImportError:
        print(
            "error: the `reachy_mini` SDK is not installed. Install the robot "
            "requirements (requirements-robot.txt) and re-run on the Pi.",
            file=sys.stderr,
        )
        return 2

    # Import the local robot helpers only after SDK checks pass so the
    # module-level import graph stays light.
    from robot_teacher import RobotController, SAMPLE_RATE

    config = _build_config(session_id=session_id, max_seconds=args.max_seconds)

    try:
        with ReachyMini(media_backend="default") as mini:
            mic_rate = mini.media.get_input_audio_samplerate() or SAMPLE_RATE
            logger.info("[robot_kids_teacher] mic_rate=%dHz", mic_rate)
            mini.media.start_playing()
            mini.media.start_recording()
            try:
                robot_controller = RobotController(mini)
                mic_reader = _build_mic_reader(mini, mic_rate)
                asyncio.run(
                    _run_session_async(config, robot_controller, mic_reader)
                )
            finally:
                try:
                    mini.media.stop_recording()
                except Exception as exc:
                    logger.warning(
                        "[robot_kids_teacher] stop_recording: %s", exc
                    )
                try:
                    mini.media.stop_playing()
                except Exception as exc:
                    logger.warning(
                        "[robot_kids_teacher] stop_playing: %s", exc
                    )
    except KeyboardInterrupt:
        logger.info("[robot_kids_teacher] interrupted by user")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
