"""Tests for the Chunk B video pipeline.

Covers the five wiring points required by
``tasks/camera-object-recognition-design.md``:

* Gemini backend: ``send_video()`` uses kwarg ``video=`` with a Blob whose
  ``mime_type == "image/jpeg"``.
* OpenAI backend: ``send_video()`` is a defensive no-op (FR-KID-1).
* Realtime handler: ``push_video()`` drops frames pre-session and
  post-teardown.
* Robot lifecycle: ``provider=openai`` does not start the camera worker
  or schedule the video task; teardown cancels the video task cleanly.
* Retention regression: an end-to-end mocked Gemini session that streams
  video must NOT leave any image files in the review-store directory,
  even with ``KIDS_REVIEW_TRANSCRIPTS_ENABLED=true`` (FR-KID-8 / §2.4).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from kids_review_store import KidsReviewStore
from kids_teacher_backend import OpenAIRealtimeBackend
from kids_teacher_fakes import FakeRealtimeBackend
from kids_teacher_flow import KidsTeacherFlowDeps, run_kids_teacher_session
from kids_teacher_gemini_backend import (
    DEFAULT_GEMINI_MODEL,
    GeminiRealtimeBackend,
)
from kids_teacher_realtime import KidsTeacherRealtimeHandler
from kids_teacher_types import (
    KidsStatusEvent,
    KidsTeacherProfile,
    KidsTeacherSessionConfig,
    KidsTranscriptEvent,
)


# ---------------------------------------------------------------------------
# Fakes (mirror the patterns in test_kids_teacher_gemini_backend.py)
# ---------------------------------------------------------------------------


class _Record:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeTypes:
    Blob = _Record
    Content = _Record
    Part = _Record
    FunctionDeclaration = _Record
    FunctionResponse = _Record
    PrebuiltVoiceConfig = _Record
    VoiceConfig = _Record
    SpeechConfig = _Record
    Tool = _Record
    AudioTranscriptionConfig = _Record
    LiveConnectConfig = _Record


class _FakeSession:
    """Records every send_realtime_input call."""

    def __init__(self) -> None:
        self.send_calls: list[dict[str, Any]] = []
        self.tool_responses: list[Any] = []

    async def receive(self):  # pragma: no cover - never iterated in these tests
        if False:
            yield None

    async def send_realtime_input(self, **kwargs: Any) -> None:
        self.send_calls.append(kwargs)

    async def send_client_content(self, **kwargs: Any) -> None:  # pragma: no cover
        pass

    async def send_tool_response(self, *, function_responses: Any) -> None:  # pragma: no cover
        self.tool_responses.append(function_responses)


class _FakeConnectionCM:
    def __init__(self, session: Any) -> None:
        self._session = session
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> Any:
        self.entered = True
        return self._session

    async def __aexit__(self, *exc: Any) -> None:
        self.exited = True


class _FakeLive:
    def __init__(self, manager: _FakeConnectionCM) -> None:
        self._manager = manager
        self.connect_calls: list[dict] = []

    def connect(self, *, model: str, config: Any) -> _FakeConnectionCM:
        self.connect_calls.append({"model": model, "config": config})
        return self._manager


class _FakeAio:
    def __init__(self, live: _FakeLive) -> None:
        self.live = live


class _FakeClient:
    def __init__(self, manager: _FakeConnectionCM) -> None:
        self._live = _FakeLive(manager)
        self.aio = _FakeAio(self._live)


# ---------------------------------------------------------------------------
# Profile / config helpers
# ---------------------------------------------------------------------------


def _profile() -> KidsTeacherProfile:
    return KidsTeacherProfile(
        name="kids_teacher",
        instructions="Be a warm preschool teacher.",
        voice="alloy",
        allowed_tools=(),
    )


def _config(session_id: str = "video-test") -> KidsTeacherSessionConfig:
    return KidsTeacherSessionConfig(
        session_id=session_id,
        model="gpt-realtime-mini",
        profile=_profile(),
        enabled_languages=("english", "telugu"),
        default_explanation_language="english",
    )


class _NoOpHooks:
    def start_assistant_playback(self, audio_chunk: bytes) -> None:
        return None

    def stop_assistant_playback(self) -> None:
        return None

    def publish_transcript(self, event: KidsTranscriptEvent) -> None:
        return None

    def publish_status(self, event: KidsStatusEvent) -> None:
        return None

    def persist_artifact(
        self, event: KidsTranscriptEvent, audio: bytes | None = None
    ) -> None:
        return None


# ---------------------------------------------------------------------------
# 1. Gemini backend: send_video uses video= kwarg with image/jpeg Blob
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_backend_send_video_uses_video_kwarg() -> None:
    session = _FakeSession()
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})
    payload = b"\xff\xd8\xff\xe0fake-jpeg"
    await backend.send_video(payload)
    await backend.close()

    video_calls = [c for c in session.send_calls if "video" in c]
    assert len(video_calls) == 1, f"expected one video send, got {session.send_calls}"
    blob = video_calls[0]["video"]
    assert blob.mime_type == "image/jpeg"
    assert blob.data == payload


@pytest.mark.asyncio
async def test_gemini_backend_send_video_noop_when_session_unset() -> None:
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: object(),
        types_module=_FakeTypes,
    )
    # No connect() — _session is still None.
    await backend.send_video(b"\xff\xd8\xff\xe0fake-jpeg")  # must not raise


@pytest.mark.asyncio
async def test_gemini_backend_send_video_swallows_send_failure(caplog) -> None:
    session = _FakeSession()
    manager = _FakeConnectionCM(session)
    client = _FakeClient(manager)
    backend = GeminiRealtimeBackend(
        model=DEFAULT_GEMINI_MODEL,
        client_factory=lambda: client,
        types_module=_FakeTypes,
    )
    await backend.connect({"instructions": "hi", "voice": "alloy"})

    async def boom(**kwargs: Any) -> None:
        raise RuntimeError("network down")

    session.send_realtime_input = boom  # type: ignore[assignment]
    with caplog.at_level("DEBUG"):
        await backend.send_video(b"\xff\xd8\xff\xe0fake-jpeg")  # never raises
    await backend.close()


# ---------------------------------------------------------------------------
# 2. OpenAI backend: send_video is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_backend_send_video_is_noop(caplog) -> None:
    backend = OpenAIRealtimeBackend(client_factory=lambda: object())
    # Connection is not opened — calling send_video must not touch any
    # network method or raise.
    with caplog.at_level("INFO"):
        await backend.send_video(b"\xff\xd8\xff\xe0fake")
        await backend.send_video(b"\xff\xd8\xff\xe0fake")
    matching = [
        r for r in caplog.records if "camera disabled: provider=openai" in r.message
    ]
    # Logged exactly once, not per frame.
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# 3. Realtime handler: push_video drops frames when session is inactive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_push_video_ignored_when_session_inactive() -> None:
    backend = FakeRealtimeBackend(scripted_events=[])
    handler = KidsTeacherRealtimeHandler(
        config=_config(),
        backend=backend,
        hooks=_NoOpHooks(),
    )
    # Pre-start: session_active is False.
    assert handler.session_active is False
    await handler.push_video(b"\xff\xd8\xff\xe0pre-session")
    assert backend.video_chunks == []

    await handler.start()
    assert handler.session_active is True
    await handler.push_video(b"\xff\xd8\xff\xe0in-session")
    assert backend.video_chunks == [b"\xff\xd8\xff\xe0in-session"]

    await handler.stop()
    assert handler.session_active is False
    await handler.push_video(b"\xff\xd8\xff\xe0post-teardown")
    # No new chunks reached the backend.
    assert backend.video_chunks == [b"\xff\xd8\xff\xe0in-session"]


@pytest.mark.asyncio
async def test_handler_session_active_false_when_connect_fails() -> None:
    backend = FakeRealtimeBackend(
        scripted_events=[], connect_error=RuntimeError("connect failed")
    )
    handler = KidsTeacherRealtimeHandler(
        config=_config(),
        backend=backend,
        hooks=_NoOpHooks(),
    )
    await handler.start()
    assert handler.session_active is False
    await handler.push_video(b"\xff\xd8\xff\xe0fake")
    assert backend.video_chunks == []


# ---------------------------------------------------------------------------
# 4. Robot lifecycle: openai provider skips camera + video task
# ---------------------------------------------------------------------------


def test_robot_kids_teacher_skips_camera_on_openai(monkeypatch, caplog) -> None:
    """``provider=openai`` must never spawn a CameraWorker.

    We invoke the helper directly so we don't have to mock the entire
    Reachy SDK boot. The behaviour we care about is conditional on the
    provider string, not on ``mini``.
    """
    import robot_kids_teacher

    started: list[Any] = []

    class _ExplodingCameraWorker:
        def __init__(self, mini: Any) -> None:
            raise AssertionError(
                "CameraWorker must not be constructed when provider=openai"
            )

        def start(self) -> None:  # pragma: no cover - never reached
            started.append("start")

    # If the helper imports CameraWorker, fail loudly.
    import sys

    fake_module = type(sys)("kids_teacher_camera_test_stub")
    fake_module.CameraWorker = _ExplodingCameraWorker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kids_teacher_camera", fake_module)

    with caplog.at_level("INFO"):
        worker = robot_kids_teacher._maybe_start_camera_worker(
            mini=object(), provider="openai"
        )

    assert worker is None
    assert started == []
    assert any(
        "camera disabled: provider=openai" in r.message for r in caplog.records
    )


def test_robot_kids_teacher_starts_camera_on_gemini(monkeypatch) -> None:
    """``provider=gemini`` constructs and starts a CameraWorker."""
    import robot_kids_teacher

    started: list[str] = []

    class _FakeCameraWorker:
        def __init__(self, mini: Any) -> None:
            self.mini = mini

        def start(self) -> None:
            started.append("start")

        def stop(self) -> None:
            started.append("stop")

    import sys

    fake_module = type(sys)("kids_teacher_camera_test_stub")
    fake_module.CameraWorker = _FakeCameraWorker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kids_teacher_camera", fake_module)

    worker = robot_kids_teacher._maybe_start_camera_worker(
        mini=object(), provider="gemini"
    )
    assert worker is not None
    assert started == ["start"]


def test_robot_kids_teacher_camera_failure_runs_audio_only(monkeypatch, caplog) -> None:
    """Camera startup failure on gemini path must not be fatal (NFR-4)."""
    import robot_kids_teacher

    class _FailingCameraWorker:
        def __init__(self, mini: Any) -> None:
            raise RuntimeError("camera not present")

    import sys

    fake_module = type(sys)("kids_teacher_camera_test_stub")
    fake_module.CameraWorker = _FailingCameraWorker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kids_teacher_camera", fake_module)

    with caplog.at_level("WARNING"):
        worker = robot_kids_teacher._maybe_start_camera_worker(
            mini=object(), provider="gemini"
        )

    assert worker is None
    assert any("camera worker unavailable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 5. Video sender loop / video task lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_sender_loop_skips_when_session_is_none() -> None:
    """Pre-session and post-teardown ticks must not call send_video()."""
    import robot_kids_teacher

    class _StubWorker:
        def __init__(self) -> None:
            self.calls = 0

        def get_latest_frame(self):
            self.calls += 1
            # Return None — but even if we returned a frame, the
            # session_active guard should prevent any send.
            return None

    worker = _StubWorker()
    factory = robot_kids_teacher._make_video_pump_factory(worker)

    class _DeadHandler:
        session_active = False

        async def push_video(self, jpeg: bytes) -> None:  # pragma: no cover
            raise AssertionError("push_video must not be called when session is dead")

    stop_event = asyncio.Event()
    task = asyncio.create_task(factory(_DeadHandler(), stop_event))
    # Let the loop tick a couple of times.
    await asyncio.sleep(0.05)
    stop_event.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_video_task_cancelled_on_session_teardown(tmp_path) -> None:
    """The flow's video pump task must cancel cleanly when the session ends."""
    import numpy as np

    from kids_teacher_camera import CameraWorker
    import robot_kids_teacher

    class _StubMini:
        def __init__(self) -> None:
            self._frame = np.zeros((4, 4, 3), dtype=np.uint8)

        class _Media:
            def __init__(self, parent: Any) -> None:
                self._parent = parent

            def get_frame(self) -> Any:
                return self._parent._frame

        @property
        def media(self) -> Any:
            return self._Media(self)

    worker = CameraWorker(_StubMini())
    worker.start()
    try:
        backend = FakeRealtimeBackend(scripted_events=[])

        deps = KidsTeacherFlowDeps(
            backend_factory=lambda: backend,
            hooks_factory=lambda: _NoOpHooks(),
            video_pump_factory=robot_kids_teacher._make_video_pump_factory(worker),
        )

        stop_event = asyncio.Event()

        async def trigger_stop():
            await asyncio.sleep(0.05)
            stop_event.set()

        triggerer = asyncio.create_task(trigger_stop())
        # If the video task is not cancelled cleanly, this would hang or
        # raise; the test asserts it returns within a bounded time.
        await asyncio.wait_for(
            run_kids_teacher_session(
                config=_config("teardown-test"), deps=deps, stop_event=stop_event
            ),
            timeout=5.0,
        )
        await triggerer
    finally:
        worker.stop()


