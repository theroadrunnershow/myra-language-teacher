"""Default registry factory for the kids-teacher tools framework.

Wires the built-in tools (location pair, weather, Wikipedia lookup)
into a :class:`ToolRegistry`, loading the :class:`LocationStore` from
GCS once before returning. The robot session-setup awaits the factory
so the registry is ready to dispatch by the time the model first
calls into it.

Weather requires an API key — if :data:`OPENWEATHER_API_KEY_ENV` is
missing the tool is *not* registered, so the model never sees a tool
that's guaranteed to fail. Wikipedia lookup needs no key and is
always registered.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from tools.base import Tool, ToolRegistry
from tools.location import GetCurrentLocationTool, RegisterCurrentLocationTool
from tools.location_store import LocationStore, location_store_from_env
from tools.weather import GetWeatherTool, OPENWEATHER_API_KEY_ENV
from tools.web_lookup import WebLookupTool

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
    tools: list[Tool] = [
        RegisterCurrentLocationTool(store),
        GetCurrentLocationTool(store),
        WebLookupTool(),
    ]
    api_key = (os.environ.get(OPENWEATHER_API_KEY_ENV) or "").strip()
    if api_key:
        tools.append(GetWeatherTool(store, api_key=api_key))
    else:
        logger.warning(
            "[tools.registry] %s not set — get_weather tool disabled",
            OPENWEATHER_API_KEY_ENV,
        )
    return ToolRegistry(tools)


__all__ = ["build_default_registry"]
