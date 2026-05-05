"""Unit tests for :mod:`tools.weather`."""

from __future__ import annotations

import json
from typing import Any, List

import pytest

from tools import weather as weather_module
from tools.weather import (
    KID_FRIENDLY_FAILURE,
    OPENWEATHER_URL,
    WEATHER_TOOL_NAME,
    GetWeatherTool,
)


class _FakeStore:
    def __init__(self, *, initial: str = "Seattle, WA 98177") -> None:
        self._location = initial

    def get(self) -> str:
        return self._location


class _FakeResponse:
    def __init__(
        self,
        *,
        ok: bool = True,
        status_code: int = 200,
        body: Any = None,
    ) -> None:
        self.ok = ok
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class _RecordingGet:
    """Captures every ``requests.get`` call so tests can assert on params."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: List[dict] = []

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self._response


def _ok_payload() -> dict:
    return {
        "weather": [{"main": "Clouds", "description": "scattered clouds"}],
        "main": {"temp": 18.4},
    }


# ---------------------------------------------------------------------------
# Spec / prompt block
# ---------------------------------------------------------------------------


def test_spec_shape():
    tool = GetWeatherTool(_FakeStore(), api_key="k")
    spec = tool.spec()
    assert spec["type"] == "function"
    assert spec["name"] == WEATHER_TOOL_NAME
    assert spec["parameters"]["required"] == []
    assert "location" in spec["parameters"]["properties"]
    assert spec["parameters"]["additionalProperties"] is False


def test_prompt_block_is_empty():
    assert GetWeatherTool(_FakeStore(), api_key="k").prompt_block() == ""


# ---------------------------------------------------------------------------
# Successful lookup
# ---------------------------------------------------------------------------


async def test_call_uses_registered_location_when_no_override(monkeypatch):
    fake_get = _RecordingGet(_FakeResponse(body=_ok_payload()))
    monkeypatch.setattr(weather_module.requests, "get", fake_get)

    tool = GetWeatherTool(_FakeStore(initial="Bangalore, India"), api_key="abc")
    result = await tool.call({})

    assert result.ok is True
    body = json.loads(result.to_payload())
    assert "Bangalore, India" in body["detail"]
    assert "scattered clouds" in body["detail"]
    assert "18" in body["detail"]
    assert body["location"] == "Bangalore, India"
    assert body["weather"] == {
        "conditions": "scattered clouds",
        "temperature_c": 18.4,
    }
    # API key must travel as a query param, not be embedded in the URL.
    assert fake_get.calls[0]["url"] == OPENWEATHER_URL
    assert fake_get.calls[0]["params"]["q"] == "Bangalore, India"
    assert fake_get.calls[0]["params"]["appid"] == "abc"
    assert fake_get.calls[0]["params"]["units"] == "metric"


async def test_call_prefers_explicit_location_over_store(monkeypatch):
    fake_get = _RecordingGet(_FakeResponse(body=_ok_payload()))
    monkeypatch.setattr(weather_module.requests, "get", fake_get)

    tool = GetWeatherTool(_FakeStore(initial="Seattle"), api_key="k")
    result = await tool.call({"location": "  Tokyo  "})

    assert result.ok is True
    assert fake_get.calls[0]["params"]["q"] == "Tokyo"


async def test_call_falls_back_to_store_when_override_blank(monkeypatch):
    fake_get = _RecordingGet(_FakeResponse(body=_ok_payload()))
    monkeypatch.setattr(weather_module.requests, "get", fake_get)

    tool = GetWeatherTool(_FakeStore(initial="Seattle"), api_key="k")
    await tool.call({"location": "   "})
    assert fake_get.calls[0]["params"]["q"] == "Seattle"


async def test_call_handles_missing_temp_gracefully(monkeypatch):
    payload = {"weather": [{"description": "rain"}], "main": {}}
    monkeypatch.setattr(
        weather_module.requests, "get",
        _RecordingGet(_FakeResponse(body=payload)),
    )
    tool = GetWeatherTool(_FakeStore(), api_key="k")
    result = await tool.call({})
    assert result.ok is True
    assert "rain" in result.detail


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


async def test_call_returns_kid_friendly_on_http_error(monkeypatch, caplog):
    monkeypatch.setattr(
        weather_module.requests, "get",
        _RecordingGet(_FakeResponse(ok=False, status_code=404)),
    )
    tool = GetWeatherTool(_FakeStore(), api_key="secret-key-xyz")

    with caplog.at_level("ERROR"):
        result = await tool.call({"location": "Atlantis"})

    assert result.ok is False
    assert result.detail == KID_FRIENDLY_FAILURE
    # Logs include the location and status, but never the API key.
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Atlantis" in log_text
    assert "404" in log_text
    assert "secret-key-xyz" not in log_text


async def test_call_returns_kid_friendly_on_network_exception(monkeypatch, caplog):
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(weather_module.requests, "get", boom)
    tool = GetWeatherTool(_FakeStore(), api_key="k")

    with caplog.at_level("ERROR"):
        result = await tool.call({})
    assert result.ok is False
    assert result.detail == KID_FRIENDLY_FAILURE
    assert any("API error" in r.getMessage() for r in caplog.records)


async def test_call_returns_kid_friendly_on_non_object_payload(monkeypatch):
    monkeypatch.setattr(
        weather_module.requests, "get",
        _RecordingGet(_FakeResponse(body=["not", "an", "object"])),
    )
    tool = GetWeatherTool(_FakeStore(), api_key="k")
    result = await tool.call({})
    assert result.ok is False
    assert result.detail == KID_FRIENDLY_FAILURE
