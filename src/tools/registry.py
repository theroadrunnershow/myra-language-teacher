"""Default registry factory for the kids-teacher tools framework.

Wires the built-in tools (currently the location pair) into a
:class:`ToolRegistry`, loading the :class:`LocationStore` from GCS
once before returning. The robot session-setup awaits the factory so
the registry is ready to dispatch by the time the model first calls
into it.
"""

from __future__ import annotations

import logging
from typing import Optional

from tools.base import ToolRegistry
from tools.location import GetCurrentLocationTool, RegisterCurrentLocationTool
from tools.location_store import LocationStore, location_store_from_env
from tools.time import GetCurrentTimeTool

logger = logging.getLogger(__name__)


async def build_default_registry(
    *,
    location_store: Optional[LocationStore] = None,
) -> ToolRegistry:
    """Build the default kids-teacher :class:`ToolRegistry`.

    Loads the location store (cache-only afterwards). If
    ``location_store`` is omitted, one is constructed from
    :data:`tools.location_store.LOCATION_BUCKET_ENV_VAR` — falling
    back to an in-memory-only store when that env var is unset.
    """
    store = location_store or location_store_from_env()
    await store.load()
    return ToolRegistry(
        [
            RegisterCurrentLocationTool(store),
            GetCurrentLocationTool(store),
            GetCurrentTimeTool(store),
        ]
    )


__all__ = ["build_default_registry"]
