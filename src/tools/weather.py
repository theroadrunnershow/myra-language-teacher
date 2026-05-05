"""Current-weather lookup via OpenWeatherMap (free tier).

Reads the API key from :data:`OPENWEATHER_API_KEY_ENV`. The factory
in :mod:`tools.registry` skips registration when the key is missing
so the model never sees a tool that's guaranteed to fail; a missing
key is a deploy-time issue, not a per-call one.

The 3 s registry wall-clock cap leaves room for one HTTP round-trip;
a 2 s per-request timeout keeps us safely inside that budget. On any
failure (network, non-2xx, malformed JSON) the call returns a
kid-friendly ``ok=False`` and logs the underlying error — never the
full URL, which would leak the API key.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Optional

import requests

from tools.base import ToolResult
from tools.location_store import LocationStore

logger = logging.getLogger(__name__)


WEATHER_TOOL_NAME = "get_weather"
OPENWEATHER_API_KEY_ENV = "OPENWEATHER_API_KEY"
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
HTTP_TIMEOUT_S = 2.0
KID_FRIENDLY_FAILURE = (
    "I can't check the weather right now — please ask a grown-up."
)


class GetWeatherTool:
    """Fetch current weather for a city via OpenWeatherMap."""

    name = WEATHER_TOOL_NAME

    def __init__(
        self,
        location_store: LocationStore,
        *,
        api_key: str,
    ) -> None:
        self._store = location_store
        self._api_key = api_key

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": WEATHER_TOOL_NAME,
            "description": (
                "Look up the current weather for a city. If no city is "
                "given, the kid's registered location is used. Returns a "
                "short summary with the conditions and the temperature in "
                "Celsius. Paraphrase before reading aloud."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": (
                            "City to look up. Optional — omit to use the "
                            "kid's registered location."
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    def prompt_block(self) -> str:
        return ""

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        override = arguments.get("location")
        if isinstance(override, str) and override.strip():
            location = override.strip()
        else:
            location = self._store.get()
        try:
            payload = await asyncio.get_event_loop().run_in_executor(
                None, self._fetch, location
            )
        except Exception as exc:
            logger.error("[get_weather] API error for %r: %s", location, exc)
            return ToolResult(ok=False, detail=KID_FRIENDLY_FAILURE)
        return ToolResult(
            ok=True,
            detail=_format_summary(location, payload),
            data={"location": location, "weather": _structured(payload)},
        )

    def _fetch(self, location: str) -> dict:
        response = requests.get(
            OPENWEATHER_URL,
            params={
                "q": location,
                "appid": self._api_key,
                "units": "metric",
            },
            timeout=HTTP_TIMEOUT_S,
        )
        if not response.ok:
            # Don't surface the URL — it carries the API key.
            raise RuntimeError(
                f"openweathermap status {response.status_code}"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("openweathermap returned non-object payload")
        return body


def _structured(payload: dict) -> dict:
    weather_list = payload.get("weather")
    weather = weather_list[0] if isinstance(weather_list, list) and weather_list else {}
    main = payload.get("main") if isinstance(payload.get("main"), dict) else {}
    return {
        "conditions": weather.get("description") or weather.get("main") or "",
        "temperature_c": main.get("temp"),
    }


def _format_summary(location: str, payload: dict) -> str:
    summary = _structured(payload)
    conditions = summary["conditions"] or "weather"
    temp = summary["temperature_c"]
    if isinstance(temp, (int, float)):
        return f"{location}: {conditions}, {round(temp)}°C"
    return f"{location}: {conditions}"


__all__ = [
    "KID_FRIENDLY_FAILURE",
    "OPENWEATHER_API_KEY_ENV",
    "WEATHER_TOOL_NAME",
    "GetWeatherTool",
]
