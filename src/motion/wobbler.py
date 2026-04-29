"""L1 audio-reactive wobble — secondary motion locked to assistant speech.

This is the "alive while talking" layer. It consumes PCM16 audio chunks
fed in by the bridge, tracks a smoothed loudness envelope, and drives a
small bank of detuned sinusoidal oscillators on top. The composer asks
for ``current_offset()`` on each tick and adds the result to the pose.

There is no LLM or gesture allow-list here: the wobbler can't pick a
"wrong" move because it can't pick at all — it only modulates.

Loudness model
--------------
Each ``feed()`` chunk's RMS is converted to a normalised ``[0, 1]``
loudness using a fixed reference level. The envelope tracks the loudness
with a fast attack and a slower exponential decay, so silence → no motion
within a few hundred milliseconds even if ``feed()`` keeps being called
with quiet chunks. ``reset()`` zeros the envelope immediately for
barge-in (plan §7 rule 4).

Oscillators
-----------
Three slow sinusoids: a head-yaw "no-no" shake plus antiphase antenna
flaps. Each is driven by the wall clock so motion stays continuous
across feeds. The envelope multiplies the amplitude of every oscillator
uniformly — quiet audio = small motion, loud audio = full-amplitude.

Frequencies are sub-Hz so the result reads as a deliberate slow shake
suitable for a young child, not a jittery wobble. Per-channel amplitudes
are conservative; the composer also has its own final-output safety cap.
"""

from __future__ import annotations

import math
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from motion.types import NEUTRAL, PoseOffset

_DEG = math.pi / 180.0


# Reference loudness — chosen so a "comfortable conversational" PCM16 stream
# normalises to ~0.7. Empirical; tune in Phase 6 with a hardware mic loop.
_REFERENCE_RMS = 6500.0

# Loudness envelope smoothing. Attack snaps up so the wobble responds to
# the first loud sample; decay is slow enough that motion doesn't stutter
# between syllables but fast enough to die during silence.
_ATTACK_SECONDS = 0.05
_DECAY_SECONDS = 0.35


@dataclass(frozen=True)
class _Oscillator:
    """One sinusoidal driver feeding one channel."""

    channel: str  # PoseOffset field name
    frequency_hz: float
    peak_amplitude: float  # SI units (rad or m) at envelope = 1.0
    phase_offset: float = 0.0


# Slow head shake + ear shake. Sub-Hz frequencies so the motion reads as
# deliberate rather than jittery. Amplitudes are conservative — caps in
# plan §9.
_DEFAULT_OSCILLATORS = (
    _Oscillator("head_yaw", frequency_hz=0.35, peak_amplitude=4.0 * _DEG),
    _Oscillator("antenna_left", frequency_hz=0.5, peak_amplitude=8.0 * _DEG),
    _Oscillator(
        "antenna_right",
        frequency_hz=0.5,
        peak_amplitude=8.0 * _DEG,
        phase_offset=math.pi,  # antiphase with left so they alternate
    ),
)


