"""Unit tests for :mod:`tools.registry`."""

from __future__ import annotations

from tools.location import GET_TOOL_NAME, REGISTER_TOOL_NAME
from tools.location_store import LocationStore
from tools.registry import build_default_registry


class _RecordingStore(LocationStore):
    """A LocationStore that doesn't touch GCS but records that load() ran."""

    def __init__(self) -> None:
        super().__init__(bucket_name=None)
        self.load_calls = 0

    async def load(self) -> None:
        self.load_calls += 1
        await super().load()


async def test_build_default_registry_loads_store_then_returns_registry():
    store = _RecordingStore()
    registry = await build_default_registry(location_store=store)

    assert store.load_calls == 1
    assert set(registry.tool_names) == {REGISTER_TOOL_NAME, GET_TOOL_NAME}


async def test_build_default_registry_falls_back_to_env_store(monkeypatch):
    """When no store is passed, the factory builds one from the env var."""
    monkeypatch.delenv("KIDS_TEACHER_LOCATION_BUCKET", raising=False)
    registry = await build_default_registry()
    assert set(registry.tool_names) == {REGISTER_TOOL_NAME, GET_TOOL_NAME}


async def test_build_default_registry_specs_contain_both_function_tools():
    registry = await build_default_registry(location_store=_RecordingStore())
    spec_names = {s["name"] for s in registry.specs()}
    assert spec_names == {REGISTER_TOOL_NAME, GET_TOOL_NAME}
