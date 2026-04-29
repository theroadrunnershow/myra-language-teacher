"""Motion-director orchestration — bundles L1/L2/L3 + composer.

The bridge owns one :class:`MotionStack`. The stack hides the wiring of:

* :class:`AudioWobbler` (L1)        — fed assistant TTS chunks.
* :class:`MovementComposer`         — 30 Hz layer mixer driving the SDK.
* :class:`GestureScheduler`         — L2 gesture queue + priority gate.
* :class:`GestureToolRouter`        — LLM tool surface for ``play_gesture``
  and ``stop_motion``.
* :class:`FaceOffsetMixer` (L3)     — subscribes to the existing
  :class:`face_tracker.FaceTracker`.

Each layer can be disabled at construction so the operator (or a kill-switch
env var) can land an audit-only release. A ``MotionStack`` with no layers
enabled still constructs a composer driving the legacy speak / listen /
idle state pose; that's the minimum overlay needed to keep the bridge's
new code path coherent without exposing aliveness yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, FrozenSet, Iterable, List, Optional

from motion.composer import MovementComposer
from motion.face_offset import FaceOffsetMixer
from motion.library import ChoreographyLibrary, default_library
from motion.scheduler import GestureScheduler
from motion.tool_specs import (
    GestureToolRouter,
    ToolCallResult,
    gesture_tool_specs,
    gesture_vocabulary_prompt_block,
)
from motion.types import PoseOffset
from motion.wobbler import AudioWobbler

logger = logging.getLogger(__name__)


# Layer names recognised by the stack. ``composer`` is implicit (always on).
LAYER_WOBBLE = "wobble"
LAYER_GESTURES = "gestures"
LAYER_TRACKING = "tracking"
ALL_LAYERS: FrozenSet[str] = frozenset({LAYER_WOBBLE, LAYER_GESTURES, LAYER_TRACKING})


def parse_layers(spec: Optional[str]) -> FrozenSet[str]:
    """Parse a ``KIDS_TEACHER_MOTION_LAYERS`` value into a layer set.

    Recognised values:

    * ``None`` / ``""`` / ``"full"`` → all layers
    * ``"none"``                     → empty set
    * comma-separated layer names   → matching subset (unknown names logged
      and dropped)
    """
    if spec is None:
        return ALL_LAYERS
    raw = spec.strip().lower()
    if not raw or raw == "full":
        return ALL_LAYERS
    if raw == "none":
        return frozenset()
    parts = {p.strip() for p in raw.split(",") if p.strip()}
    layers = parts & ALL_LAYERS
    unknown = parts - ALL_LAYERS
    if unknown:
        logger.warning(
            "[motion.stack] ignoring unknown motion layers: %s", sorted(unknown)
        )
    return frozenset(layers)


@dataclass(frozen=True)
class MotionStackConfig:
    layers: FrozenSet[str] = ALL_LAYERS
    tick_hz: float = 30.0


class MotionStack:
    """One orchestrator the bridge talks to instead of four separate objects.

    Construction never starts the composer thread; call :meth:`start` after
    wiring is complete. :meth:`stop` is idempotent.
    """

    def __init__(
        self,
        *,
        robot_controller: Any,
        config: MotionStackConfig = MotionStackConfig(),
        library: Optional[ChoreographyLibrary] = None,
        decision_logger=None,
    ) -> None:
        self._robot = robot_controller
        self._config = config
        self._library = library or default_library()

        self._composer = MovementComposer(
            pose_sink=self._pose_sink,
            tick_hz=config.tick_hz,
        )

        self._wobbler: Optional[AudioWobbler] = None
        if LAYER_WOBBLE in config.layers:
            self._wobbler = AudioWobbler()
            self._composer.set_wobble_source(self._wobbler.current_offset)

        self._scheduler: Optional[GestureScheduler] = None
        self._tool_router: Optional[GestureToolRouter] = None
        if LAYER_GESTURES in config.layers:
            self._scheduler = GestureScheduler(
                composer=self._composer,
                library=self._library,
                decision_logger=decision_logger,
            )
            self._tool_router = GestureToolRouter(scheduler=self._scheduler)

        self._face_mixer: Optional[FaceOffsetMixer] = None
        if LAYER_TRACKING in config.layers:
            self._face_mixer = FaceOffsetMixer()
            self._composer.set_face_offset_source(self._face_mixer.current_offset)

        self._first_assistant_chunk_seen = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._composer.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._composer.stop(timeout=timeout)
        if self._face_mixer is not None:
            self._face_mixer.detach()

    # ------------------------------------------------------------------
    # Bridge-facing event hooks
    # ------------------------------------------------------------------

    def feed_assistant_audio(self, chunk: bytes) -> None:
        if self._wobbler is not None:
            self._wobbler.feed(chunk)

    def enter_assistant_speech(self) -> None:
        """Called on the first audio chunk of an assistant turn."""
        self._composer.set_state("speak")
        if self._face_mixer is not None:
            self._face_mixer.set_gain_state("robot_speaking")
        self._first_assistant_chunk_seen = True

    def exit_assistant_speech(self) -> None:
        """Called when the turn ends or barge-in flushes playback."""
        self._first_assistant_chunk_seen = False
        if self._wobbler is not None:
            self._wobbler.reset()
        if self._scheduler is not None:
            self._scheduler.flush()
        self._composer.set_state("listen")
        if self._face_mixer is not None:
            self._face_mixer.set_gain_state("idle")

    def enter_child_speech(self) -> None:
        """Called on VAD ``speech_started``."""
        if self._scheduler is not None:
            self._scheduler.flush()
        if self._wobbler is not None:
            self._wobbler.reset()
        self._composer.set_state("listen")
        if self._face_mixer is not None:
            self._face_mixer.set_gain_state("child_speaking")

    def exit_child_speech(self) -> None:
        """Called on VAD ``speech_stopped``.

        Does not change the composer state — the assistant turn (or session
        idle) decides what's next.
        """
        if self._face_mixer is not None and self._face_mixer.gain_state == "child_speaking":
            self._face_mixer.set_gain_state("idle")

    def set_idle(self) -> None:
        """Called on session IDLE / ENDED status."""
        self._composer.set_state("idle")
        if self._face_mixer is not None:
            self._face_mixer.set_gain_state("idle")

    # ------------------------------------------------------------------
    # Tool routing
    # ------------------------------------------------------------------

    def additional_tool_specs(self) -> List[dict]:
        """Tool specs to inject into ``build_session_payload``.

        Empty when the gestures layer is disabled.
        """
        if self._tool_router is None:
            return []
        return gesture_tool_specs(self._library)

    def gesture_vocabulary_prompt_block(self) -> str:
        """Prompt block describing the gesture vocabulary semantics.

        Empty when the gestures layer is disabled — no point telling the
        model about tools it can't call.
        """
        if self._tool_router is None:
            return ""
        return gesture_vocabulary_prompt_block()

    def handle_tool_call(self, call_id: str, name: str, arguments: str) -> Optional[str]:
        """Run a tool call and return a JSON ack string, or ``None``."""
        del call_id  # opaque to the router; bridge owns the ack/no-ack policy
        if self._tool_router is None:
            return None
        result = self._tool_router.dispatch(name, arguments)
        return result.to_payload()

    # ------------------------------------------------------------------
    # L3 wiring
    # ------------------------------------------------------------------

    def attach_face_tracker(self, tracker: Any) -> None:
        if self._face_mixer is None:
            return
        self._face_mixer.attach(tracker)

    def detach_face_tracker(self) -> None:
        if self._face_mixer is None:
            return
        self._face_mixer.detach()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _pose_sink(self, offset: PoseOffset, period: float) -> None:
        apply = getattr(self._robot, "apply_pose", None)
        if apply is None:
            return
        try:
            apply(
                head_pitch=offset.head_pitch,
                head_yaw=offset.head_yaw,
                head_roll=offset.head_roll,
                head_x=offset.head_x,
                head_y=offset.head_y,
                head_z=offset.head_z,
                antenna_left=offset.antenna_left,
                antenna_right=offset.antenna_right,
                duration=period,
            )
        except Exception as exc:
            logger.debug("[motion.stack] apply_pose raised: %s", exc)


# Convenience used by the CLI factory; importable for tests.
def select_layers_from_iterable(layers: Iterable[str]) -> FrozenSet[str]:
    return frozenset({l for l in layers if l in ALL_LAYERS})


__all__ = [
    "ALL_LAYERS",
    "LAYER_GESTURES",
    "LAYER_TRACKING",
    "LAYER_WOBBLE",
    "MotionStack",
    "MotionStackConfig",
    "ToolCallResult",
    "parse_layers",
    "select_layers_from_iterable",
]