class AudioWobbler:
    """Audio-amplitude-modulated additive offset source.

    Wire as the composer's wobble source:

        composer.set_wobble_source(wobbler.current_offset)

    Feed PCM16 mono chunks (any sample rate) from the assistant audio path,
    and call :meth:`reset` on barge-in.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        oscillators=_DEFAULT_OSCILLATORS,
        reference_rms: float = _REFERENCE_RMS,
    ) -> None:
        self._clock = clock
        self._oscillators = tuple(oscillators)
        self._reference_rms = reference_rms

        self._lock = threading.Lock()
        self._envelope = 0.0
        self._last_update_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Audio ingestion
    # ------------------------------------------------------------------

    def feed(self, pcm_bytes: bytes) -> None:
        """Update the loudness envelope from one PCM16 chunk."""
        if not pcm_bytes:
            return
        loudness = self._chunk_loudness(pcm_bytes)
        if loudness <= 0.0:
            # Silent chunk: just decay; don't pull envelope up to zero.
            now = self._clock()
            with self._lock:
                self._decay_to_now_locked(now)
            return
        now = self._clock()
        with self._lock:
            self._follow_envelope_locked(now, loudness)

    def reset(self) -> None:
        """Zero the envelope immediately. Used on barge-in."""
        with self._lock:
            self._envelope = 0.0
            self._last_update_at = self._clock()

    # ------------------------------------------------------------------
    # Composer-facing source
    # ------------------------------------------------------------------

    def current_offset(self) -> PoseOffset:
        """Return the current additive pose offset.

        Decays the envelope toward the current clock first so the wobble
        dies cleanly during silence even if no ``feed()`` calls arrive.
        """
        now = self._clock()
        with self._lock:
            self._decay_to_now_locked(now)
            envelope = self._envelope

        # Below ~0.1% of full envelope the per-channel offsets are sub-mrad —
        # not worth computing oscillators for and visually identical to
        # neutral. The cutoff also ensures the wobble dies cleanly during
        # silence even though exponential decay never literally reaches 0.
        if envelope <= 1e-3:
            return NEUTRAL

        accum = {
            "head_pitch": 0.0,
            "head_yaw": 0.0,
            "head_roll": 0.0,
            "head_x": 0.0,
            "head_y": 0.0,
            "head_z": 0.0,
            "antenna_left": 0.0,
            "antenna_right": 0.0,
        }
        for osc in self._oscillators:
            phase = 2.0 * math.pi * osc.frequency_hz * now + osc.phase_offset
            accum[osc.channel] += envelope * osc.peak_amplitude * math.sin(phase)
        return PoseOffset(**accum)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _chunk_loudness(self, pcm_bytes: bytes) -> float:
        """Normalised loudness in ``[0, 1]`` from one PCM16 chunk.

        Uses RMS divided by a fixed reference level, soft-clipped at 1.0.
        """
        # PCM16 little-endian ≡ ``<i2``. Drop a trailing odd byte if the
        # backend sent half a sample (defensive — should never happen).
        usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
        if usable < 2:
            return 0.0
        sample_count = usable // 2
        samples = struct.unpack(f"<{sample_count}h", pcm_bytes[:usable])
        # Mean of squares without numpy — chunks are small (<= 80ms ≈ 1920
        # samples at 24kHz) so a Python loop is fine.
        sum_sq = 0.0
        for s in samples:
            sum_sq += s * s
        rms = math.sqrt(sum_sq / sample_count)
        loudness = rms / self._reference_rms
        if loudness > 1.0:
            return 1.0
        if loudness < 0.0:  # impossible but cheap
            return 0.0
        return loudness

    def _follow_envelope_locked(self, now: float, target: float) -> None:
        """Standard attack/release envelope follower.

        When the envelope is effectively zero (first feed ever, or after
        silence has decayed it) we snap to ``target`` so the wobble responds
        instantly to the leading edge of an utterance rather than waiting
        for the attack constant to ramp from zero.
        """
        if self._envelope <= 1e-3:
            self._envelope = target
            self._last_update_at = now
            return
        if self._last_update_at is None:
            self._envelope = target
            self._last_update_at = now
            return
        dt = max(now - self._last_update_at, 1e-6)
        tau = _ATTACK_SECONDS if target > self._envelope else _DECAY_SECONDS
        alpha = 1.0 - math.exp(-dt / tau)
        self._envelope += (target - self._envelope) * alpha
        self._last_update_at = now

    def _decay_to_now_locked(self, now: float) -> None:
        """Pull the envelope toward zero based on time since the last update."""
        if self._last_update_at is None:
            self._last_update_at = now
            return
        dt = now - self._last_update_at
        if dt <= 0:
            return
        self._envelope *= math.exp(-dt / _DECAY_SECONDS)
        self._last_update_at = now
