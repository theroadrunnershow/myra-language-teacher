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
import time
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

# Face-rec session-start sweep: 5 frames over ~1 s, name confirmed at ≥2/5
# hits (FR-KID-15). Tunables here are deliberate constants — no env knobs
# requested for this chunk.
_FACE_SWEEP_FRAMES = 5
_FACE_SWEEP_INTERVAL_SEC = 0.2
_FACE_SWEEP_THRESHOLD = 2

# On-demand recheck loop: poll bboxes every N seconds, throttle new-arrival
# announcements to one per ~5 s (FR-KID-16). Recheck interval is env-driven
# per the design doc; throttle is a fixed constant.
_FACE_RECHECK_ENV_VAR = "KIDS_TEACHER_FACE_RECHECK_SEC"
_FACE_RECHECK_DEFAULT_SEC = 10.0
_FACE_ARRIVAL_THROTTLE_SEC = 5.0
_FACE_UNKNOWN_ARRIVAL_NOTE = (
    "Someone new is here. If a grown-up tells you who, you can remember them."
)


def _target_mic_sample_rate_for(provider: str) -> int:
    if provider == "gemini":
        return _GEMINI_TARGET_MIC_SAMPLE_RATE
    return _OPENAI_TARGET_MIC_SAMPLE_RATE


def _build_config(
    session_id: str,
    max_seconds: Optional[int],
    present_names: Optional[list[str]] = None,
) -> KidsTeacherSessionConfig:
    profile = load_profile(present_names=present_names)
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
    session_id: str,
    max_seconds: Optional[int],
    robot_controller: Any,
    mic_reader: Callable[[], Optional[bytes]],
    provider: str,
    camera_worker: Any,
) -> None:
    from kids_teacher_flow import build_robot_hooks

    # Run the session-start face sweep BEFORE building the config so the
    # present-names note is available to ``load_profile`` (FR-KID-15 /
    # FR-KID-22). When face-rec or the camera is unavailable, the sweep
    # returns ``[]`` and ``load_profile`` omits the section (FR-KID-18 /
    # FR-KID-25).
    if camera_worker is not None and provider == "gemini":
        present_names = await run_session_start_face_sweep(camera_worker)
    else:
        present_names = []
    config = _build_config(
        session_id=session_id,
        max_seconds=max_seconds,
        present_names=present_names,
    )

    hooks = build_robot_hooks(robot_controller)
    backend_factory = _build_backend_factory(provider, camera_worker)
    video_pump_factory = (
        _make_video_pump_factory(camera_worker)
        if camera_worker is not None
        else None
    )
    gaze_loop_factory = _maybe_make_gaze_loop_factory(camera_worker, provider)
    face_rec_loop_factory = _maybe_build_face_rec_loop_factory(
        camera_worker=camera_worker,
        provider=provider,
        initial_names=present_names,
    )
    deps = KidsTeacherFlowDeps(
        backend_factory=backend_factory,
        hooks_factory=lambda: hooks,
        mic_pump_factory=_make_mic_pump_factory(mic_reader),
        video_pump_factory=video_pump_factory,
        gaze_loop_factory=gaze_loop_factory,
        face_rec_loop_factory=face_rec_loop_factory,
    )
    await run_kids_teacher_session(config=config, deps=deps)


def _maybe_build_face_rec_loop_factory(
    *,
    camera_worker: Any,
    provider: str,
    initial_names: list[str],
) -> Optional[Callable[[Any, asyncio.Event], Awaitable[None]]]:
    """Wrap :func:`_make_face_rec_loop_factory` with the provider gate.

    ``provider=openai`` (FR-KID-23) or missing camera worker → no factory.
    Missing ``face_recognition`` → no factory (one warning logged at module
    import time by :mod:`face_service`); the session still runs.
    """
    if provider != "gemini":
        logger.info("[robot_kids_teacher] face-rec disabled: provider=openai")
        return None
    if camera_worker is None:
        return None
    import face_service

    if not face_service.HAS_FACE_REC:
        logger.warning(
            "[robot_kids_teacher] face-rec disabled: face_recognition unavailable"
        )
        return None
    return _make_face_rec_loop_factory(camera_worker, list(initial_names))


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


_GAZE_FOLLOW_ENABLED_ENV_VAR = "KIDS_TEACHER_GAZE_FOLLOW_ENABLED"


