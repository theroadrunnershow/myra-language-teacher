"""Unit tests for :mod:`tools.base`."""

from __future__ import annotations

import asyncio
import json

import pytest

from tools.base import (
    DEFAULT_DISPATCH_TIMEOUT_S,
    ToolRegistry,
    ToolResult,
)


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _FakeTool:
    """Stand-in for a real :class:`Tool` implementation."""

    def __init__(
        self,
        name: str,
        *,
        result: ToolResult | None = None,
        prompt: str = "",
        delay: float = 0.0,
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self._result = result or ToolResult(ok=True, detail=f"{name} ok")
        self._prompt = prompt
        self._delay = delay
        self._raises = raises
        self.last_arguments: dict | None = None

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": f"{self.name} test tool",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        }

    def prompt_block(self) -> str:
        return self._prompt

    async def call(self, arguments):
        self.last_arguments = dict(arguments)
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        return self._result


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


def test_tool_result_payload_round_trip():
    payload = ToolResult(ok=True, detail="done").to_payload()
    assert json.loads(payload) == {"ok": True, "detail": "done"}


def test_tool_result_payload_includes_data():
    payload = ToolResult(
        ok=True,
        detail="ok",
        data={"location": "Seattle, WA 98177"},
    ).to_payload()
    assert json.loads(payload) == {
        "ok": True,
        "detail": "ok",
        "location": "Seattle, WA 98177",
    }


def test_default_timeout_is_3s():
    assert DEFAULT_DISPATCH_TIMEOUT_S == 3.0


# ---------------------------------------------------------------------------
# ToolRegistry — specs & prompt
# ---------------------------------------------------------------------------


def test_registry_specs_includes_each_tool():
    registry = ToolRegistry([_FakeTool("a"), _FakeTool("b")])
    names = {s["name"] for s in registry.specs()}
    assert names == {"a", "b"}


def test_registry_rejects_duplicate_names():
    with pytest.raises(ValueError):
        ToolRegistry([_FakeTool("a"), _FakeTool("a")])


def test_registry_tool_names_property():
    registry = ToolRegistry([_FakeTool("a"), _FakeTool("b")])
    assert set(registry.tool_names) == {"a", "b"}


def test_registry_prompt_block_concatenates_non_empty():
    registry = ToolRegistry(
        [
            _FakeTool("a", prompt="Use a for foo."),
            _FakeTool("b", prompt=""),
            _FakeTool("c", prompt="Use c for bar."),
        ]
    )
    block = registry.prompt_block()
    assert "Use a for foo." in block
    assert "Use c for bar." in block
    # Empty prompt from "b" must not introduce a blank section.
    assert block.count("\n\n") == 1


def test_registry_prompt_block_empty_when_all_blank():
    registry = ToolRegistry([_FakeTool("a", prompt=""), _FakeTool("b", prompt="")])
    assert registry.prompt_block() == ""


# ---------------------------------------------------------------------------
# ToolRegistry — dispatch
# ---------------------------------------------------------------------------


async def test_registry_dispatch_happy_path():
    tool = _FakeTool("hello", result=ToolResult(ok=True, detail="hi there"))
    registry = ToolRegistry([tool])
    result = await registry.dispatch("hello", {"who": "world"})
    assert result.ok is True
    assert result.detail == "hi there"
    assert tool.last_arguments == {"who": "world"}


async def test_registry_dispatch_accepts_json_string_arguments():
    tool = _FakeTool("a")
    registry = ToolRegistry([tool])
    result = await registry.dispatch("a", '{"x": 1}')
    assert result.ok is True
    assert tool.last_arguments == {"x": 1}


async def test_registry_dispatch_empty_string_args_treated_as_empty_dict():
    tool = _FakeTool("a")
    registry = ToolRegistry([tool])
    result = await registry.dispatch("a", "")
    assert result.ok is True
    assert tool.last_arguments == {}


async def test_registry_dispatch_unknown_tool():
    registry = ToolRegistry([_FakeTool("a")])
    result = await registry.dispatch("z", "{}")
    assert result.ok is False
    assert "unknown tool" in result.detail


async def test_registry_dispatch_malformed_json():
    registry = ToolRegistry([_FakeTool("a")])
    result = await registry.dispatch("a", "this is not json")
    assert result.ok is False
    assert "invalid arguments" in result.detail


async def test_registry_dispatch_non_dict_json():
    registry = ToolRegistry([_FakeTool("a")])
    result = await registry.dispatch("a", "[1, 2, 3]")
    assert result.ok is False
    assert "invalid arguments" in result.detail


async def test_registry_dispatch_traps_exceptions():
    tool = _FakeTool("boom", raises=RuntimeError("kaboom"))
    registry = ToolRegistry([tool])
    result = await registry.dispatch("boom", {})
    assert result.ok is False
    assert "kaboom" in result.detail


async def test_registry_dispatch_enforces_timeout():
    tool = _FakeTool("slow", delay=0.05)
    registry = ToolRegistry([tool], timeout_s=0.01)
    result = await registry.dispatch("slow", {})
    assert result.ok is False
    assert "timed out" in result.detail


async def test_registry_dispatch_returns_tool_result_with_data():
    tool = _FakeTool(
        "loc",
        result=ToolResult(ok=True, detail="ok", data={"location": "Seattle"}),
    )
    registry = ToolRegistry([tool])
    result = await registry.dispatch("loc", {})
    payload = json.loads(result.to_payload())
    assert payload == {"ok": True, "detail": "ok", "location": "Seattle"}
