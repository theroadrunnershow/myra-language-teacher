"""Map a registered location string to an IANA timezone.

V1 ships a tiny lookup table for the cities we actually serve — the
home location ("Seattle") is the only one that has to work today.
Unknown locations fall back to UTC so the model still gets a clock,
just not the kid's local one.

When we eventually need arbitrary cities, swap the implementation
behind :func:`timezone_name_for_location` for ``timezonefinder`` +
``geopy`` (or similar) without changing callers.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

DEFAULT_TZ_NAME = "UTC"

# City name (lowercased, no qualifiers) → IANA timezone. The lookup
# matches the first comma-separated segment of the registered string,
# so "Seattle", "Seattle, WA", and "Seattle, WA 98177" all resolve.
_CITY_TO_TZ: dict[str, str] = {
    "seattle": "America/Los_Angeles",
}


def timezone_name_for_location(location: str) -> str:
    """Return the IANA timezone name for ``location`` (or ``UTC`` fallback)."""
    if not location:
        return DEFAULT_TZ_NAME
    city = location.split(",", 1)[0].strip().lower()
    if not city:
        return DEFAULT_TZ_NAME
    return _CITY_TO_TZ.get(city, DEFAULT_TZ_NAME)


def timezone_for_location(location: str) -> ZoneInfo:
    """Return a :class:`ZoneInfo` for ``location`` (or UTC fallback)."""
    return ZoneInfo(timezone_name_for_location(location))


__all__ = [
    "DEFAULT_TZ_NAME",
    "timezone_for_location",
    "timezone_name_for_location",
]
