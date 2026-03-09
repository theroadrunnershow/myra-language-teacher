import logging
import threading
from unittest.mock import MagicMock, call, patch

import numpy as np

import robot_teacher
from robot_teacher import (
    CLOUD_SERVER_URL,
    LOCAL_SERVER_URL,
    RobotController,
    _trigger_led_celebration,
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


def test_speak_loop_handles_motion_timeout(monkeypatch, caplog):
    mini = _FakeMini()
    controller = RobotController(mini)

    def fail_once(**_kwargs):
        controller._stop_event.set()
        raise TimeoutError("Task did not complete in time.")

    mini.goto_behavior = fail_once
    monkeypatch.setattr("robot_teacher.time.sleep", lambda *_args, **_kwargs: None)

    with caplog.at_level(logging.WARNING):
        controller._speak_loop()

    assert "Robot motion failed during speak nod up" in caplog.text
    assert len(mini.goto_calls) == 1


# ── LED celebration tests ─────────────────────────────────────────────────────

def test_trigger_led_celebration_posts_joy_then_off(monkeypatch):
    """_trigger_led_celebration POSTs joy animation then turns LEDs off."""
    monkeypatch.setattr("robot_teacher.time.sleep", lambda *_args, **_kwargs: None)
    mock_post = MagicMock()
    monkeypatch.setattr("robot_teacher.requests.post", mock_post)

    _trigger_led_celebration("http://rmeyes.local", duration=0.0)

    assert mock_post.call_count == 2
    animate_call, off_call = mock_post.call_args_list
    assert animate_call == call(
        "http://rmeyes.local/api/v1/led-animate",
        json={"animation": "joy", "brightness": 100, "speed": 30},
        timeout=2.0,
    )
    assert off_call == call(
        "http://rmeyes.local/api/v1/led",
        json={"led": -1, "on": False},
        timeout=2.0,
    )


def test_trigger_led_celebration_silently_skips_on_network_error(monkeypatch, caplog):
    """Connection errors are swallowed and logged at DEBUG — no exception raised."""
    monkeypatch.setattr("robot_teacher.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "robot_teacher.requests.post",
        MagicMock(side_effect=ConnectionError("no route to host")),
    )

    with caplog.at_level(logging.DEBUG):
        _trigger_led_celebration("http://rmeyes.local", duration=0.0)  # must not raise

    assert "LED celebration skipped" in caplog.text


def test_celebrate_spawns_led_thread_when_enabled(monkeypatch):
    """celebrate() starts an LED thread when REACHY_EYES_ENABLED is True."""
    mini = _FakeMini()
    controller = RobotController(mini)
    monkeypatch.setattr("robot_teacher.REACHY_EYES_ENABLED", True)
    monkeypatch.setattr("robot_teacher.REACHY_EYES_URL", "http://rmeyes.local")

    spawned_targets = []
    original_thread = threading.Thread

    def capture_thread(*args, target=None, **kwargs):
        spawned_targets.append(target)
        t = original_thread(*args, target=target, **kwargs)
        return t

    monkeypatch.setattr("robot_teacher.threading.Thread", capture_thread)
    controller.celebrate()

    assert _trigger_led_celebration in spawned_targets


def test_celebrate_skips_led_thread_when_disabled(monkeypatch):
    """celebrate() does NOT start an LED thread when REACHY_EYES_ENABLED is False."""
    mini = _FakeMini()
    controller = RobotController(mini)
    monkeypatch.setattr("robot_teacher.REACHY_EYES_ENABLED", False)

    spawned_targets = []
    original_thread = threading.Thread

    def capture_thread(*args, target=None, **kwargs):
        spawned_targets.append(target)
        t = original_thread(*args, target=target, **kwargs)
        return t

    monkeypatch.setattr("robot_teacher.threading.Thread", capture_thread)
    controller.celebrate()

    assert _trigger_led_celebration not in spawned_targets
