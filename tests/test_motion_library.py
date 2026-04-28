"""Unit tests for :mod:`motion.library`."""

from __future__ import annotations

import math

import pytest

from motion.library import (
    Clip,
    ChoreographyLibrary,
    default_library,
)
from motion.types import NEUTRAL, PoseOffset


def _const_clip(name: str, duration: float, lane: str, value: PoseOffset) -> Clip:
    return Clip(name=name, duration=duration, lane=lane, pose_at=lambda t: value)  # noqa: ARG005


def test_register_then_get_returns_clip():
    lib = ChoreographyLibrary()
    clip = _const_clip("foo", 1.0, "affect", PoseOffset(head_pitch=0.1))
    lib.register(clip)
    assert lib.get("foo") is clip


def test_get_unknown_returns_none():
    lib = ChoreographyLibrary()
    assert lib.get("does_not_exist") is None


def test_register_duplicate_name_raises():
    lib = ChoreographyLibrary()
    lib.register(_const_clip("foo", 1.0, "affect", NEUTRAL))
    with pytest.raises(ValueError):
        lib.register(_const_clip("foo", 2.0, "affect", NEUTRAL))


def test_names_returns_sorted_list():
    lib = ChoreographyLibrary(
        [
            _const_clip("zebra", 1.0, "affect", NEUTRAL),
            _const_clip("apple", 1.0, "affect", NEUTRAL),
            _const_clip("mango", 1.0, "affect", NEUTRAL),
        ]
    )
    assert lib.names() == ["apple", "mango", "zebra"]


def test_contains_only_matches_strings():
    lib = ChoreographyLibrary([_const_clip("foo", 1.0, "affect", NEUTRAL)])
    assert "foo" in lib
    assert "bar" not in lib
    assert 5 not in lib  # type: ignore[operator]


# ---------------------------------------------------------------------------
# default_library V1 vocabulary
# ---------------------------------------------------------------------------


def test_default_library_has_expected_v1_clips():
    lib = default_library()
    expected = {
        "cancel",
        "head_tilt_curious",
        "mini_dance_excited",
        "nod_encourage",
        "sad_droop",
        "surprise_pop",
        "thinking_up",
        "victory_wiggle",
    }
    assert set(lib.names()) == expected


def test_default_clips_return_neutral_outside_duration():
    lib = default_library()
    for name in lib.names():
        clip = lib.get(name)
        assert clip is not None
        assert clip.pose_at(-0.5) == NEUTRAL
        assert clip.pose_at(clip.duration) == NEUTRAL
        assert clip.pose_at(clip.duration + 1.0) == NEUTRAL


def test_default_clips_return_neutral_at_boundaries_due_to_envelope():
    """All envelope-shaped clips ramp from / to zero. Cancel + tilt clips
    are exempt because tilt holds and cancel is constant-zero."""
    lib = default_library()
    for name in ("nod_encourage", "mini_dance_excited", "victory_wiggle", "sad_droop"):
        clip = lib.get(name)
        assert clip is not None
        # Just inside both ends, the envelope is ~0 so the offset is ~0.
        epsilon = 1e-6
        assert _close_to_neutral(clip.pose_at(epsilon))
        assert _close_to_neutral(clip.pose_at(clip.duration - epsilon))


def test_cancel_clip_is_always_neutral():
    clip = default_library().get("cancel")
    assert clip is not None
    for t in (0.0, 0.1, 0.2, 0.3):
        assert clip.pose_at(t) == NEUTRAL


def test_nod_encourage_actually_nods():
    """At the peak of the first nod we should be well away from neutral."""
    clip = default_library().get("nod_encourage")
    assert clip is not None
    # 2-cycle sinusoid: zeros at u=0, 0.25, 0.5, 0.75, 1.0 — sample at the
    # peak between zeros (u=0.125) where sin(2π·2·u) = sin(π/2) = 1.
    pose = clip.pose_at(clip.duration * 0.125)
    assert abs(pose.head_pitch) > 0.01
    # The rest of the channels should be unaffected.
    assert pose.head_roll == 0.0
    assert pose.antenna_left == 0.0


def test_head_tilt_curious_holds_target_in_middle():
    clip = default_library().get("head_tilt_curious")
    assert clip is not None
    pose = clip.pose_at(clip.duration * 0.5)
    # At midpoint the trapezoid envelope is fully on, so roll = target.
    assert math.isclose(pose.head_roll, math.radians(15.0), rel_tol=1e-6)


def test_mini_dance_excited_drives_antennae_in_opposition():
    clip = default_library().get("mini_dance_excited")
    assert clip is not None
    # Sample several points; antenna L and R should always sum to ~zero.
    for u in (0.2, 0.35, 0.5, 0.7, 0.85):
        pose = clip.pose_at(clip.duration * u)
        assert math.isclose(
            pose.antenna_left + pose.antenna_right, 0.0, abs_tol=1e-9
        )


def test_clip_metadata_lanes():
    lib = default_library()
    assert lib.get("cancel").lane == "system"  # type: ignore[union-attr]
    assert lib.get("mini_dance_excited").lane == "celebration"  # type: ignore[union-attr]
    assert lib.get("victory_wiggle").lane == "celebration"  # type: ignore[union-attr]
    assert lib.get("nod_encourage").lane == "affect"  # type: ignore[union-attr]


def _close_to_neutral(pose: PoseOffset, abs_tol: float = 1e-3) -> bool:
    return all(
        abs(getattr(pose, field)) <= abs_tol
        for field in (
            "head_pitch",
            "head_yaw",
            "head_roll",
            "head_x",
            "head_y",
            "head_z",
            "antenna_left",
            "antenna_right",
        )
    )
