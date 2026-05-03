"""Tools framework — opt-in pack of LLM tools mounted via :class:`ToolRegistry`.

See ``tasks/plan-tools-framework.md`` for the design.
"""

from tools.base import (
    DEFAULT_DISPATCH_TIMEOUT_S,
    Tool,
    ToolRegistry,
    ToolResult,
)

__all__ = [
    "DEFAULT_DISPATCH_TIMEOUT_S",
    "Tool",
    "ToolRegistry",
    "ToolResult",
]
