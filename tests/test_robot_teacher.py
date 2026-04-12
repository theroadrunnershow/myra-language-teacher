import logging

import numpy as np

import robot_teacher
from robot_teacher import (
    CLOUD_SERVER_URL,
    LOCAL_SERVER_URL,
    RobotController,
    REPLAY_WORD_BATCH,
    _interpret_play_again_transcript,
    _prompt_play_again,
    configure_server_url,
    resolve_server_url,
    should_start_local_server,
)


class _FakeMedia:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.pushed_samples: list[np.ndarray] = []

    def get_output_audio_samplerate(self) -> int:
        return self.sample_rate

    def push_audio_sample(self, samples: np.ndarray) -> None:
        self.pushed_samples.append(np.array(samples, copy=True))


class _FakeMini:
    def __init__(self, sample_rate: int = 16000):
        self.media = _FakeMedia(sample_rate=sample_rate)
        self.goto_calls: list[dict] = []
        self.goto_behavior = None

    def goto_target(self, **kwargs):
        self.goto_calls.append(kwargs)
        if self.goto_behavior is not None:
            return self.goto_behavior(**kwargs)
        return None


class _FakeSessionMedia:
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.start_playing_calls = 0
        self.start_recording_calls = 0
        self.stop_recording_calls = 0
        self.stop_playing_calls = 0

    def get_input_audio_samplerate(self) -> int:
        return self.sample_rate

    def start_playing(self) -> None:
        self.start_playing_calls += 1

    def start_recording(self) -> None:
        self.start_recording_calls += 1

    def stop_recording(self) -> None:
        self.stop_recording_calls += 1

    def stop_playing(self) -> None:
        self.stop_playing_calls += 1


class _FakeReachySession:
    def __init__(self, *_args, **_kwargs):
        self.media = _FakeSessionMedia()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeSessionRobot:
    def __init__(self, mini):
        self.mini = mini
        self.output_sample_rate = 16000
        self.idle_calls = 0
        self.celebrate_calls = 0
        self.play_audio_calls = 0

    def idle(self):
        self.idle_calls += 1

    def celebrate(self):
        self.celebrate_calls += 1

    def play_audio(self, *_args, **_kwargs):
        self.play_audio_calls += 1


