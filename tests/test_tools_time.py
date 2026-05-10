"""Unit tests for :mod:`tools.time`."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List

import pytest

from tools.time import GET_CURRENT_TIME_TOOL_NAME, GetCurrentTimeTool


class _FakeStore:
    """Minimal :class:`LocationStore`-shaped fake."""

    def __init__(self, *, initial: str = "Seattle, WA 98177") -> None:
        self._location = initial
        self.set_calls: List[str] = []

    def get(self) -> str:
        return self._location

    async def set(self, location: str) -> None:
        self.set_calls.append(location)
        self._location = location


def _frozen_utc(dt: datetime):
    """Return a ``now_fn`` that always yields ``dt`` (must be tz-aware UTC)."""
    assert dt.tzinfo is timezone.utc
    return lambda: dt


# ---------------------------------------------------------------------------
# Spec / prompt block
# ---------------------------------------------------------------------------


def test_spec_shape():
    spec = GetCurrentTimeTool(_FakeStore()).spec()
    assert spec["type"] == "function"
    assert spec["name"] == GET_CURRENT_TIME_TOOL_NAME
    assert spec["parameters"]["required"] == []
    assert spec["parameters"]["properties"] == {}
    assert spec["parameters"]["additionalProperties"] is False


def test_prompt_block_is_empty():
    """The companion location tool's prompt block already advertises this
    tool — keep this one silent so we don't duplicate guidance."""
    assert GetCurrentTimeTool(_FakeStore()).prompt_block() == ""


# ---------------------------------------------------------------------------
# call() — happy path
# ---------------------------------------------------------------------------


async def test_call_returns_pacific_time_for_seattle():
    # 2026-05-09 23:30 UTC == 2026-05-09 16:30 PDT (DST in effect).
    now = datetime(2026, 5, 9, 23, 30, 0, tzinfo=timezone.utc)
    store = _FakeStore(initial="Seattle, WA 98177")
    tool = GetCurrentTimeTool(store, now_fn=_frozen_utc(now))

    result = await tool.call({})
    body = json.loads(result.to_payload())

    assert body["ok"] is True
    assert body["timezone"] == "America/Los_Angeles"
    assert body["location"] == "Seattle, WA 98177"
    assert body["time"] == "4:30 PM"
    assert body["date"] == "Saturday, May 9, 2026"
    assert body["iso"] == "2026-05-09T16:30:00-07:00"
    assert "Seattle" in body["detail"]


async def test_call_falls_back_to_utc_for_unknown_location():
    now = datetime(2026, 5, 9, 23, 30, 0, tzinfo=timezone.utc)
    store = _FakeStore(initial="Atlantis")
    tool = GetCurrentTimeTool(store, now_fn=_frozen_utc(now))

    body = json.loads((await tool.call({})).to_payload())
    assert body["timezone"] == "UTC"
    assert body["time"] == "11:30 PM"
    assert body["iso"] == "2026-05-09T23:30:00+00:00"


async def test_call_reflects_location_changes_between_calls():
    """Reads ``store.get()`` on every call, so a register between calls
    flips the timezone immediately — no caching."""
    now = datetime(2026, 5, 9, 23, 30, 0, tzinfo=timezone.utc)
    store = _FakeStore(initial="Seattle")
    tool = GetCurrentTimeTool(store, now_fn=_frozen_utc(now))

    first = json.loads((await tool.call({})).to_payload())
    assert first["timezone"] == "America/Los_Angeles"

    await store.set("Atlantis")
    second = json.loads((await tool.call({})).to_payload())
    assert second["timezone"] == "UTC"


async def test_call_default_now_fn_is_real_clock():
    """No ``now_fn`` injected → uses real wall clock (sanity-check that
    the default isn't wired to something stuck)."""
    before = datetime.now(timezone.utc)
    store = _FakeStore(initial="Seattle")
    body = json.loads((await GetCurrentTimeTool(store).call({})).to_payload())
    after = datetime.now(timezone.utc)
    iso = datetime.fromisoformat(body["iso"])
    # The reported time must lie within the bracket (allow second-level slack).
    assert before.replace(microsecond=0) <= iso.astimezone(timezone.utc) <= after
