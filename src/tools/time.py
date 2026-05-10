"""Tool: read the current local time at the kid's registered location.

Pairs with :class:`tools.location.GetCurrentLocationTool` — the model
already knows *where* the kid is via the location prompt block; this
tool answers *when* by converting UTC ``now()`` into the location's
timezone at call time. No frozen-snapshot injection: the value is
fresh on every invocation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from tools.base import ToolResult
from tools.location_store import LocationStore
from tools.timezone_lookup import timezone_for_location, timezone_name_for_location

logger = logging.getLogger(__name__)


GET_CURRENT_TIME_TOOL_NAME = "get_current_time"


class GetCurrentTimeTool:
    """Return the current local time at the registered location."""

    name = GET_CURRENT_TIME_TOOL_NAME

    def __init__(
        self,
        store: LocationStore,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._store = store
        # ``now_fn`` returns a tz-aware UTC datetime. Injectable so tests
        # can pin "now" deterministically.
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": GET_CURRENT_TIME_TOOL_NAME,
            "description": (
                "Read the current local time at the kid's registered "
                "location. Use this whenever the kid asks 'what time is "
                "it', 'what day is it', or anything time-of-day related."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }

    def prompt_block(self) -> str:
        # The companion location tool's prompt block already mentions
        # this tool; keep this empty to avoid duplication.
        return ""

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        del arguments  # the spec declares no parameters
        location = self._store.get()
        tz_name = timezone_name_for_location(location)
        local = self._now().astimezone(timezone_for_location(location))
        return ToolResult(
            ok=True,
            detail=f"current local time at {location}",
            data={
                "iso": local.isoformat(timespec="seconds"),
                "time": local.strftime("%-I:%M %p"),
                "date": local.strftime("%A, %B %-d, %Y"),
                "timezone": tz_name,
                "location": location,
            },
        )


__all__ = [
    "GET_CURRENT_TIME_TOOL_NAME",
    "GetCurrentTimeTool",
]
