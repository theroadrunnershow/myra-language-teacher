"""Unit tests for :mod:`tools.timezone_lookup`."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from tools.timezone_lookup import (
    DEFAULT_TZ_NAME,
    timezone_for_location,
    timezone_name_for_location,
)


# ---------------------------------------------------------------------------
# timezone_name_for_location
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "Seattle",
        "Seattle, WA",
        "Seattle, WA 98177",
        "  Seattle  ",
        "seattle",
        "SEATTLE",
        "Seattle, United States",
    ],
)
def test_seattle_variants_resolve_to_pacific(location: str):
    assert timezone_name_for_location(location) == "America/Los_Angeles"


@pytest.mark.parametrize(
    "location",
    [
        "Bangalore, India",
        "Brooklyn, NY",
        "Tokyo",
        "somewhere weird",
    ],
)
def test_unknown_locations_fall_back_to_utc(location: str):
    assert timezone_name_for_location(location) == DEFAULT_TZ_NAME


@pytest.mark.parametrize("location", ["", "   ", ","])
def test_empty_or_blank_locations_fall_back_to_utc(location: str):
    assert timezone_name_for_location(location) == DEFAULT_TZ_NAME


def test_default_tz_name_is_utc():
    assert DEFAULT_TZ_NAME == "UTC"


# ---------------------------------------------------------------------------
# timezone_for_location
# ---------------------------------------------------------------------------


def test_timezone_for_location_returns_zoneinfo_for_known_city():
    tz = timezone_for_location("Seattle, WA 98177")
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "America/Los_Angeles"


def test_timezone_for_location_returns_utc_zoneinfo_for_unknown():
    tz = timezone_for_location("nowhereville")
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "UTC"
