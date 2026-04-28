"""Unit tests for :mod:`motion.types`."""

from __future__ import annotations

import math

import pytest

from motion.types import NEUTRAL, PoseOffset


def test_neutral_is_all_zero():
    assert NEUTRAL == PoseOffset()
    assert NEUTRAL.head_pitch == 0.0
    assert NEUTRAL.antenna_left == 0.0


def test_addition_sums_every_channel():
    a = PoseOffset(head_pitch=0.1, antenna_left=0.2)
    b = PoseOffset(head_pitch=0.3, head_yaw=-0.4, antenna_right=0.5)
    c = a + b
    assert c == PoseOffset(
        head_pitch=0.4,
        head_yaw=-0.4,
        antenna_left=0.2,
        antenna_right=0.5,
    )


def test_addition_with_non_pose_returns_notimplemented():
    a = PoseOffset(head_pitch=0.1)
    with pytest.raises(TypeError):
        _ = a + 5  # type: ignore[operator]


def test_scaled_multiplies_every_channel():
    a = PoseOffset(head_pitch=0.2, antenna_left=0.5)
    scaled = a.scaled(0.5)
    assert scaled == PoseOffset(head_pitch=0.1, antenna_left=0.25)


def test_scaled_by_zero_yields_neutral():
    a = PoseOffset(head_pitch=1.0, antenna_left=1.0, head_z=0.5)
    assert a.scaled(0.0) == NEUTRAL


def test_clipped_caps_each_channel_independently():
    cap = PoseOffset(head_pitch=0.1, head_yaw=0.2, antenna_left=0.3)
    raw = PoseOffset(head_pitch=0.5, head_yaw=-0.5, antenna_left=-0.4)
    clipped = raw.clipped(cap)
    assert math.isclose(clipped.head_pitch, 0.1)
    assert math.isclose(clipped.head_yaw, -0.2)
    assert math.isclose(clipped.antenna_left, -0.3)


def test_clipped_zero_cap_zeroes_channel():
    cap = PoseOffset()  # all zero
    raw = PoseOffset(head_pitch=0.5, antenna_left=0.5)
    assert raw.clipped(cap) == NEUTRAL


def test_clipped_within_bounds_is_unchanged():
    cap = PoseOffset(head_pitch=1.0, antenna_left=1.0)
    raw = PoseOffset(head_pitch=0.3, antenna_left=-0.7)
    assert raw.clipped(cap) == raw


def test_clipped_uses_absolute_cap_value():
    cap = PoseOffset(head_pitch=-0.1)  # negative caps treated as magnitude
    raw = PoseOffset(head_pitch=0.5)
    assert math.isclose(raw.clipped(cap).head_pitch, 0.1)


def test_with_replaces_only_named_channels():
    a = PoseOffset(head_pitch=0.1, antenna_left=0.2, antenna_right=0.3)
    b = a.with_(head_pitch=0.0)
    assert b == PoseOffset(antenna_left=0.2, antenna_right=0.3)
