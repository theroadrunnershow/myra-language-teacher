"""Unit tests for :mod:`tools.web_lookup`."""

from __future__ import annotations

import json
from typing import Any, List

import pytest

from tools import web_lookup as web_lookup_module
from tools.web_lookup import (
    KID_FRIENDLY_FAILURE,
    MAX_EXTRACT_CHARS,
    WEB_LOOKUP_TOOL_NAME,
    WIKIPEDIA_SUMMARY_URL,
    WebLookupTool,
)


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
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: List[dict] = []

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self._response


# ---------------------------------------------------------------------------
# Spec / prompt block
# ---------------------------------------------------------------------------


def test_spec_shape():
    spec = WebLookupTool().spec()
    assert spec["type"] == "function"
    assert spec["name"] == WEB_LOOKUP_TOOL_NAME
    assert spec["parameters"]["required"] == ["topic"]
    assert spec["parameters"]["properties"]["topic"]["type"] == "string"
    assert spec["parameters"]["additionalProperties"] is False


def test_prompt_block_is_empty():
    assert WebLookupTool().prompt_block() == ""


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


async def test_call_rejects_missing_topic():
    result = await WebLookupTool().call({})
    assert result.ok is False
    assert "non-empty" in result.detail


async def test_call_rejects_blank_topic():
    result = await WebLookupTool().call({"topic": "   "})
    assert result.ok is False


async def test_call_rejects_non_string_topic():
    result = await WebLookupTool().call({"topic": 42})
    assert result.ok is False


# ---------------------------------------------------------------------------
# Successful lookup
# ---------------------------------------------------------------------------


async def test_call_returns_extract_and_url_encodes_topic(monkeypatch):
    fake_get = _RecordingGet(
        _FakeResponse(body={"extract": "An octopus is a sea creature."})
    )
    monkeypatch.setattr(web_lookup_module.requests, "get", fake_get)

    result = await WebLookupTool().call({"topic": "  octopus "})
    assert result.ok is True
    body = json.loads(result.to_payload())
    assert body["detail"] == "An octopus is a sea creature."
    assert body["topic"] == "octopus"
    assert body["source"] == "wikipedia"


async def test_call_replaces_spaces_with_underscores_and_quotes_unicode(
    monkeypatch,
):
    fake_get = _RecordingGet(_FakeResponse(body={"extract": "ok"}))
    monkeypatch.setattr(web_lookup_module.requests, "get", fake_get)

    await WebLookupTool().call({"topic": "Mount Everest"})
    expected_url = WIKIPEDIA_SUMMARY_URL.format(title="Mount_Everest")
    assert fake_get.calls[0]["url"] == expected_url
    assert fake_get.calls[0]["headers"]["User-Agent"]


async def test_call_truncates_long_extracts(monkeypatch):
    long_extract = "A" * (MAX_EXTRACT_CHARS + 200)
    monkeypatch.setattr(
        web_lookup_module.requests, "get",
        _RecordingGet(_FakeResponse(body={"extract": long_extract})),
    )
    result = await WebLookupTool().call({"topic": "long"})
    assert result.ok is True
    assert len(result.detail) == MAX_EXTRACT_CHARS


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


async def test_call_returns_specific_message_when_extract_missing(monkeypatch):
    monkeypatch.setattr(
        web_lookup_module.requests, "get",
        _RecordingGet(_FakeResponse(body={"type": "disambiguation"})),
    )
    result = await WebLookupTool().call({"topic": "Mercury"})
    assert result.ok is False
    assert "Mercury" in result.detail


async def test_call_returns_kid_friendly_on_http_error(monkeypatch, caplog):
    monkeypatch.setattr(
        web_lookup_module.requests, "get",
        _RecordingGet(_FakeResponse(ok=False, status_code=503)),
    )
    with caplog.at_level("ERROR"):
        result = await WebLookupTool().call({"topic": "octopus"})
    assert result.ok is False
    assert result.detail == KID_FRIENDLY_FAILURE
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "octopus" in log_text
    assert "503" in log_text


async def test_call_returns_kid_friendly_on_network_exception(monkeypatch, caplog):
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("dns failure")

    monkeypatch.setattr(web_lookup_module.requests, "get", boom)
    with caplog.at_level("ERROR"):
        result = await WebLookupTool().call({"topic": "octopus"})
    assert result.ok is False
    assert result.detail == KID_FRIENDLY_FAILURE
    assert any("API error" in r.getMessage() for r in caplog.records)
