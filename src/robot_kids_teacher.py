"""Robot entry point for the kids-teacher realtime session.

Opens a :class:`ReachyMini` media context, builds a :class:`RobotController`
plus the robot hook bridge, converts mic frames to PCM16 mono for the
active realtime backend, and feeds them into the backend via
``pump_microphone_to_backend``.

The active backend is chosen by the ``KIDS_TEACHER_REALTIME_PROVIDER``
env var (``openai`` | ``gemini``). Input sample rate and the required SDK
presence check both follow the provider selection.

Every robot + ML dependency is lazy-imported inside ``main()`` so the module
itself stays cheap to import (tests rely on this to verify neither the
``openai`` nor the ``google.genai`` SDK is pulled in transitively).
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
from kids_teacher_backend import resolve_realtime_model, resolve_realtime_provider
from kids_teacher_flow import KidsTeacherFlowDeps, run_kids_teacher_session
from kids_teacher_profile import load_profile
from kids_teacher_types import KidsTeacherSessionConfig

load_project_dotenv()

logger = logging.getLogger(__name__)

# Per-provider mic target sample rates. OpenAI Realtime expects PCM16 at
# 24 kHz; Gemini Live expects PCM16 at 16 kHz. Picked at session build
# time based on the resolved provider.
_OPENAI_TARGET_MIC_SAMPLE_RATE = 24000
_GEMINI_TARGET_MIC_SAMPLE_RATE = 16000


def _target_mic_sample_rate_for(provider: str) -> int:
    if provider == "gemini":
        return _GEMINI_TARGET_MIC_SAMPLE_RATE
    return _OPENAI_TARGET_MIC_SAMPLE_RATE


def _build_config(session_id: str, max_seconds: Optional[int]) -> KidsTeacherSessionConfig:
    profile = load_profile()
    enabled_raw = os.environ.get("KIDS_ENABLED_LANGUAGES", "english,telugu")
    enabled = tuple(p.strip() for p in enabled_raw.split(",") if p.strip()) or ("english",)
    default_lang = os.environ.get("KIDS_DEFAULT_EXPLANATION_LANGUAGE", "").strip()
    if not default_lang or default_lang not in enabled:
        default_lang = enabled[0]
    provider = resolve_realtime_provider()
    if provider == "gemini":
        from kids_teacher_gemini_backend import resolve_gemini_model

        model = resolve_gemini_model()
    else:
        model = resolve_realtime_model()
    return KidsTeacherSessionConfig(
        session_id=session_id,
        model=model,
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
    mini: Any, mic_rate: int, target_rate: int
) -> Callable[[], Optional[bytes]]:
    """Return a sync callable that pulls one PCM16 mono frame from the robot.

    ``mini.media.get_audio_sample()`` returns ``None`` when no frame is
    buffered yet; the pump treats ``None`` as "poll again" so we pass that
    through. Conversion errors are logged and also return ``None`` so a
    single bad frame never ends the session.

    ``target_rate`` is provider-dependent: 24 kHz for OpenAI Realtime,
    16 kHz for Gemini Live. Passed in rather than read from a module
    global so the mic reader stays a pure function of its inputs.
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
            mono = _resample_audio(mono, mic_rate, target_rate)
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
    provider: str,
    camera_worker: Any,
) -> None:
    from kids_teacher_flow import build_robot_hooks

    hooks = build_robot_hooks(robot_controller)
    backend_factory = _build_backend_factory(provider)
    video_pump_factory = (
        _make_video_pump_factory(camera_worker)
        if camera_worker is not None
        else None
    )
    deps = KidsTeacherFlowDeps(
        backend_factory=backend_factory,
        hooks_factory=lambda: hooks,
        mic_pump_factory=_make_mic_pump_factory(mic_reader),
        video_pump_factory=video_pump_factory,
    )
    await run_kids_teacher_session(config=config, deps=deps)


