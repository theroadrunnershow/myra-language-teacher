"""Composable hooks mixin that exposes a :class:`ToolRegistry`.

The web-side hooks class can compose this mixin directly. The robot
bridge already has motion-specific implementations of
``additional_tool_specs`` / ``handle_tool_call``; per the plan §3.3 it
chains the registry's outputs with motion's at the bridge layer rather
than going through the mixin.
"""

from __future__ import annotations

from typing import Any, Optional

from tools.base import ToolRegistry


class ToolsHooksMixin:
    """Provide the three optional hooks via a :class:`ToolRegistry`.

    ``tool_registry`` may be ``None`` to opt out — the mixin's methods
    then return empty / ``None`` values.
    """

    def __init__(
        self,
        *args: Any,
        tool_registry: Optional[ToolRegistry] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._tool_registry = tool_registry

    def additional_tool_specs(self) -> list[dict]:
        if self._tool_registry is None:
            return []
        return self._tool_registry.specs()

    def additional_instructions(self) -> str:
        if self._tool_registry is None:
            return ""
        return self._tool_registry.prompt_block()

    async def handle_tool_call(
        self,
        call_id: str,
        name: str,
        arguments: Any,
    ) -> Optional[str]:
        del call_id
        if self._tool_registry is None:
            return None
        result = await self._tool_registry.dispatch(name, arguments)
        return result.to_payload()


__all__ = ["ToolsHooksMixin"]
