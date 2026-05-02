"""Unit tests for :mod:`motion.wobbler`."""

from __future__ import annotations

import math
import struct

import pytest

from motion.types import NEUTRAL
from motion.wobbler import AudioWobbler


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def _pcm16(samples) -> bytes:
    """Pack a list of ints as little-endian PCM16."""
    return struct.pack(f"<{len(samples)}h", *samples)


def _silent_chunk(n: int = 480) -> bytes:
    return _pcm16([0] * n)


def _loud_chunk(n: int = 480, amplitude: int = 12000) -> bytes:
    """A loud-ish PCM16 sine chunk at ~440 Hz against 24 kHz."""
    return _pcm16([int(amplitude * math.sin(2 * math.pi * 440 * i / 24000)) for i in range(n)])


# ---------------------------------------------------------------------------
# Construction & defaults
# ---------------------------------------------------------------------------


def test_initial_offset_is_neutral():
    w = AudioWobbler(clock=_FakeClock())
    assert w.current_offset() == NEUTRAL


def test_silent_chunk_keeps_offset_neutral():
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    w.feed(_silent_chunk())
    clock.advance(0.05)
    assert w.current_offset() == NEUTRAL


def test_empty_feed_is_a_noop():
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    w.feed(b"")
    assert w.current_offset() == NEUTRAL


def test_odd_byte_chunk_does_not_crash():
    """If the backend ever sends a half sample we must not raise."""
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    # Single byte — under one PCM16 sample.
    w.feed(b"\x01")
    assert w.current_offset() == NEUTRAL
    # Odd-byte loud-ish — drop the trailing byte and keep going.
    w.feed(_loud_chunk() + b"\x7f")
    clock.advance(0.05)
    offset = w.current_offset()
    assert offset != NEUTRAL


# ---------------------------------------------------------------------------
# Envelope behaviour
# ---------------------------------------------------------------------------


def test_loud_audio_drives_offset_away_from_neutral():
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    # Several loud chunks to drive the envelope up.
    for _ in range(10):
        w.feed(_loud_chunk())
        clock.advance(0.05)
    # Sample over a few seconds — the slow oscillator needs time to swing
    # through a non-zero phase even with a fully-driven envelope.
    saw_motion = False
    for _ in range(200):
        if abs(w.current_offset().head_yaw) > 1e-4:
            saw_motion = True
            break
        clock.advance(0.05)
        w.feed(_loud_chunk())
    assert saw_motion


def test_envelope_decays_during_silence():
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    for _ in range(10):
        w.feed(_loud_chunk())
        clock.advance(0.05)
    # No more feeds — envelope should decay to ~0 within ~2s.
    clock.advance(3.0)
    assert w.current_offset() == NEUTRAL


def test_reset_zeroes_envelope_immediately():
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    for _ in range(10):
        w.feed(_loud_chunk())
        clock.advance(0.05)
    # Envelope is up.
    w.reset()
    assert w.current_offset() == NEUTRAL


# ---------------------------------------------------------------------------
# Channel coverage
# ---------------------------------------------------------------------------


def test_default_oscillator_drives_head_yaw():
    """Smoke: prove the head_yaw channel sees motion at some sample."""
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    for _ in range(20):
        w.feed(_loud_chunk())
        clock.advance(0.05)
    seen_head_yaw = False
    # Sample over a few seconds — the slow oscillator needs time to swing
    # through a non-zero phase even with a fully-driven envelope.
    for _ in range(200):
        if abs(w.current_offset().head_yaw) > 1e-6:
            seen_head_yaw = True
            break
        clock.advance(0.02)
        # Keep envelope up for the duration of the probe.
        w.feed(_loud_chunk())
    assert seen_head_yaw


def test_unmoved_channels_stay_zero():
    """Only ``head_yaw`` is driven by the default oscillator."""
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    for _ in range(20):
        w.feed(_loud_chunk())
        clock.advance(0.05)
    for _ in range(40):
        offset = w.current_offset()
        assert offset.head_pitch == 0.0
        assert offset.head_roll == 0.0
        assert offset.head_x == 0.0
        assert offset.head_y == 0.0
        assert offset.head_z == 0.0
        assert offset.antenna_left == 0.0
        assert offset.antenna_right == 0.0
        clock.advance(0.02)


# ---------------------------------------------------------------------------
# Amplitude bounds
# ---------------------------------------------------------------------------


def test_offsets_stay_within_oscillator_amplitudes():
    """Envelope is capped at 1.0, so head_yaw output ≤ peak_amplitude."""
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    # Hammer with extremely loud audio.
    huge = _pcm16([30000] * 480)
    for _ in range(10):
        w.feed(huge)
        clock.advance(0.05)
    # Sub-Hz oscillator needs a multi-second sweep to hit its peak.
    max_head_yaw = 0.0
    for _ in range(2000):
        offset = w.current_offset()
        max_head_yaw = max(max_head_yaw, abs(offset.head_yaw))
        clock.advance(0.005)
        w.feed(huge)

    # Bound drawn from the default oscillator amplitude (with a tiny slack
    # for floating-point + interaction with the envelope ceiling).
    assert max_head_yaw <= math.radians(6.0) * 1.05


# ---------------------------------------------------------------------------
# Concurrency smoke
# ---------------------------------------------------------------------------


def test_feed_and_current_offset_concurrent():
    import threading

    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    stop = threading.Event()

    def _feeder() -> None:
        chunk = _loud_chunk()
        while not stop.is_set():
            w.feed(chunk)

    t = threading.Thread(target=_feeder)
    t.start()
    try:
        for _ in range(500):
            clock.advance(0.001)
            w.current_offset()
    finally:
        stop.set()
        t.join(timeout=1.0)
