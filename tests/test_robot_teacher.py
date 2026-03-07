import numpy as np

from robot_teacher import (
    CLOUD_SERVER_URL,
    LOCAL_SERVER_URL,
    RobotController,
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
