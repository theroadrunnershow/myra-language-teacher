"""Tests for src/kids_teacher_camera.py.

Covers the hardware-agnostic `CameraWorker` lifecycle plus the
`encode_bgr_frame_as_jpeg` round-trip. PyAV (`av`) is installed in the venv,
so the JPEG round-trip exercises the real codec.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from kids_teacher_camera import CameraWorker, encode_bgr_frame_as_jpeg


def _wait_for(predicate, timeout: float = 2.0, poll: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return False


def test_camera_worker_returns_copy_of_latest_frame() -> None:
    fixed_frame = np.zeros((4, 4, 3), dtype=np.uint8)
    fixed_frame[0, 0] = [10, 20, 30]

    mini = MagicMock()
    mini.media.get_frame.return_value = fixed_frame

    worker = CameraWorker(mini)
    worker.start()
    try:
        assert _wait_for(lambda: worker.get_latest_frame() is not None)
        first = worker.get_latest_frame()
        assert first is not None
        # Mutating the returned array must not corrupt the worker's buffer.
        first[0, 0] = [99, 99, 99]

        second = worker.get_latest_frame()
        assert second is not None
        assert second[0, 0].tolist() == [10, 20, 30]
        # And the original source frame is also untouched.
        assert fixed_frame[0, 0].tolist() == [10, 20, 30]
    finally:
        worker.stop()


def test_encode_bgr_frame_as_jpeg_round_trip() -> None:
    # Small BGR frame - 8x8 with a recognizable colored pixel.
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    frame[0, 0] = [200, 100, 50]  # BGR

    encoded = encode_bgr_frame_as_jpeg(frame)

    assert isinstance(encoded, bytes)
    assert len(encoded) > 0
    assert encoded.startswith(b"\xff\xd8")  # JPEG SOI
    assert encoded.endswith(b"\xff\xd9")  # JPEG EOI


def test_camera_worker_keeps_polling_after_get_frame_error() -> None:
    success_frame = np.full((2, 2, 3), 7, dtype=np.uint8)

    call_count = {"n": 0}

    def flaky_get_frame():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient SDK failure")
        return success_frame

    mini = MagicMock()
    mini.media.get_frame.side_effect = flaky_get_frame

    worker = CameraWorker(mini)
    worker.start()
    try:
        assert _wait_for(lambda: worker.get_latest_frame() is not None)
        latest = worker.get_latest_frame()
        assert latest is not None
        assert np.array_equal(latest, success_frame)
        # Worker thread should still be alive after the error.
        assert worker._thread is not None and worker._thread.is_alive()
    finally:
        worker.stop()


def test_camera_worker_start_is_idempotent() -> None:
    mini = MagicMock()
    mini.media.get_frame.return_value = np.zeros((2, 2, 3), dtype=np.uint8)

    worker = CameraWorker(mini)
    worker.start()
    try:
        first_thread = worker._thread
        assert first_thread is not None
        worker.start()  # second call must not spawn a new thread
        assert worker._thread is first_thread
        # Sanity: only one CameraWorker thread is alive in this process.
        camera_threads = [
            t for t in threading.enumerate() if t.name == "CameraWorker"
        ]
        assert len(camera_threads) == 1
    finally:
        worker.stop()


def test_camera_worker_stop_idempotent_and_safe_before_start() -> None:
    mini = MagicMock()
    worker = CameraWorker(mini)

    # stop() before start() must be a no-op (no exception, no thread join).
    worker.stop()
    assert worker._thread is None

    # And calling stop() again is still safe.
    worker.stop()
    assert worker._thread is None
