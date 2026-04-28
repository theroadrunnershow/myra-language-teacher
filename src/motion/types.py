"""Shared value type for motion-director offsets.

A :class:`PoseOffset` is an additive contribution to the robot's pose: head
pitch/yaw/roll (radians), head x/y/z translation (metres), and per-antenna
angles (radians). Layers (L1 wobble, L2 clip, L3 face offset) each produce
a ``PoseOffset`` per tick; the composer sums them and clips the result
against safety bounds before handing the final pose to the sink.

All zero values are the neutral pose. Scalar-multiplication and addition are
the only operations; they are all the composer needs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class PoseOffset:
    """Additive head + antenna offsets in SI units (radians, metres)."""

    head_pitch: float = 0.0
    head_yaw: float = 0.0
    head_roll: float = 0.0
    head_x: float = 0.0
    head_y: float = 0.0
    head_z: float = 0.0
    antenna_left: float = 0.0
    antenna_right: float = 0.0

    def __add__(self, other: "PoseOffset") -> "PoseOffset":
        if not isinstance(other, PoseOffset):
            return NotImplemented
        return PoseOffset(
            head_pitch=self.head_pitch + other.head_pitch,
            head_yaw=self.head_yaw + other.head_yaw,
            head_roll=self.head_roll + other.head_roll,
            head_x=self.head_x + other.head_x,
            head_y=self.head_y + other.head_y,
            head_z=self.head_z + other.head_z,
            antenna_left=self.antenna_left + other.antenna_left,
            antenna_right=self.antenna_right + other.antenna_right,
        )

    def scaled(self, gain: float) -> "PoseOffset":
        return PoseOffset(
            head_pitch=self.head_pitch * gain,
            head_yaw=self.head_yaw * gain,
            head_roll=self.head_roll * gain,
            head_x=self.head_x * gain,
            head_y=self.head_y * gain,
            head_z=self.head_z * gain,
            antenna_left=self.antenna_left * gain,
            antenna_right=self.antenna_right * gain,
        )

    def clipped(self, caps: "PoseOffset") -> "PoseOffset":
        """Clamp every channel to ``[-cap, +cap]`` per ``caps``.

        Caps are interpreted as absolute magnitudes; sign is ignored. A cap
        of ``0`` zeroes the channel (useful for disabling translation on
        layers that should only rotate the head).
        """
        return PoseOffset(
            head_pitch=_clamp(self.head_pitch, abs(caps.head_pitch)),
            head_yaw=_clamp(self.head_yaw, abs(caps.head_yaw)),
            head_roll=_clamp(self.head_roll, abs(caps.head_roll)),
            head_x=_clamp(self.head_x, abs(caps.head_x)),
            head_y=_clamp(self.head_y, abs(caps.head_y)),
            head_z=_clamp(self.head_z, abs(caps.head_z)),
            antenna_left=_clamp(self.antenna_left, abs(caps.antenna_left)),
            antenna_right=_clamp(self.antenna_right, abs(caps.antenna_right)),
        )

    def with_(self, **changes: float) -> "PoseOffset":
        """Convenience: return a copy with the named channels replaced."""
        return replace(self, **changes)


NEUTRAL = PoseOffset()


def _clamp(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    if value > cap:
        return cap
    if value < -cap:
        return -cap
    return value