def test_play_audio_primes_speaker_once(monkeypatch):
    mini = _FakeMini(sample_rate=8000)
    controller = RobotController(mini)
    audio = np.ones((400, 1), dtype=np.float32) * 0.25

    monkeypatch.setattr("robot_teacher.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(controller, "speak", lambda: None)
    monkeypatch.setattr(controller, "_stop_background", lambda: None)

    controller.play_audio(audio)
    controller.play_audio(audio)

    assert len(mini.media.pushed_samples) == 3

    priming_frame = mini.media.pushed_samples[0]
    assert priming_frame.shape == (2000, 1)
    assert np.allclose(priming_frame, 0.0)

    assert np.allclose(mini.media.pushed_samples[1], audio)
    assert np.allclose(mini.media.pushed_samples[2], audio)


def test_prime_speaker_is_idempotent(monkeypatch):
    mini = _FakeMini()
    controller = RobotController(mini)

    monkeypatch.setattr("robot_teacher.time.sleep", lambda *_args, **_kwargs: None)

    controller.prime_speaker()
    controller.prime_speaker()

    assert len(mini.media.pushed_samples) == 1


def test_resolve_server_url_supports_cloud_and_local():
    assert resolve_server_url("cloud") == CLOUD_SERVER_URL
    assert resolve_server_url("reachy_local") == LOCAL_SERVER_URL


def test_configure_server_url_returns_local_url():
    assert configure_server_url("reachy_local") == LOCAL_SERVER_URL


def test_should_start_local_server_only_in_reachy_local_mode():
    assert should_start_local_server("reachy_local", no_server=False) is True
    assert should_start_local_server("reachy_local", no_server=True) is False
    assert should_start_local_server("cloud", no_server=False) is False


def test_resolve_server_url_with_custom_url():
    """--server-url overrides both cloud and reachy_local modes."""
    custom = "http://192.168.1.50:8765"
    assert resolve_server_url("cloud", custom) == custom
    assert resolve_server_url("reachy_local", custom) == custom


def test_should_start_local_server_false_when_server_url_provided():
    """No subprocess spawned when a custom --server-url is given."""
    assert should_start_local_server(
        "reachy_local", no_server=False, server_url="http://192.168.1.50:8765"
    ) is False


def test_listen_handles_motion_errors(caplog):
    mini = _FakeMini()
    controller = RobotController(mini)

    def fail_motion(**_kwargs):
        raise Exception("Motor communication error! Check connections and power supply.")

    mini.goto_behavior = fail_motion

    with caplog.at_level(logging.WARNING):
        controller.listen()

    assert "Robot motion failed during listen pose" in caplog.text
    assert len(mini.goto_calls) == 1


def test_listen_still_runs_after_background_stop():
    mini = _FakeMini()
    controller = RobotController(mini)

    controller._stop_event.set()
    controller.listen()

    assert len(mini.goto_calls) == 1


def test_speak_loop_handles_motion_timeout(monkeypatch, caplog):
    mini = _FakeMini()
    controller = RobotController(mini)
    controller._bg_generation = 1

    def fail_once(**_kwargs):
        controller._stop_event.set()
        raise TimeoutError("Task did not complete in time.")

    mini.goto_behavior = fail_once
    monkeypatch.setattr("robot_teacher.time.sleep", lambda *_args, **_kwargs: None)

    with caplog.at_level(logging.WARNING):
        controller._speak_loop(token=1)

    assert "Robot motion failed during speak nod up" in caplog.text
    assert len(mini.goto_calls) == 1


def test_goto_target_safe_retries_timeout_once(caplog):
    mini = _FakeMini()
    controller = RobotController(mini)
    attempts = {"count": 0}

    def fail_once_then_succeed(**_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("Task did not complete in time.")

    mini.goto_behavior = fail_once_then_succeed

    with caplog.at_level(logging.INFO):
        ok = controller._goto_target_safe(
            "celebrate bob down",
            head={"z": -5},
            antennas=[-0.8, 0.8],
            duration=0.3,
        )

    assert ok is True
    assert len(mini.goto_calls) == 2
    assert mini.goto_calls[0]["duration"] == 0.3
    assert mini.goto_calls[1]["duration"] == 0.6
    assert "retrying once with 0.60s duration" in caplog.text
    assert "Robot motion failed during celebrate bob down" not in caplog.text


def test_idle_loop_stops_when_generation_changes(monkeypatch):
    mini = _FakeMini()
    controller = RobotController(mini)
    controller._bg_generation = 1
    contexts: list[tuple[str, int | None]] = []

    def fake_motion(context, preserve_timing=False, token=None, **_kwargs):
        contexts.append((context, token))
        controller._bg_generation += 1
        return True

    monkeypatch.setattr(controller, "_goto_target_safe", fake_motion)

    controller._idle_loop(token=1)

    assert contexts == [("idle sway left", 1)]


def test_prompt_play_again_accepts_yes(monkeypatch):
    spoken: list[tuple[str, str]] = []
    mini = _FakeReachySession()
    robot = _FakeSessionRobot(mini=None)

    monkeypatch.setattr(robot_teacher.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(
        "robot_teacher._say",
        lambda _robot, text, language="english": spoken.append((text, language)),
    )

    answer = _prompt_play_again(
        mini,
        robot,
        "Myra",
        mini.media.get_input_audio_samplerate(),
        recognize_func=lambda _mini, _mic_rate, _duration: "YES",
        duration=3.0,
    )

    assert answer is True
    assert robot.idle_calls == 1
    assert "Play again" in spoken[0][0]


def test_prompt_play_again_treats_no_response_as_no(monkeypatch, caplog):
    spoken: list[str] = []
    mini = _FakeReachySession()
    robot = _FakeSessionRobot(mini=None)

    monkeypatch.setattr(robot_teacher.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(
        "robot_teacher._say",
        lambda _robot, text, language="english": spoken.append(text),
    )

    with caplog.at_level(logging.INFO):
        answer = _prompt_play_again(
            mini,
            robot,
            "Myra",
            mini.media.get_input_audio_samplerate(),
            recognize_func=lambda _mini, _mic_rate, _duration: "",
            duration=3.0,
        )

    assert answer is False
    assert robot.idle_calls == 1
    assert "No play-again response received" in caplog.text
    assert spoken


def test_interpret_play_again_transcript_handles_yes_and_no_phrases():
    assert _interpret_play_again_transcript("yes please") is True
    assert _interpret_play_again_transcript("yeah again") is True
    assert _interpret_play_again_transcript("no thanks") is False
    assert _interpret_play_again_transcript("all done stop") is False
    assert _interpret_play_again_transcript("") is None


def test_run_lesson_session_adds_five_words_on_yes(monkeypatch):
    lesson_calls: list[int] = []
    spoken: list[str] = []
    responses = iter([True, False])

    monkeypatch.setattr("robot_teacher._ROBOT_SDK_AVAILABLE", True)
    monkeypatch.setattr(robot_teacher, "ReachyMini", _FakeReachySession, raising=False)
    monkeypatch.setattr("robot_teacher.RobotController", _FakeSessionRobot)
    monkeypatch.setattr("robot_teacher.api_get_dino_voice", lambda _text: b"")
    monkeypatch.setattr("robot_teacher.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(robot_teacher.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(
        "robot_teacher._say",
        lambda _robot, text, language="english": spoken.append(text),
    )

    def fake_run_lesson_word(*_args, **_kwargs):
        lesson_calls.append(len(lesson_calls) + 1)
        return "correct"

    monkeypatch.setattr("robot_teacher.run_lesson_word", fake_run_lesson_word)

    robot_teacher.run_lesson_session(
        languages=["telugu"],
        categories=["animals"],
        num_words=2,
        threshold=50,
        max_attempts=3,
        child_name="Myra",
        play_again_prompt_func=lambda *_args, **_kwargs: next(responses),
    )

    assert len(lesson_calls) == 2 + REPLAY_WORD_BATCH
    assert f"Let's learn {REPLAY_WORD_BATCH} more words" in spoken[0]
    assert "We learned 7 words today!" in spoken[-1]


def test_run_lesson_session_keeps_looping_while_play_again_is_yes(monkeypatch):
    lesson_calls: list[int] = []
    responses = iter([True, True, False])

    monkeypatch.setattr("robot_teacher._ROBOT_SDK_AVAILABLE", True)
    monkeypatch.setattr(robot_teacher, "ReachyMini", _FakeReachySession, raising=False)
    monkeypatch.setattr("robot_teacher.RobotController", _FakeSessionRobot)
    monkeypatch.setattr("robot_teacher.api_get_dino_voice", lambda _text: b"")
    monkeypatch.setattr("robot_teacher.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(robot_teacher.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr("robot_teacher._say", lambda *_args, **_kwargs: None)

    def fake_run_lesson_word(*_args, **_kwargs):
        lesson_calls.append(len(lesson_calls) + 1)
        return "correct"

    monkeypatch.setattr("robot_teacher.run_lesson_word", fake_run_lesson_word)

    robot_teacher.run_lesson_session(
        languages=["telugu"],
        categories=["animals"],
        num_words=1,
        threshold=50,
        max_attempts=3,
        child_name="Myra",
        play_again_prompt_func=lambda *_args, **_kwargs: next(responses),
    )

    assert len(lesson_calls) == 1 + REPLAY_WORD_BATCH + REPLAY_WORD_BATCH
