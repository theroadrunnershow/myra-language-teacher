"""Unit tests for :mod:`tools.location`."""

from __future__ import annotations

import json
from typing import List, Tuple

import pytest

from tools.location import (
    GET_TOOL_NAME,
    REGISTER_TOOL_NAME,
    GetCurrentLocationTool,
    RegisterCurrentLocationTool,
)


class _FakeStore:
    """Minimal :class:`LocationStore`-shaped fake."""

    def __init__(self, *, initial: str = "Seattle, WA 98177") -> None:
        self._location = initial
        self.set_calls: List[str] = []
        self.set_raises: Exception | None = None

    def get(self) -> str:
        return self._location

    async def set(self, location: str) -> None:
        self.set_calls.append(location)
        if self.set_raises is not None:
            raise self.set_raises
        self._location = location


# ---------------------------------------------------------------------------
# Spec shape
# ---------------------------------------------------------------------------


def test_register_tool_spec_shape():
    spec = RegisterCurrentLocationTool(_FakeStore()).spec()
    assert spec["type"] == "function"
    assert spec["name"] == REGISTER_TOOL_NAME
    assert spec["parameters"]["required"] == ["location"]
    assert spec["parameters"]["properties"]["location"]["type"] == "string"
    assert spec["parameters"]["additionalProperties"] is False


def test_get_tool_spec_has_no_required_arguments():
    spec = GetCurrentLocationTool(_FakeStore()).spec()
    assert spec["name"] == GET_TOOL_NAME
    assert spec["parameters"]["required"] == []
    assert spec["parameters"]["properties"] == {}


def test_register_tool_has_no_prompt_block():
    """Register's behaviour is documented in the kids-profile instructions
    file (Step 6); the tool itself contributes no extra prompt context."""
    assert RegisterCurrentLocationTool(_FakeStore()).prompt_block() == ""


def test_get_tool_prompt_block_includes_current_location():
    """The Get tool's prompt block is read at session-payload build time
    and injects the registered location into the system prompt — so the
    model can answer 'where do I live?' without paying a tool round-trip."""
    store = _FakeStore(initial="Seattle, WA 98177")
    block = GetCurrentLocationTool(store).prompt_block()
    assert "Seattle, WA 98177" in block
    assert "Current location" in block


def test_get_tool_prompt_block_advertises_get_current_time_tool():
    """Time questions need to call ``get_current_time``; the location
    tool's prompt block is the one place we tell the model so."""
    block = GetCurrentLocationTool(_FakeStore()).prompt_block()
    assert "get_current_time" in block


def test_get_tool_prompt_block_reflects_registered_value():
    """After register, prompt_block reads the new location.

    Practically the system prompt is fixed at connect time, but the
    next session's prompt_block sees the updated value — that's the
    'persists across sessions' contract.
    """
    store = _FakeStore(initial="Seattle")
    # Simulate a register happening between sessions.
    store._location = "Brooklyn, NY"
    block = GetCurrentLocationTool(store).prompt_block()
    assert "Brooklyn, NY" in block


# ---------------------------------------------------------------------------
# RegisterCurrentLocationTool — call behaviour
# ---------------------------------------------------------------------------


async def test_register_tool_persists_value_and_returns_payload():
    store = _FakeStore(initial="Seattle, WA 98177")
    tool = RegisterCurrentLocationTool(store)
    result = await tool.call({"location": "Brooklyn, NY"})
    assert result.ok is True
    assert store.set_calls == ["Brooklyn, NY"]
    body = json.loads(result.to_payload())
    assert body == {
        "ok": True,
        "detail": "location saved",
        "location": "Brooklyn, NY",
    }


async def test_register_tool_rejects_missing_location():
    store = _FakeStore()
    tool = RegisterCurrentLocationTool(store)
    result = await tool.call({})
    assert result.ok is False
    assert "non-empty" in result.detail
    assert store.set_calls == []


async def test_register_tool_rejects_blank_location():
    store = _FakeStore()
    result = await RegisterCurrentLocationTool(store).call({"location": "   "})
    assert result.ok is False
    assert store.set_calls == []


async def test_register_tool_rejects_non_string_location():
    store = _FakeStore()
    result = await RegisterCurrentLocationTool(store).call({"location": 42})
    assert result.ok is False
    assert store.set_calls == []


async def test_register_tool_translates_value_error_from_store():
    store = _FakeStore()
    store.set_raises = ValueError("location must be non-empty")
    tool = RegisterCurrentLocationTool(store)
    result = await tool.call({"location": "Bangalore"})
    assert result.ok is False
    assert "non-empty" in result.detail


async def test_register_tool_translates_generic_exception_from_store():
    store = _FakeStore()
    store.set_raises = RuntimeError("network down")
    tool = RegisterCurrentLocationTool(store)
    result = await tool.call({"location": "Bangalore"})
    assert result.ok is False
    # Generic message — never leak raw exception text to the kid.
    assert "couldn't save" in result.detail


# ---------------------------------------------------------------------------
# GetCurrentLocationTool — call behaviour
# ---------------------------------------------------------------------------


async def test_get_tool_returns_current_location():
    store = _FakeStore(initial="Bangalore, India")
    result = await GetCurrentLocationTool(store).call({})
    assert result.ok is True
    body = json.loads(result.to_payload())
    assert body == {
        "ok": True,
        "detail": "current location",
        "location": "Bangalore, India",
    }


async def test_get_tool_reflects_recent_register():
    store = _FakeStore(initial="Seattle")
    register = RegisterCurrentLocationTool(store)
    get_tool = GetCurrentLocationTool(store)
    await register.call({"location": "Brooklyn"})
    result = await get_tool.call({})
    assert json.loads(result.to_payload())["location"] == "Brooklyn"
