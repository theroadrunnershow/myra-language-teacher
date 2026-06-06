"""Unit tests for :mod:`tools.weather`."""

from __future__ import annotations

import json
from typing import Any, List, Mapping

import pytest

from tools.base import ToolRegistry
from tools.weather import GET_WEATHER_TOOL_NAME, GetWeatherTool


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimal :class:`LocationStore`-shaped fake."""

    def __init__(self, *, initial: str = "Seattle, WA 98177") -> None:
        self._location = initial

    def get(self) -> str:
        return self._location


def _seattle_geocode() -> Mapping[str, Any]:
    return {
        "results": [
            {"name": "Seattle", "latitude": 47.6062, "longitude": -122.3321}
        ]
    }


def _seattle_forecast() -> Mapping[str, Any]:
    """Open-Meteo-shaped response with today + tomorrow data."""
    return {
        "current": {
            "temperature_2m": 61.7,
            "weather_code": 0,  # sunny
            "is_day": 1,
            "wind_speed_10m": 8.0,
        },
        "daily": {
            "temperature_2m_max": [65.4, 58.1],
            "temperature_2m_min": [50.9, 49.7],
            "weather_code": [0, 63],  # sunny today, rainy tomorrow
            "precipitation_probability_max": [10, 80],
        },
    }


class _RecordingHttpGet:
    """Async callable that returns canned payloads keyed by URL substring."""

    def __init__(self, responses: List[tuple[str, Any]]) -> None:
        # Each entry: (substring_to_match, response_or_exception).
        self._responses = responses
        self.calls: List[str] = []

    async def __call__(self, url: str) -> Mapping[str, Any]:
        self.calls.append(url)
        for needle, value in self._responses:
            if needle in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected URL: {url}")


# ---------------------------------------------------------------------------
# Spec / prompt block
# ---------------------------------------------------------------------------


def test_spec_shape():
    spec = GetWeatherTool(_FakeStore()).spec()
    assert spec["type"] == "function"
    assert spec["name"] == GET_WEATHER_TOOL_NAME
    # location is optional — the tool defaults to the registered value.
    assert spec["parameters"]["required"] == []
    assert spec["parameters"]["properties"]["location"]["type"] == "string"
    assert spec["parameters"]["additionalProperties"] is False


def test_prompt_block_is_empty():
    """Location tool's prompt block already covers weather guidance."""
    assert GetWeatherTool(_FakeStore()).prompt_block() == ""


# ---------------------------------------------------------------------------
# call() — happy paths
# ---------------------------------------------------------------------------


async def test_call_with_explicit_location_hits_open_meteo_twice():
    http = _RecordingHttpGet(
        [
            ("geocoding-api", _seattle_geocode()),
            ("api.open-meteo.com/v1/forecast", _seattle_forecast()),
        ]
    )
    tool = GetWeatherTool(_FakeStore(initial="Bangalore"), http_get=http)

    result = await tool.call({"location": "Seattle"})
    body = json.loads(result.to_payload())

    assert len(http.calls) == 2
    assert "name=Seattle" in http.calls[0]
    assert "latitude=47.6062" in http.calls[1]
    assert "longitude=-122.3321" in http.calls[1]
    assert body["ok"] is True
    assert body["location"] == "Seattle"
    assert body["summary"] == "sunny"
    assert body["temp_f"] == 62
    assert body["high_f"] == 65
    assert body["low_f"] == 51
    assert body["rain_chance_pct"] == 10
    assert body["tomorrow"] == {
        "summary": "rainy",
        "high_f": 58,
        "low_f": 50,
        "rain_chance_pct": 80,
    }


async def test_call_with_no_arg_uses_registered_location():
    http = _RecordingHttpGet(
        [
            ("geocoding-api", _seattle_geocode()),
            ("api.open-meteo.com/v1/forecast", _seattle_forecast()),
        ]
    )
    tool = GetWeatherTool(_FakeStore(initial="Seattle, WA 98177"), http_get=http)

    result = await tool.call({})

    # The geocoder is asked about the registered location verbatim.
    assert "name=Seattle%2C%20WA%2098177" in http.calls[0]
    assert result.ok is True


async def test_call_blank_location_falls_back_to_store():
    http = _RecordingHttpGet(
        [
            ("geocoding-api", _seattle_geocode()),
            ("api.open-meteo.com/v1/forecast", _seattle_forecast()),
        ]
    )
    tool = GetWeatherTool(_FakeStore(initial="Seattle"), http_get=http)

    result = await tool.call({"location": "   "})
    assert result.ok is True
    assert "name=Seattle" in http.calls[0]


