"""Named choreography clips — the L2 gesture vocabulary.

Each clip is a function-of-time returning an additive :class:`PoseOffset`.
The composer evaluates the active clip at every tick and blends the result
on top of the state pose, the L1 wobble, and the L3 face offset.

Clips are intentionally analytical (sinusoids, ramps): no keyframe-asset
loader, no HuggingFace pull, no extra deps. V1 ships ~8 clips covering
affect, celebration, system. Pedagogical clips (`count_bob`, `mimicry`,
`point_with_gaze`) are Phase 4 per ``tasks/plan-motion-director.md``.

Lane semantics — used by the scheduler for priority + debounce:

* ``safety``      — emergency stop / clamp.
* ``system``      — composer-driven (cancel / reset).
* ``celebration`` — long-form rewards (mini_dance, victory).
* ``affect``      — short emotional reactions (nod, tilt, pop, droop).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List

from motion.types import NEUTRAL, PoseOffset

PoseAt = Callable[[float], PoseOffset]


@dataclass(frozen=True)
class Clip:
    """A named pose trajectory.

    ``pose_at(t)`` is queried with ``t`` in the half-open interval
    ``[0, duration)``. Outside that range the composer treats the clip as
    finished and stops querying it.
    """

    name: str
    duration: float
    lane: str
    pose_at: PoseAt


class ChoreographyLibrary:
    """Registry of named clips.

    Looking up an unknown name returns ``None`` — the scheduler logs and
    drops the gesture rather than executing surprise motion.
    """

    def __init__(self, clips: Iterable[Clip] = ()) -> None:
        self._clips: Dict[str, Clip] = {}
        for clip in clips:
            self.register(clip)

    def register(self, clip: Clip) -> None:
        if clip.name in self._clips:
            raise ValueError(f"clip {clip.name!r} already registered")
        self._clips[clip.name] = clip

    def get(self, name: str) -> Clip | None:
        return self._clips.get(name)

    def names(self) -> List[str]:
        return sorted(self._clips)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._clips


# ---------------------------------------------------------------------------
# V1 clips
# ---------------------------------------------------------------------------

_DEG = math.pi / 180.0


def _ease_in_out(u: float) -> float:
    """Smoothstep on ``u`` clamped to ``[0, 1]`` — used for ramp envelopes."""
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    return u * u * (3.0 - 2.0 * u)


def _envelope(t: float, duration: float, ramp: float) -> float:
    """Trapezoid envelope: ramp up over ``ramp``, hold, ramp down over ``ramp``."""
    if duration <= 0.0:
        return 0.0
    if t <= 0.0 or t >= duration:
        return 0.0
    fall_start = duration - ramp
    if t < ramp:
        return _ease_in_out(t / ramp)
    if t > fall_start:
        return _ease_in_out((duration - t) / ramp)
    return 1.0


def _nod_encourage(duration: float = 1.6) -> PoseAt:
    amp = 8.0 * _DEG
    cycles = 2

    def _at(t: float) -> PoseOffset:
        if t < 0.0 or t >= duration:
            return NEUTRAL
        env = _envelope(t, duration, ramp=0.2)
        phase = 2.0 * math.pi * cycles * (t / duration)
        return PoseOffset(head_pitch=-amp * env * math.sin(phase))

    return _at


def _head_tilt_curious(duration: float = 2.0) -> PoseAt:
    target_roll = 15.0 * _DEG

    def _at(t: float) -> PoseOffset:
        if t < 0.0 or t >= duration:
            return NEUTRAL
        env = _envelope(t, duration, ramp=0.4)
        return PoseOffset(head_roll=target_roll * env)

    return _at


def _mini_dance_excited(duration: float = 2.0) -> PoseAt:
    bounce_amp_m = 0.012  # 12 mm head z bounce
    antenna_amp = 25.0 * _DEG
    bounce_cycles = 4
    antenna_cycles = 6

    def _at(t: float) -> PoseOffset:
        if t < 0.0 or t >= duration:
            return NEUTRAL
        env = _envelope(t, duration, ramp=0.25)
        bounce_phase = 2.0 * math.pi * bounce_cycles * (t / duration)
        antenna_phase = 2.0 * math.pi * antenna_cycles * (t / duration)
        bounce = bounce_amp_m * env * abs(math.sin(bounce_phase))
        wiggle = antenna_amp * env * math.sin(antenna_phase)
        return PoseOffset(
            head_z=bounce,
            antenna_left=wiggle,
            antenna_right=-wiggle,
        )

    return _at


def _victory_wiggle(duration: float = 2.5) -> PoseAt:
    nod_amp = 10.0 * _DEG
    antenna_amp = 35.0 * _DEG
    nod_cycles = 3
    antenna_cycles = 8

    def _at(t: float) -> PoseOffset:
        if t < 0.0 or t >= duration:
            return NEUTRAL
        env = _envelope(t, duration, ramp=0.3)
        nod_phase = 2.0 * math.pi * nod_cycles * (t / duration)
        antenna_phase = 2.0 * math.pi * antenna_cycles * (t / duration)
        return PoseOffset(
            head_pitch=-nod_amp * env * math.sin(nod_phase),
            antenna_left=antenna_amp * env * math.sin(antenna_phase),
            antenna_right=-antenna_amp * env * math.sin(antenna_phase),
        )

    return _at


def _surprise_pop(duration: float = 0.8) -> PoseAt:
    pitch_amp = 12.0 * _DEG  # head tips back
    antenna_amp = 30.0 * _DEG

    def _at(t: float) -> PoseOffset:
        if t < 0.0 or t >= duration:
            return NEUTRAL
        u = t / duration
        # Asymmetric: snap up in first 25%, ease back over the rest.
        if u < 0.25:
            env = _ease_in_out(u / 0.25)
        else:
            env = _ease_in_out((1.0 - u) / 0.75)
        return PoseOffset(
            head_pitch=pitch_amp * env,
            antenna_left=antenna_amp * env,
            antenna_right=antenna_amp * env,
        )

    return _at


def _sad_droop(duration: float = 1.5) -> PoseAt:
    pitch_amp = 10.0 * _DEG  # head down
    antenna_amp = -25.0 * _DEG  # antennas drop

    def _at(t: float) -> PoseOffset:
        if t < 0.0 or t >= duration:
            return NEUTRAL
        env = _envelope(t, duration, ramp=0.4)
        return PoseOffset(
            head_pitch=-pitch_amp * env,
            antenna_left=antenna_amp * env,
            antenna_right=antenna_amp * env,
        )

    return _at


def _thinking_up(duration: float = 2.0) -> PoseAt:
    pitch_amp = 8.0 * _DEG  # gaze up
    roll_amp = 5.0 * _DEG  # slight tilt

    def _at(t: float) -> PoseOffset:
        if t < 0.0 or t >= duration:
            return NEUTRAL
        env = _envelope(t, duration, ramp=0.4)
        return PoseOffset(
            head_pitch=pitch_amp * env,
            head_roll=roll_amp * env,
        )

    return _at


def _cancel(duration: float = 0.4) -> PoseAt:
    """No-op clip used by the scheduler to occupy the L2 lane while the
    composer eases the primary offset back toward neutral. Always returns
    :data:`NEUTRAL` — the easing happens in the composer, not the clip.
    """

    def _at(t: float) -> PoseOffset:  # noqa: ARG001
        return NEUTRAL

    return _at


def default_library() -> ChoreographyLibrary:
    """Build the V1 vocabulary. Order is alphabetical for stability."""
    return ChoreographyLibrary(
        [
            Clip("cancel", 0.4, "system", _cancel(0.4)),
            Clip("head_tilt_curious", 2.0, "affect", _head_tilt_curious(2.0)),
            Clip("mini_dance_excited", 2.0, "celebration", _mini_dance_excited(2.0)),
            Clip("nod_encourage", 1.6, "affect", _nod_encourage(1.6)),
            Clip("sad_droop", 1.5, "affect", _sad_droop(1.5)),
            Clip("surprise_pop", 0.8, "affect", _surprise_pop(0.8)),
            Clip("thinking_up", 2.0, "affect", _thinking_up(2.0)),
            Clip("victory_wiggle", 2.5, "celebration", _victory_wiggle(2.5)),
        ]
    )
