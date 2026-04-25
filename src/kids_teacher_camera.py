"""Hardware-agnostic camera worker and JPEG encoder for kids-teacher mode.

Ported from pollen-robotics/reachy_mini_conversation_app's `camera_worker.py`
and `camera_frame_encoding.py`. This module is intentionally decoupled from
Gemini, OpenAI, face-recognition, and the rest of the kids-teacher stack so
that multiple consumers (Gemini video sender, face-recognition, gaze-following)
can share a single `CameraWorker` instance.

The worker only knows about `mini.media.get_frame()` returning a BGR numpy
array. PyAV (`av`) is a transitive dependency of the `reachy_mini` SDK; if it
is unavailable at import time we log a warning and let
`encode_bgr_frame_as_jpeg` raise on call. Graceful session-level degradation
is handled by callers, not here.
"""

from __future__ import annotations

import logging
import threading
import time
from fractions import Fraction
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import av  # type: ignore
except Exception as exc:  # pragma: no cover - exercised via stubbed import
    av = None  # type: ignore[assignment]
    logger.warning("PyAV (av) unavailable; JPEG encoding disabled: %s", exc)


def encode_bgr_frame_as_jpeg(frame: np.ndarray) -> bytes:
    """Encode a BGR numpy frame as a JPEG byte string using PyAV's MJPEG codec.

    Lifted verbatim from Pollen's pattern: BGR -> RGB via reverse-stride slice,
    MJPEG codec at native resolution, yuvj444p pixel format, qscale=3.
    """
    if av is None:
        raise RuntimeError("PyAV (av) is not available; cannot encode JPEG")

    rgb = np.ascontiguousarray(frame[..., ::-1])
    codec = av.CodecContext.create("mjpeg", "w")
    codec.width = rgb.shape[1]
    codec.height = rgb.shape[0]
    codec.pix_fmt = "yuvj444p"
    codec.options = {"qscale": "3"}
    codec.time_base = Fraction(1, 1)

    av_frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
    packets = list(codec.encode(av_frame))
    packets.extend(codec.encode(None))  # flush

    return b"".join(bytes(p) for p in packets)


class CameraWorker:
    """Daemon-thread producer that polls `mini.media.get_frame()` at ~25 fps.

    Stores the latest BGR frame under a lock. Consumers call
    `get_latest_frame()` to receive a copy; the worker never hands out raw
    references. Errors from the SDK are logged and swallowed - the loop is
    never fatal.
    """

    def __init__(self, mini) -> None:
        self.mini = mini
        self.latest_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Spawn the polling thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.working_loop, name="CameraWorker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the loop to stop and join the thread. Safe before start()."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def working_loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self.mini.media.get_frame()
                with self.frame_lock:
                    self.latest_frame = frame
            except Exception as exc:
                logger.error("CameraWorker.get_frame failed: %s", exc)
                time.sleep(0.1)
            time.sleep(0.04)

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Return a copy of the latest frame, or None if none captured yet."""
        with self.frame_lock:
            if self.latest_frame is None:
                return None
            return self.latest_frame.copy()