# ---------------------------------------------------------------------------
# call() — failure modes (kid-friendly ok=False, no exceptions leak)
# ---------------------------------------------------------------------------


async def test_geocode_zero_results_returns_ok_false():
    http = _RecordingHttpGet(
        [
            ("geocoding-api", {"results": []}),
        ]
    )
    tool = GetWeatherTool(_FakeStore(), http_get=http)

    result = await tool.call({"location": "Atlantis"})

    assert result.ok is False
    assert "Atlantis" in result.detail
    # Forecast endpoint must not be touched if geocoding gave nothing.
    assert all("forecast" not in url for url in http.calls)


async def test_geocode_http_failure_returns_kid_friendly_ok_false():
    http = _RecordingHttpGet(
        [
            ("geocoding-api", RuntimeError("network down")),
        ]
    )
    tool = GetWeatherTool(_FakeStore(initial="Seattle"), http_get=http)

    result = await tool.call({})

    assert result.ok is False
    assert "couldn't check the weather" in result.detail
    # No exception text leaked into the kid-facing detail.
    assert "network down" not in result.detail


async def test_forecast_http_failure_returns_kid_friendly_ok_false():
    http = _RecordingHttpGet(
        [
            ("geocoding-api", _seattle_geocode()),
            ("api.open-meteo.com/v1/forecast", RuntimeError("upstream 502")),
        ]
    )
    tool = GetWeatherTool(_FakeStore(initial="Seattle"), http_get=http)

    result = await tool.call({})

    assert result.ok is False
    assert "couldn't check the weather" in result.detail
    assert "502" not in result.detail


async def test_forecast_missing_current_temp_returns_kid_friendly_ok_false():
    """Malformed forecast (missing required field) shouldn't surface
    a stack trace to the kid — the registry guarantees no exceptions
    propagate, but the tool itself maps the bad payload to ok=False."""
    bad_forecast = {"current": {}, "daily": {}}
    http = _RecordingHttpGet(
        [
            ("geocoding-api", _seattle_geocode()),
            ("api.open-meteo.com/v1/forecast", bad_forecast),
        ]
    )
    tool = GetWeatherTool(_FakeStore(initial="Seattle"), http_get=http)

    result = await tool.call({})

    assert result.ok is False
    assert "couldn't check the weather" in result.detail


# ---------------------------------------------------------------------------
# WMO weather-code mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected_summary",
    [
        (0, "sunny"),
        (3, "cloudy"),
        (63, "rainy"),
        (73, "snowy"),
        (95, "thunderstorm"),
        # Unknown code falls back to an empty string rather than a guess.
        (42, ""),
    ],
)
async def test_summary_mapping_for_representative_codes(code, expected_summary):
    forecast = {
        "current": {"temperature_2m": 50.0, "weather_code": code},
        "daily": {
            "temperature_2m_max": [55.0],
            "temperature_2m_min": [45.0],
            "weather_code": [code],
            "precipitation_probability_max": [20],
        },
    }
    http = _RecordingHttpGet(
        [
            ("geocoding-api", _seattle_geocode()),
            ("api.open-meteo.com/v1/forecast", forecast),
        ]
    )
    tool = GetWeatherTool(_FakeStore(initial="Seattle"), http_get=http)

    body = json.loads((await tool.call({})).to_payload())
    assert body["summary"] == expected_summary


# ---------------------------------------------------------------------------
# Registry dispatch wiring
# ---------------------------------------------------------------------------


async def test_dispatch_via_registry_round_trips_to_get_weather():
    """Smoke test: a registry holding only this tool dispatches by name."""
    http = _RecordingHttpGet(
        [
            ("geocoding-api", _seattle_geocode()),
            ("api.open-meteo.com/v1/forecast", _seattle_forecast()),
        ]
    )
    tool = GetWeatherTool(_FakeStore(initial="Seattle"), http_get=http)
    registry = ToolRegistry([tool])

    result = await registry.dispatch(GET_WEATHER_TOOL_NAME, {"location": "Seattle"})

    assert result.ok is True
    body = json.loads(result.to_payload())
    assert body["location"] == "Seattle"
    assert body["temp_f"] == 62