# ---------------------------------------------------------------------------
# 6. Retention regression: review store never holds frames
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_store_never_contains_frames(tmp_path, monkeypatch) -> None:
    """End-to-end mocked session that streams video must leave NO image files.

    Even with ``KIDS_REVIEW_TRANSCRIPTS_ENABLED=true``, ``KidsReviewStore``
    must contain only transcripts/JSON — never any binary image artifact.
    Regression guard for FR-KID-8 / §2.4.
    """
    import numpy as np

    from kids_teacher_camera import CameraWorker
    import robot_kids_teacher

    monkeypatch.setenv("KIDS_REVIEW_TRANSCRIPTS_ENABLED", "true")

    class _StubMini:
        def __init__(self) -> None:
            # Tiny RGB frame, BGR-laid-out for the encoder.
            self._frame = np.zeros((8, 8, 3), dtype=np.uint8)

        class _Media:
            def __init__(self, parent: Any) -> None:
                self._parent = parent

            def get_frame(self) -> Any:
                return self._parent._frame

        @property
        def media(self) -> Any:
            return self._Media(self)

    worker = CameraWorker(_StubMini())
    worker.start()
    try:
        scripted = [
            {
                "type": "input_transcript.final",
                "text": "hello",
                "language": "english",
            },
            {"type": "assistant_transcript.delta", "text": "hi "},
            {
                "type": "assistant_transcript.final",
                "text": "hi there",
                "language": "english",
            },
            {"type": "response.done"},
        ]
        backend = FakeRealtimeBackend(scripted_events=scripted)

        review = KidsReviewStore(
            transcripts_enabled=True,
            audio_enabled=False,
            retention_days=30,
            local_dir=str(tmp_path),
        )

        deps = KidsTeacherFlowDeps(
            backend_factory=lambda: backend,
            hooks_factory=lambda: _NoOpHooks(),
            review_store=review,
            video_pump_factory=robot_kids_teacher._make_video_pump_factory(worker),
        )

        async def end_soon():
            await asyncio.sleep(0)
            await backend.end_stream()

        ender = asyncio.create_task(end_soon())
        await asyncio.wait_for(
            run_kids_teacher_session(config=_config("retention"), deps=deps),
            timeout=5.0,
        )
        await ender
    finally:
        worker.stop()

    # Walk the review-store directory and assert no image files exist.
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
    found_images: list[str] = []
    for root, _dirs, files in os.walk(tmp_path):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in image_extensions:
                found_images.append(os.path.join(root, name))
    assert found_images == [], (
        f"frames must never reach disk; found: {found_images}"
    )
