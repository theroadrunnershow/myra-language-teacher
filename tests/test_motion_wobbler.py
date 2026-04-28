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


def test_constructor_rejects_bad_sample_rate():
    with pytest.raises(ValueError):
        AudioWobbler(sample_rate=0)
    with pytest.raises(ValueError):
        AudioWobbler(sample_rate=-1)


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
    offset = w.current_offset()
    # At least one channel should be away from zero.
    assert any(
        abs(getattr(offset, ch)) > 1e-4
        for ch in (
            "head_pitch",
            "head_yaw",
            "head_roll",
            "head_z",
            "antenna_left",
            "antenna_right",
        )
    )


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


def test_default_oscillators_drive_at_least_six_channels():
    """Smoke: prove every default-driven channel sees motion at some sample."""
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    for _ in range(20):
        w.feed(_loud_chunk())
        clock.advance(0.05)
    seen = {
        ch: False
        for ch in (
            "head_pitch",
            "head_yaw",
            "head_roll",
            "head_z",
            "antenna_left",
            "antenna_right",
        )
    }
    # Sample at many points so we don't miss a zero crossing.
    for _ in range(40):
        offset = w.current_offset()
        for ch in seen:
            if abs(getattr(offset, ch)) > 1e-6:
                seen[ch] = True
        clock.advance(0.02)
        # Keep envelope up for the duration of the probe.
        w.feed(_loud_chunk())
    assert all(seen.values()), f"some channels never moved: {seen}"


def test_unmoved_channels_stay_zero():
    """``head_x`` / ``head_y`` are not driven by the default oscillators."""
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    for _ in range(20):
        w.feed(_loud_chunk())
        clock.advance(0.05)
    for _ in range(40):
        offset = w.current_offset()
        assert offset.head_x == 0.0
        assert offset.head_y == 0.0
        clock.advance(0.02)


# ---------------------------------------------------------------------------
# Amplitude bounds
# ---------------------------------------------------------------------------


def test_offsets_stay_within_oscillator_amplitudes():
    """Envelope is capped at 1.0, so per-channel output ≤ peak_amplitude."""
    clock = _FakeClock()
    w = AudioWobbler(clock=clock)
    # Hammer with extremely loud audio.
    huge = _pcm16([30000] * 480)
    for _ in range(10):
        w.feed(huge)
        clock.advance(0.05)
    # Sample widely.
    max_per_channel = {
        "head_pitch": 0.0,
        "head_yaw": 0.0,
        "head_roll": 0.0,
        "head_z": 0.0,
        "antenna_left": 0.0,
        "antenna_right": 0.0,
    }
    for _ in range(200):
        offset = w.current_offset()
        for ch in max_per_channel:
            max_per_channel[ch] = max(max_per_channel[ch], abs(getattr(offset, ch)))
        clock.advance(0.005)
        w.feed(huge)

    # Bounds drawn from the default oscillator amplitudes (with a tiny
    # slack for floating-point + interaction with the envelope ceiling).
    assert max_per_channel["head_pitch"] <= math.radians(3.0) * 1.05
    assert max_per_channel["head_yaw"] <= math.radians(2.0) * 1.05
    assert max_per_channel["head_roll"] <= math.radians(1.0) * 1.05
    assert max_per_channel["head_z"] <= 0.0025 * 1.05
    assert max_per_channel["antenna_left"] <= math.radians(8.0) * 1.05
    assert max_per_channel["antenna_right"] <= math.radians(8.0) * 1.05


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