def _gaze_follow_enabled() -> bool:
    """Read ``KIDS_TEACHER_GAZE_FOLLOW_ENABLED`` (default true)."""
    raw = os.environ.get(_GAZE_FOLLOW_ENABLED_ENV_VAR, "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _maybe_make_gaze_loop_factory(
    camera_worker: Any, provider: str
) -> Optional[Callable[[Any, asyncio.Event], Awaitable[None]]]:
    """Return a gaze-loop factory for ``provider=gemini`` only.

    Provider gate (FR-KID-30): no FaceTracker is constructed when
    ``provider=openai`` — the loop relies on the same camera worker that
    Chunk B's video pump uses, and that worker is itself off on OpenAI.
    Returns ``None`` (with a single info log) when:

    - ``provider != "gemini"`` — no gaze.
    - ``camera_worker is None`` — gemini's camera probe failed (NFR-4)
      or never got started; gaze has no input.
    - ``KIDS_TEACHER_GAZE_FOLLOW_ENABLED`` is set to a falsey string —
      operator kill-switch.

    Also logs a single warning when ``face_service.HAS_FACE_REC`` is
    False; the FaceTracker still constructs but every tick will publish
    ``None`` because :func:`face_service.detect_face_bboxes` short-circuits
    to ``[]`` (FR-KID-24 / NFR-7).
    """
    if provider != "gemini":
        logger.info("[robot_kids_teacher] gaze disabled: provider=openai")
        return None
    if camera_worker is None:
        logger.info("[robot_kids_teacher] gaze disabled: camera worker absent")
        return None
    if not _gaze_follow_enabled():
        logger.info(
            "[robot_kids_teacher] gaze disabled: %s=false",
            _GAZE_FOLLOW_ENABLED_ENV_VAR,
        )
        return None

    import face_service

    if not face_service.HAS_FACE_REC:
        logger.warning(
            "[robot_kids_teacher] gaze loop running without face_recognition; "
            "no targets will be published until dlib is installed"
        )

    return _make_gaze_loop_factory(camera_worker)


def _make_gaze_loop_factory(
    camera_worker: Any,
) -> Callable[[Any, asyncio.Event], Awaitable[None]]:
    """Return a coroutine factory that runs the FaceTracker loop.

    A default debug-level logging subscriber is attached so the
    ``gaze_target`` channel is observable even before the motion director
    ships. The motion director will register its own subscriber through
    ``FaceTracker.subscribe`` when it lands; this factory does not import
    or depend on it.
    """
    from face_tracker import FaceTracker

    child_name = os.environ.get("KIDS_TEACHER_CHILD_NAME", "").strip() or None

    async def _loop(handler: Any, stop_event: asyncio.Event) -> None:
        tracker = FaceTracker(camera_worker, child_name=child_name)

        def _log_target(target: Optional[tuple[float, float]]) -> None:
            if target is None:
                logger.debug("[face_tracker] gaze_target=None")
            else:
                logger.debug(
                    "[face_tracker] gaze_target=(%.3f, %.3f)", target[0], target[1]
                )

        tracker.subscribe(_log_target)
        try:
            await tracker.run(stop_event)
        finally:
            await tracker.stop()

    return _loop


def _resolve_face_recheck_interval() -> float:
    raw = os.environ.get(_FACE_RECHECK_ENV_VAR, "").strip()
    if not raw:
        return _FACE_RECHECK_DEFAULT_SEC
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "[robot_kids_teacher] %s=%r not a float; defaulting to %.1fs",
            _FACE_RECHECK_ENV_VAR,
            raw,
            _FACE_RECHECK_DEFAULT_SEC,
        )
        return _FACE_RECHECK_DEFAULT_SEC
    if value <= 0:
        logger.warning(
            "[robot_kids_teacher] %s=%s must be positive; defaulting to %.1fs",
            _FACE_RECHECK_ENV_VAR,
            value,
            _FACE_RECHECK_DEFAULT_SEC,
        )
        return _FACE_RECHECK_DEFAULT_SEC
    return value


