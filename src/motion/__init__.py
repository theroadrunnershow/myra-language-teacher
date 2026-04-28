"""Motion director — three-layer additive motion stack for kids-teacher mode.

See ``tasks/plan-motion-director.md`` for the design. The package is split:

* :mod:`motion.types` — shared :class:`PoseOffset` value type.
* :mod:`motion.library` — named choreography clips (L2 vocabulary).
* :mod:`motion.composer` — the per-tick layer mixer that hands a final
  pose to a sink (the sink is the bridge's adapter to ``RobotController``).

L1/L2/L3 sources (audio wobbler, LLM-gesture scheduler, face-offset mixer)
plug into the composer via ``set_wobble_source`` / ``play_clip`` /
``set_face_offset_source``. None of those modules import each other; they
only meet inside the composer.
"""

from motion.composer import MovementComposer
from motion.library import Clip, ChoreographyLibrary, default_library
from motion.types import PoseOffset

__all__ = [
    "Clip",
    "ChoreographyLibrary",
    "MovementComposer",
    "PoseOffset",
    "default_library",
]
