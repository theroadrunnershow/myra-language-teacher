"""Unit tests for :mod:`tools.hooks` — the mixin in isolation.

Bridge-level integration tests live in ``test_kids_teacher_robot_bridge``
once Step 4 mounts the registry there.
"""

from __future__ import annotations

import json

from tools.base import ToolRegistry, ToolResult
from tools.hooks import ToolsHooksMixin


class _FakeTool:
    def __init__(self, name: str, *, prompt: str = "", reply: str = "ok") -> None:
        self.name = name
        self._prompt = prompt
        self._reply = reply

    def spec(self) -> dict:
        return {"type": "function", "name": self.name, "parameters": {}}

    def prompt_block(self) -> str:
        return self._prompt

    async def call(self, arguments):
        return ToolResult(ok=True, detail=self._reply)


class _Hooks(ToolsHooksMixin):
    """Concrete subclass — the mixin is meant to be composed."""


def test_mixin_returns_specs_when_registry_present():
    registry = ToolRegistry([_FakeTool("a"), _FakeTool("b")])
    hooks = _Hooks(tool_registry=registry)
    names = {s["name"] for s in hooks.additional_tool_specs()}
    assert names == {"a", "b"}


def test_mixin_returns_empty_specs_when_registry_absent():
    hooks = _Hooks()
    assert hooks.additional_tool_specs() == []
    assert hooks.additional_instructions() == ""


def test_mixin_concatenates_prompt_blocks():
    registry = ToolRegistry(
        [_FakeTool("a", prompt="Use a."), _FakeTool("b", prompt="Use b.")]
    )
    hooks = _Hooks(tool_registry=registry)
    instructions = hooks.additional_instructions()
    assert "Use a." in instructions
    assert "Use b." in instructions


async def test_mixin_handle_tool_call_returns_payload():
    registry = ToolRegistry([_FakeTool("a", reply="hello")])
    hooks = _Hooks(tool_registry=registry)
    out = await hooks.handle_tool_call("call-1", "a", "{}")
    assert out is not None
    assert json.loads(out) == {"ok": True, "detail": "hello"}


async def test_mixin_handle_tool_call_returns_none_when_registry_absent():
    hooks = _Hooks()
    out = await hooks.handle_tool_call("call-1", "anything", "{}")
    assert out is None


async def test_mixin_handle_tool_call_unknown_tool_returns_error_payload():
    registry = ToolRegistry([_FakeTool("a")])
    hooks = _Hooks(tool_registry=registry)
    out = await hooks.handle_tool_call("call-1", "missing", "{}")
    assert out is not None
    body = json.loads(out)
    assert body["ok"] is False
    assert "unknown tool" in body["detail"]
