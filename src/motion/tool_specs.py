"""LLM tool specs + router for the L2 gesture vocabulary.

Pollen's reference exposes ~7 motion tools to the Realtime model. We expose
two for V1, both routed through :class:`GestureScheduler`:

* ``play_gesture(name)`` — fire any clip from the choreography library.
* ``stop_motion()``     — flush the scheduler (LLM-driven self-cancel).

Why so few? Pollen's tool split (`dance` / `play_emotion` / `move_head`)
exists because their HuggingFace clip libraries are split that way. Our
clips are flat — one tool with an enum is enough. ``move_head`` belongs
to the L3 face-tracking layer, not the L2 LLM tool surface.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional

from motion.library import ChoreographyLibrary
from motion.scheduler import GestureDecision, GestureScheduler

logger = logging.getLogger(__name__)


PLAY_GESTURE_TOOL_NAME = "play_gesture"
STOP_MOTION_TOOL_NAME = "stop_motion"
MOTION_TOOL_NAMES = (PLAY_GESTURE_TOOL_NAME, STOP_MOTION_TOOL_NAME)


def gesture_tool_specs(library: ChoreographyLibrary) -> List[dict]:
    """Build OpenAI Realtime ``function`` tool specs for the L2 vocabulary.

    The ``play_gesture`` schema enumerates the clip names so the model can
    only request gestures the library actually knows about — the scheduler
    enforces this again at dispatch time, but pinning the enum at the API
    boundary is cheap insurance.
    """
    return [
        {
            "type": "function",
            "name": PLAY_GESTURE_TOOL_NAME,
            "description": (
                "Play a short expressive robot gesture (head + antenna motion) "
                "to make the conversation feel alive. Pick a gesture that "
                "matches the emotion you're conveying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": library.names(),
                        "description": "Name of the gesture clip to play.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": STOP_MOTION_TOOL_NAME,
            "description": (
                "Stop any in-flight gesture immediately. Use when you've "
                "changed your mind about expressing something."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    ]


# A short paragraph the system prompt can include so the model knows what
# each gesture *means*. Per plan §6.2 we describe semantics, not when-to-use.
def gesture_vocabulary_prompt_block() -> str:
    return (
        "# Robot gestures\n"
        "When you want to react physically to something, call play_gesture(name) "
        "with one of these names:\n"
        "- nod_encourage: gentle double nod for affirmation.\n"
        "- head_tilt_curious: tilted head, like asking a question.\n"
        "- mini_dance_excited: small celebration bounce for correct answers.\n"
        "- victory_wiggle: bigger celebration for milestones.\n"
        "- surprise_pop: quick head-up reaction to a surprise.\n"
        "- sad_droop: head and antennas drop for sympathy.\n"
        "- thinking_up: gaze upward while you think.\n"
        "Call stop_motion() if you change your mind mid-gesture.\n"
    )


@dataclass(frozen=True)
class ToolCallResult:
    """Outcome of a single tool dispatch — what the bridge sends back."""

    ok: bool
    detail: str

    def to_payload(self) -> str:
        """JSON string suitable for the OpenAI ``function_call_output`` field."""
        return json.dumps({"ok": self.ok, "detail": self.detail})


class GestureToolRouter:
    """Translate normalized ``tool.call`` events into scheduler actions.

    Validates the tool name + arguments, calls into
    :class:`GestureScheduler`, and returns a :class:`ToolCallResult` that
    the caller can ship back to the LLM as a ``function_call_output``.

    Unknown tool names and malformed argument payloads are reported as
    ``ok=False`` with a short reason. The router never raises — bad
    tool calls must not crash the realtime event loop.
    """

    def __init__(
        self,
        *,
        scheduler: GestureScheduler,
        logger_override: Optional[logging.Logger] = None,
    ) -> None:
        self._scheduler = scheduler
        self._log = logger_override or logger

    @property
    def tool_names(self) -> tuple[str, ...]:
        return MOTION_TOOL_NAMES

    def dispatch(self, name: str, arguments: Any) -> ToolCallResult:
        if name == PLAY_GESTURE_TOOL_NAME:
            return self._dispatch_play_gesture(arguments)
        if name == STOP_MOTION_TOOL_NAME:
            self._scheduler.flush()
            return ToolCallResult(ok=True, detail="motion stopped")
        self._log.info("[motion.tool_router] unknown tool %r", name)
        return ToolCallResult(ok=False, detail=f"unknown tool: {name}")

    def _dispatch_play_gesture(self, arguments: Any) -> ToolCallResult:
        parsed = _coerce_arguments(arguments)
        if parsed is None:
            return ToolCallResult(ok=False, detail="invalid arguments JSON")
        gesture_name = parsed.get("name")
        if not isinstance(gesture_name, str) or not gesture_name:
            return ToolCallResult(
                ok=False, detail="play_gesture requires a non-empty 'name' string"
            )
        decision: GestureDecision = self._scheduler.request(gesture_name)
        if decision.accepted:
            return ToolCallResult(ok=True, detail=f"playing {decision.name}")
        return ToolCallResult(
            ok=False, detail=f"dropped: {decision.reason or 'unknown reason'}"
        )


def _coerce_arguments(arguments: Any) -> Optional[Mapping[str, Any]]:
    """Tool-call arguments arrive either as a dict or a JSON string.

    Returns ``None`` for anything that can't be coerced into a mapping
    (which the caller surfaces as an error result).
    """
    if isinstance(arguments, Mapping):
        return arguments
    if isinstance(arguments, (bytes, bytearray)):
        try:
            arguments = arguments.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(arguments, str):
        if not arguments.strip():
            return {}
        try:
            parsed = json.loads(arguments)
        except (ValueError, TypeError):
            return None
        if isinstance(parsed, Mapping):
            return parsed
        return None
    return None
