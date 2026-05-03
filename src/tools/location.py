"""Register / get tools for the kid's current location.

The model uses ``register_current_location`` when the kid (or parent)
tells it where they live; subsequent answers read the value back via
``get_current_location`` (or via the system-prompt injection wired in
Step 6). The persistence layer is :class:`LocationStore`.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from tools.base import ToolResult
from tools.location_store import LocationStore

logger = logging.getLogger(__name__)


REGISTER_TOOL_NAME = "register_current_location"
GET_TOOL_NAME = "get_current_location"


class RegisterCurrentLocationTool:
    """Persist the kid's current city / town."""

    name = REGISTER_TOOL_NAME

    def __init__(self, store: LocationStore) -> None:
        self._store = store

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": REGISTER_TOOL_NAME,
            "description": (
                "Save the kid's current city or town. Call this after the "
                "kid (or parent) tells you where they live, so future "
                "weather and local-event answers know what to search for."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": (
                            "City and (optionally) state/country. Examples: "
                            "'Seattle', 'Bangalore, India', 'Brooklyn, NY'."
                        ),
                    },
                },
                "required": ["location"],
                "additionalProperties": False,
            },
        }

    def prompt_block(self) -> str:
        # Step 6 owns the broader usage hint in the kids-profile prompt.
        return ""

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        location = arguments.get("location")
        if not isinstance(location, str) or not location.strip():
            return ToolResult(
                ok=False, detail="location must be a non-empty string"
            )
        try:
            await self._store.set(location)
        except ValueError as exc:
            return ToolResult(ok=False, detail=str(exc))
        except Exception as exc:
            logger.warning("[register_current_location] persist failed: %s", exc)
            return ToolResult(
                ok=False,
                detail="couldn't save the location — please try again",
            )
        return ToolResult(
            ok=True,
            detail="location saved",
            data={"location": self._store.get()},
        )


class GetCurrentLocationTool:
    """Read the cached location."""

    name = GET_TOOL_NAME

    def __init__(self, store: LocationStore) -> None:
        self._store = store

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": GET_TOOL_NAME,
            "description": (
                "Read the kid's currently registered location. Use this "
                "before searching for weather or local events if you "
                "haven't been told a location in this turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }

    def prompt_block(self) -> str:
        # Injected at session start so the model knows the location
        # without paying a round-trip through the tool. Mid-session
        # updates flow through the register/get tool payloads — the
        # system prompt itself is set once at connect time.
        return (
            "# Current location\n"
            f"The child currently lives in: {self._store.get()}.\n"
            "Use this for weather and local-events questions without asking "
            "again. If they tell you they moved, call "
            "`register_current_location` with the new city."
        )

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        del arguments  # the spec declares no parameters
        return ToolResult(
            ok=True,
            detail="current location",
            data={"location": self._store.get()},
        )


__all__ = [
    "GET_TOOL_NAME",
    "REGISTER_TOOL_NAME",
    "GetCurrentLocationTool",
    "RegisterCurrentLocationTool",
]
