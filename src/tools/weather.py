"""Tool: current weather + short forecast at the kid's location.

Backed by `Open-Meteo <https://open-meteo.com>`_: free, no API key, no
signup, ~10k calls/day for non-commercial use. Two GETs per invocation
— geocode the location string, then fetch the forecast at the resolved
lat/lon. Defaults the lookup to whatever the kid has registered in
:class:`LocationStore` so "what's the weather today?" works out of the
box.

Pairs with :class:`tools.location.GetCurrentLocationTool` and
:class:`tools.time.GetCurrentTimeTool`: the location prompt block
already advertises this tool's existence (no extra system-prompt block
here).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Mapping, Optional
from urllib.parse import quote

from tools.base import ToolResult
from tools.location_store import LocationStore

logger = logging.getLogger(__name__)


GET_WEATHER_TOOL_NAME = "get_weather"

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather-interpretation codes collapsed into ~6 kid-friendly
# buckets. Reference: https://open-meteo.com/en/docs#weathervariables
_WMO_SUMMARY: Mapping[int, str] = {
    0: "sunny",
    1: "mostly sunny",
    2: "partly cloudy",
    3: "cloudy",
    45: "foggy",
    48: "foggy",
    51: "drizzly", 53: "drizzly", 55: "drizzly",
    56: "drizzly", 57: "drizzly",
    61: "rainy", 63: "rainy", 65: "rainy",
    66: "rainy", 67: "rainy",
    71: "snowy", 73: "snowy", 75: "snowy", 77: "snowy",
    80: "rainy", 81: "rainy", 82: "rainy",
    85: "snowy", 86: "snowy",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}


HttpGet = Callable[[str], Awaitable[Mapping[str, Any]]]


class GetWeatherTool:
    """Return current weather + today/tomorrow forecast for a location."""

    name = GET_WEATHER_TOOL_NAME

    def __init__(
        self,
        store: LocationStore,
        *,
        http_get: Optional[HttpGet] = None,
    ) -> None:
        self._store = store
        # Injectable for tests; the default uses httpx.AsyncClient lazily.
        self._http_get = http_get or _default_http_get

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": GET_WEATHER_TOOL_NAME,
            "description": (
                "Read the current weather and a short forecast for today "
                "and tomorrow. Use this whenever the kid asks anything "
                "weather-related ('is it raining?', 'is it cold outside?', "
                "'will it snow tomorrow?'). Defaults to the kid's "
                "registered location when no 'location' is given."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": (
                            "City and (optionally) state/country, e.g. "
                            "'Seattle, WA' or 'Bangalore, India'. Omit to "
                            "use the kid's registered location."
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        }

    def prompt_block(self) -> str:
        # The location tool's prompt block already mentions weather; no
        # need to duplicate guidance here.
        return ""

    async def call(self, arguments: Mapping[str, Any]) -> ToolResult:
        raw = arguments.get("location") if isinstance(arguments, Mapping) else None
        if isinstance(raw, str) and raw.strip():
            location = raw.strip()
        else:
            location = self._store.get()

        try:
            geo = await self._http_get(
                f"{_GEOCODE_URL}?name={quote(location)}&count=1&format=json"
            )
        except Exception as exc:
            logger.warning("[get_weather] geocode failed for %r: %s", location, exc)
            return _bridge_failure()

        results = geo.get("results") if isinstance(geo, Mapping) else None
        if not results:
            return ToolResult(
                ok=False,
                detail=f"couldn't find a weather match for {location}",
            )
        first = results[0]
        try:
            lat = float(first["latitude"])
            lon = float(first["longitude"])
        except (KeyError, TypeError, ValueError):
            return _bridge_failure()
        resolved_name = first.get("name") or location

        try:
            forecast = await self._http_get(
                f"{_FORECAST_URL}?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,weather_code,is_day,wind_speed_10m"
                "&daily=temperature_2m_max,temperature_2m_min,weather_code,"
                "precipitation_probability_max"
                "&temperature_unit=fahrenheit&wind_speed_unit=mph"
                "&forecast_days=2&timezone=auto"
            )
        except Exception as exc:
            logger.warning(
                "[get_weather] forecast failed for %r: %s", resolved_name, exc
            )
            return _bridge_failure()

        payload = _build_payload(resolved_name, forecast)
        if payload is None:
            return _bridge_failure()
        return ToolResult(
            ok=True,
            detail=f"current weather at {resolved_name}",
            data=payload,
        )


def _bridge_failure() -> ToolResult:
    return ToolResult(
        ok=False, detail="I couldn't check the weather just now."
    )


def _summary_for_code(code: Any) -> str:
    try:
        return _WMO_SUMMARY.get(int(code), "")
    except (TypeError, ValueError):
        return ""


def _build_payload(
    location: str, forecast: Mapping[str, Any]
) -> Optional[Mapping[str, Any]]:
    if not isinstance(forecast, Mapping):
        return None
    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}
    if not isinstance(current, Mapping) or not isinstance(daily, Mapping):
        return None
    try:
        temp_f = round(float(current["temperature_2m"]))
    except (KeyError, TypeError, ValueError):
        return None

    out: dict[str, Any] = {
        "location": location,
        "summary": _summary_for_code(current.get("weather_code")),
        "temp_f": temp_f,
    }

    daily_max = daily.get("temperature_2m_max") or []
    daily_min = daily.get("temperature_2m_min") or []
    daily_code = daily.get("weather_code") or []
    daily_rain = daily.get("precipitation_probability_max") or []

    if daily_max and daily_min:
        try:
            out["high_f"] = round(float(daily_max[0]))
            out["low_f"] = round(float(daily_min[0]))
        except (TypeError, ValueError):
            pass
    if daily_rain:
        try:
            out["rain_chance_pct"] = int(daily_rain[0])
        except (TypeError, ValueError):
            pass

    if len(daily_max) > 1 and len(daily_min) > 1:
        try:
            tomorrow: dict[str, Any] = {
                "summary": _summary_for_code(daily_code[1])
                if len(daily_code) > 1
                else "",
                "high_f": round(float(daily_max[1])),
                "low_f": round(float(daily_min[1])),
            }
            if len(daily_rain) > 1:
                tomorrow["rain_chance_pct"] = int(daily_rain[1])
            out["tomorrow"] = tomorrow
        except (TypeError, ValueError):
            pass

    return out


async def _default_http_get(url: str) -> Mapping[str, Any]:
    # Lazy import so this module stays importable on hosts that haven't
    # installed httpx yet (mirrors the pattern used elsewhere for
    # google-cloud-storage in :mod:`tools.location_store`).
    import httpx

    async with httpx.AsyncClient(timeout=2.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


__all__ = [
    "GET_WEATHER_TOOL_NAME",
    "GetWeatherTool",
]