def _make_video_pump_factory(
    camera_worker: Any,
) -> Callable[[Any, asyncio.Event], Awaitable[None]]:
    """Return a coroutine factory that streams JPEG frames at ~1 fps.

    Lifted from Pollen's ``_video_sender_loop``: poll
    ``camera_worker.get_latest_frame()`` every 1 s, encode via
    :func:`encode_bgr_frame_as_jpeg`, and forward through
    ``handler.push_video(...)``. Each tick checks the stop event AND the
    handler's ``session_active`` so frames are dropped pre-connect and
    post-teardown. Encode/send failures are debug-logged and swallowed —
    never fatal (NFR-4 / design §1 "Error handling"). Frames are NEVER
    written to disk.
    """
    from kids_teacher_camera import encode_bgr_frame_as_jpeg

    async def _pump(handler: Any, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                if handler.session_active:
                    frame = camera_worker.get_latest_frame()
                    if frame is not None:
                        try:
                            jpeg = encode_bgr_frame_as_jpeg(frame)
                            await handler.push_video(jpeg)
                        except Exception:
                            logger.debug(
                                "[robot_kids_teacher] video send failed",
                                exc_info=True,
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(
                    "[robot_kids_teacher] video sender tick raised",
                    exc_info=True,
                )
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise

    return _pump


def _build_backend_factory(provider: str) -> Callable[[], Any]:
    """Return a zero-arg factory for the active realtime backend.

    Isolated so tests can assert which provider was selected without
    running a real session. Lazy-imports the provider-specific module.
    """
    if provider == "gemini":
        from kids_teacher_gemini_backend import GeminiRealtimeBackend

        return lambda: GeminiRealtimeBackend()
    from kids_teacher_backend import OpenAIRealtimeBackend

    return lambda: OpenAIRealtimeBackend()


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point. Returns a unix-style exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    session_id = args.session_id or str(uuid.uuid4())

    # Resolve provider FIRST so the SDK presence check targets the right
    # package. A bad provider env var should fail before we open any
    # event loop or robot media context.
    try:
        provider = resolve_realtime_provider()
    except Exception as exc:
        print(f"error: invalid {exc}", file=sys.stderr)
        return 2

    # Verify SDKs are present BEFORE opening an event loop or the robot
    # media context. Keeps --help and dry-run paths free of heavy imports.
    if provider == "gemini":
        try:
            import google.genai  # type: ignore  # noqa: F401
        except ImportError:
            print(
                "error: the `google-genai` package is not installed. Install it to run "
                "the kids-teacher realtime session with provider=gemini.",
                file=sys.stderr,
            )
            return 2
    else:
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
    target_rate = _target_mic_sample_rate_for(provider)

    try:
        with ReachyMini(media_backend="default") as mini:
            mic_rate = mini.media.get_input_audio_samplerate() or SAMPLE_RATE
            logger.info(
                "[robot_kids_teacher] provider=%s mic_rate=%dHz target_rate=%dHz",
                provider,
                mic_rate,
                target_rate,
            )
            mini.media.start_playing()
            mini.media.start_recording()
            # Camera worker is process-lifetime: started before Gemini
            # connects, stopped in the outer finally. The per-session
            # video task lives inside run_kids_teacher_session and is
            # cancelled there. Provider gate (FR-KID-1): no worker spawned
            # when provider=openai.
            camera_worker = _maybe_start_camera_worker(mini, provider)
            try:
                robot_controller = RobotController(mini)
                mic_reader = _build_mic_reader(mini, mic_rate, target_rate)
                asyncio.run(
                    _run_session_async(
                        config,
                        robot_controller,
                        mic_reader,
                        provider,
                        camera_worker,
                    )
                )
            finally:
                if camera_worker is not None:
                    try:
                        camera_worker.stop()
                    except Exception as exc:
                        logger.warning(
                            "[robot_kids_teacher] camera_worker.stop: %s",
                            exc,
                        )
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


def _maybe_start_camera_worker(mini: Any, provider: str) -> Any:
    """Start :class:`CameraWorker` for ``provider=gemini`` only.

    Returns ``None`` for ``provider=openai`` (FR-KID-1) or when the worker
    fails to start (NFR-4 — never fatal). On the gemini path we lazy-import
    the module so the OpenAI path doesn't pay the import cost.
    """
    if provider != "gemini":
        logger.info("[robot_kids_teacher] camera disabled: provider=openai")
        return None
    try:
        from kids_teacher_camera import CameraWorker

        worker = CameraWorker(mini)
        worker.start()
        return worker
    except Exception as exc:
        logger.warning(
            "[robot_kids_teacher] camera worker unavailable; "
            "running audio-only session: %s",
            exc,
        )
        return None


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