async def run_session_start_face_sweep(camera_worker: Any) -> list[str]:
    """Capture ~5 frames over ~1 s and return names seen ≥2 times (FR-KID-15).

    Uses :func:`face_service.identify_in_frame` per frame, keeps a tally,
    and returns the deduped list of names whose tally hits the
    :data:`_FACE_SWEEP_THRESHOLD`. Returns ``[]`` when face-rec is
    unavailable or the camera has no frames yet — the caller is expected
    to treat ``[]`` as "no present-names note" (FR-KID-18 / FR-KID-25).
    """
    import face_service

    if not face_service.HAS_FACE_REC or camera_worker is None:
        return []
    tally: dict[str, int] = {}
    for _ in range(_FACE_SWEEP_FRAMES):
        frame = camera_worker.get_latest_frame()
        if frame is not None:
            try:
                names = face_service.identify_in_frame(frame)
            except Exception:
                logger.debug(
                    "[robot_kids_teacher] face sweep tick raised", exc_info=True
                )
                names = []
            for name in names:
                tally[name] = tally.get(name, 0) + 1
        try:
            await asyncio.sleep(_FACE_SWEEP_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise
    confirmed = sorted(
        name for name, count in tally.items() if count >= _FACE_SWEEP_THRESHOLD
    )
    if confirmed:
        logger.info(
            "[robot_kids_teacher] session-start sweep saw: %s", ", ".join(confirmed)
        )
    return confirmed


def _make_face_rec_loop_factory(
    camera_worker: Any,
    initial_names: list[str],
    *,
    interval_sec: Optional[float] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Callable[[Any, asyncio.Event], Awaitable[None]]:
    """Return a coroutine factory that polls bboxes and announces arrivals.

    Each tick (default every 10 s):
      1. ``face_service.detect_face_bboxes(latest_frame)`` — cheap HOG poll.
      2. If the count grew vs. the previous tick, run a single-frame
         :func:`face_service.identify_in_frame` pass.
      3. Diff the recognized name set against the running known set; for
         each new name push ``"<name> just joined."`` via
         ``handler.push_text``. If the count grew but no new name was
         identified, push the unknown-arrival prompt instead.
      4. Throttle to one announcement per ``_FACE_ARRIVAL_THROTTLE_SEC``.
    """
    import face_service

    period = (
        interval_sec
        if interval_sec is not None
        else _resolve_face_recheck_interval()
    )

    async def _push(handler: Any, text: str) -> None:
        try:
            await handler.push_text(text)
        except Exception:
            logger.debug("[robot_kids_teacher] push_text failed", exc_info=True)

    async def _announce(handler: Any, frame: Any, known_names: set[str]) -> None:
        try:
            seen = face_service.identify_in_frame(frame)
        except Exception:
            logger.debug(
                "[robot_kids_teacher] identify_in_frame raised", exc_info=True
            )
            seen = []
        new_names = [name for name in seen if name not in known_names]
        if new_names:
            for name in new_names:
                known_names.add(name)
                logger.info(
                    "[robot_kids_teacher] face-rec announce arrival: %s", name
                )
                await _push(handler, f"{name} just joined.")
        else:
            logger.info(
                "[robot_kids_teacher] face-rec announce unknown arrival"
            )
            await _push(handler, _FACE_UNKNOWN_ARRIVAL_NOTE)

    async def _loop(handler: Any, stop_event: asyncio.Event) -> None:
        if not face_service.HAS_FACE_REC:
            return
        prev_bbox_count = 0
        known_names: set[str] = set(initial_names)
        last_announcement_ts: Optional[float] = None
        while not stop_event.is_set():
            try:
                frame = camera_worker.get_latest_frame()
                if frame is not None:
                    bboxes = face_service.detect_face_bboxes(frame)
                    if len(bboxes) > prev_bbox_count:
                        # Throttle: skip if a recent announcement is still
                        # within the cool-down window. ``prev_bbox_count``
                        # is updated below regardless so we don't fire on
                        # the next tick for the same arrival.
                        now = monotonic()
                        within_cooldown = (
                            last_announcement_ts is not None
                            and (now - last_announcement_ts)
                            < _FACE_ARRIVAL_THROTTLE_SEC
                        )
                        if not within_cooldown:
                            await _announce(handler, frame, known_names)
                            last_announcement_ts = now
                    prev_bbox_count = len(bboxes)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(
                    "[robot_kids_teacher] face-rec tick raised", exc_info=True
                )
            try:
                await asyncio.sleep(period)
            except asyncio.CancelledError:
                raise

    return _loop


def _build_backend_factory(
    provider: str, camera_worker: Any = None
) -> Callable[[], Any]:
    """Return a zero-arg factory for the active realtime backend.

    Isolated so tests can assert which provider was selected without
    running a real session. Lazy-imports the provider-specific module.
    The Gemini factory threads ``camera_worker.get_latest_frame`` through
    as ``face_frame_provider`` so the ``remember_face`` tool handler can
    grab the latest frame at tool-call time. ``camera_worker is None``
    leaves the provider unset and the tool returns a polite refusal.
    """
    if provider == "gemini":
        from kids_teacher_gemini_backend import GeminiRealtimeBackend

        face_frame_provider = (
            camera_worker.get_latest_frame if camera_worker is not None else None
        )
        return lambda: GeminiRealtimeBackend(
            face_frame_provider=face_frame_provider
        )
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
                        session_id,
                        args.max_seconds,
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
