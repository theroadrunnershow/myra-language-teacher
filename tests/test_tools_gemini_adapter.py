"""Unit tests for :mod:`tools.gemini_adapter`.

Uses a tiny fake ``types`` module that just records the kwargs each
constructor receives — same idea as ``_FakeTypes`` in
``tests/test_kids_teacher_gemini_backend.py``, kept local so the
adapter tests have no implicit dependency on that file.
"""

from __future__ import annotations

from typing import Any

from tools.gemini_adapter import build_gemini_tools


# ---------------------------------------------------------------------------
# Fake Gemini types module
# ---------------------------------------------------------------------------


class _Record:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeTypes:
    Tool = _Record
    FunctionDeclaration = _Record


# ---------------------------------------------------------------------------
# build_gemini_tools — happy path translations
# ---------------------------------------------------------------------------


def _required_only_spec() -> dict:
    return {
        "type": "function",
        "name": "register_current_location",
        "description": "Save the kid's current city.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name.",
                }
            },
            "required": ["location"],
            "additionalProperties": False,
        },
    }


def test_build_gemini_tools_translates_required_only_spec():
    [tool] = build_gemini_tools(_FakeTypes, [_required_only_spec()])
    decl = tool.kwargs["function_declarations"][0]
    assert decl.kwargs["name"] == "register_current_location"
    assert decl.kwargs["description"] == "Save the kid's current city."
    assert decl.kwargs["parameters_json_schema"] == {
        "type": "object",
        "properties": {"location": {"type": "string", "description": "City name."}},
        "required": ["location"],
        "additionalProperties": False,
    }


def test_build_gemini_tools_translates_optional_property():
    spec = {
        "type": "function",
        "name": "play_gesture",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "intensity": {"type": "number"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    }
    [tool] = build_gemini_tools(_FakeTypes, [spec])
    decl = tool.kwargs["function_declarations"][0]
    schema = decl.kwargs["parameters_json_schema"]
    assert "intensity" in schema["properties"]
    assert schema["required"] == ["name"]


def test_build_gemini_tools_translates_enum_property():
    spec = {
        "type": "function",
        "name": "play_gesture",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": ["nod_encourage", "head_tilt_curious"],
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    }
    [tool] = build_gemini_tools(_FakeTypes, [spec])
    decl = tool.kwargs["function_declarations"][0]
    schema = decl.kwargs["parameters_json_schema"]
    assert schema["properties"]["name"]["enum"] == [
        "nod_encourage",
        "head_tilt_curious",
    ]


def test_build_gemini_tools_emits_one_tool_per_spec():
    a = _required_only_spec()
    b = {
        "type": "function",
        "name": "get_current_location",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }
    tools = build_gemini_tools(_FakeTypes, [a, b])
    names = [t.kwargs["function_declarations"][0].kwargs["name"] for t in tools]
    assert names == ["register_current_location", "get_current_location"]


def test_build_gemini_tools_omits_description_when_absent():
    spec = {
        "type": "function",
        "name": "no_desc",
        "parameters": {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
            "additionalProperties": False,
        },
    }
    [tool] = build_gemini_tools(_FakeTypes, [spec])
    decl_kwargs = tool.kwargs["function_declarations"][0].kwargs
    assert "description" not in decl_kwargs


# ---------------------------------------------------------------------------
# build_gemini_tools — input edge cases
# ---------------------------------------------------------------------------


def test_build_gemini_tools_returns_empty_for_none():
    assert build_gemini_tools(_FakeTypes, None) == []


def test_build_gemini_tools_returns_empty_for_empty_list():
    assert build_gemini_tools(_FakeTypes, []) == []


def test_build_gemini_tools_skips_stub_specs_without_parameters():
    """Profile.allowed_tools loop produces ``{type, name}`` stubs.

    The adapter must skip them quietly — explicit memory/face builders
    own those names and produce real declarations.
    """
    stub = {"type": "function", "name": "set_about_kid"}
    real = _required_only_spec()
    tools = build_gemini_tools(_FakeTypes, [stub, real])
    assert len(tools) == 1
    assert (
        tools[0].kwargs["function_declarations"][0].kwargs["name"]
        == "register_current_location"
    )


def test_build_gemini_tools_skips_stub_with_empty_parameters_dict():
    stub = {"type": "function", "name": "x", "parameters": {}}
    assert build_gemini_tools(_FakeTypes, [stub]) == []


def test_build_gemini_tools_skips_non_function_type():
    spec = {"type": "retrieval", "name": "future_retrieval_tool"}
    assert build_gemini_tools(_FakeTypes, [spec]) == []


def test_build_gemini_tools_drops_malformed_spec_without_name(caplog):
    bad = {"type": "function", "parameters": {"type": "object", "properties": {}}}
    good = _required_only_spec()
    with caplog.at_level("WARNING"):
        tools = build_gemini_tools(_FakeTypes, [bad, good])
    assert len(tools) == 1
    assert any("dropping malformed spec" in r.message for r in caplog.records)


def test_build_gemini_tools_drops_non_mapping_spec(caplog):
    with caplog.at_level("WARNING"):
        tools = build_gemini_tools(_FakeTypes, ["not a dict", _required_only_spec()])
    assert len(tools) == 1


def test_build_gemini_tools_does_not_crash_on_iterable_other_than_list():
    # Generators are valid input — reuse-iterable contract.
    def gen():
        yield _required_only_spec()
    tools = build_gemini_tools(_FakeTypes, list(gen()))
    assert len(tools) == 1


