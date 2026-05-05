"""Unit tests for :mod:`tools.registry`."""

from __future__ import annotations

from tools.location import GET_TOOL_NAME, REGISTER_TOOL_NAME
from tools.location_store import LocationStore
from tools.registry import build_default_registry
from tools.weather import OPENWEATHER_API_KEY_ENV, WEATHER_TOOL_NAME
from tools.web_lookup import WEB_LOOKUP_TOOL_NAME


_LOCATION_TOOL_NAMES = {REGISTER_TOOL_NAME, GET_TOOL_NAME}
_KEYLESS_TOOL_NAMES = _LOCATION_TOOL_NAMES | {WEB_LOOKUP_TOOL_NAME}


class _RecordingStore(LocationStore):
    """A LocationStore that doesn't touch GCS but records that load() ran."""

    def __init__(self) -> None:
        super().__init__(bucket_name=None)
        self.load_calls = 0

    async def load(self) -> None:
        self.load_calls += 1
        await super().load()


async def test_build_default_registry_loads_store_then_returns_registry(
    monkeypatch,
):
    monkeypatch.delenv(OPENWEATHER_API_KEY_ENV, raising=False)
    store = _RecordingStore()
    registry = await build_default_registry(location_store=store)

    assert store.load_calls == 1
    assert set(registry.tool_names) == _KEYLESS_TOOL_NAMES


async def test_build_default_registry_falls_back_to_env_store(monkeypatch):
    """When no store is passed, the factory builds one from the env var."""
    monkeypatch.delenv("KIDS_TEACHER_LOCATION_BUCKET", raising=False)
    monkeypatch.delenv(OPENWEATHER_API_KEY_ENV, raising=False)
    registry = await build_default_registry()
    assert set(registry.tool_names) == _KEYLESS_TOOL_NAMES


async def test_build_default_registry_specs_contain_keyless_tools(monkeypatch):
    monkeypatch.delenv(OPENWEATHER_API_KEY_ENV, raising=False)
    registry = await build_default_registry(location_store=_RecordingStore())
    spec_names = {s["name"] for s in registry.specs()}
    assert spec_names == _KEYLESS_TOOL_NAMES


async def test_build_default_registry_registers_weather_when_key_set(
    monkeypatch,
):
    monkeypatch.setenv(OPENWEATHER_API_KEY_ENV, "abc-123")
    registry = await build_default_registry(location_store=_RecordingStore())
    assert WEATHER_TOOL_NAME in registry.tool_names
    assert set(registry.tool_names) == _KEYLESS_TOOL_NAMES | {WEATHER_TOOL_NAME}


async def test_build_default_registry_skips_weather_when_key_blank(monkeypatch):
    monkeypatch.setenv(OPENWEATHER_API_KEY_ENV, "   ")
    registry = await build_default_registry(location_store=_RecordingStore())
    assert WEATHER_TOOL_NAME not in registry.tool_names
