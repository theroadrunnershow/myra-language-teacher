"""Core types for the tools framework.

Each tool implements :class:`Tool` (a ``Protocol``-shaped class with
``name``, ``spec()``, ``prompt_block()``, ``call()``). The
:class:`ToolRegistry` aggregates tools, dispatches calls under a 3 s
wall-clock cap (Q5 in the plan), and traps exceptions/timeouts into
``ok=False`` so a broken tool never kills the assistant turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# 3s wall-clock cap on tool dispatch (plan §5 Q5). Tools may set tighter
# per-call timeouts; the registry never lets one go longer than this.
DEFAULT_DISPATCH_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a single tool dispatch — what the registry sends back.

    Mirrors :class:`motion.tool_specs.ToolCallResult` but adds ``data``
    for structured payloads (e.g. the registered location).
    """

    ok: bool
    detail: str
    data: Optional[Mapping[str, Any]] = None

    def to_payload(self) -> str:
        """JSON string suitable for the model's ``function_call_output`` field."""
        body: dict[str, Any] = {"ok": self.ok, "detail": self.detail}
        if self.data is not None:
            body.update(self.data)
        return json.dumps(body)


@runtime_checkable
class Tool(Protocol):
    """A tool the registry can dispatch.

    ``spec()`` must return an OpenAI-Realtime-shaped function spec
    (the canonical form). ``prompt_block()`` returns text appended to
    the system prompt — empty string opts out. ``call()`` runs the
    tool; it may raise, but the registry traps exceptions and returns
    ``ok=False`` so callers don't have to.
    """

    name: str

    def spec(self) -> dict: ...

    def prompt_block(self) -> str: ...

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult: ...


class ToolRegistry:
    """Aggregate tools, dispatch by name with a wall-clock cap.

    The registry knows about argument coercion, exception trapping, the
    3s timeout, and unknown-name handling — individual tools stay
    small.
    """

    def __init__(
        self,
        tools: Iterable[Tool],
        *,
        timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S,
        logger_override: Optional[logging.Logger] = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools:
            if t.name in self._tools:
                raise ValueError(f"duplicate tool name: {t.name!r}")
            self._tools[t.name] = t
        self._timeout_s = timeout_s
        self._log = logger_override or logger

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def prompt_block(self) -> str:
        blocks = [t.prompt_block() for t in self._tools.values()]
        return "\n\n".join(b for b in blocks if b.strip())

    async def dispatch(self, name: str, arguments: Any) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            self._log.info("[tools.registry] unknown tool %r", name)
            return ToolResult(ok=False, detail=f"unknown tool: {name}")

        parsed = _coerce_arguments(arguments)
        if parsed is None:
            return ToolResult(ok=False, detail="invalid arguments JSON")

        try:
            return await asyncio.wait_for(tool.call(parsed), timeout=self._timeout_s)
        except asyncio.TimeoutError:
            self._log.warning(
                "[tools.registry] %r exceeded %.1fs cap", name, self._timeout_s
            )
            return ToolResult(
                ok=False, detail=f"{name} timed out after {self._timeout_s:g}s"
            )
        except Exception as exc:
            self._log.warning("[tools.registry] %r raised: %s", name, exc)
            return ToolResult(ok=False, detail=f"{name} failed: {exc!s}")


def _coerce_arguments(arguments: Any) -> Optional[Mapping[str, Any]]:
    """Tool-call arguments arrive either as a dict or a JSON string.

    Returns ``None`` for anything that can't be coerced into a mapping
    (which the caller surfaces as an error result). Mirrors the helper
    in :mod:`motion.tool_specs`; duplicated for module independence.
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


__all__ = [
    "DEFAULT_DISPATCH_TIMEOUT_S",
    "Tool",
    "ToolRegistry",
    "ToolResult",
]
