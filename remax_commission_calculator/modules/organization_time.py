"""
Organization-aware date/time handling (Phase 4B).

The database already stores naive ISO timestamps in UTC and every
organization already has a validated ``timezone`` setting, but nothing
used it for rendering yet. This module closes that gap:

    DB   -> naive ISO UTC ("2026-09-03T19:30:00")
    UI   -> organization timezone for both parsing and rendering

Business dates that are not points in time (``movement_date``,
``operation_date``) keep their current plain-date handling.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from modules.database.organization_settings_repository import (
    DEFAULT_TIMEZONE,
    get_organization_settings,
)


UTC = timezone.utc


def now_utc():
    """Timezone-aware current instant in UTC."""
    return datetime.now(UTC).replace(microsecond=0)


def now_utc_iso():
    return to_utc_iso(now_utc())


def get_timezone(timezone_name):
    try:
        return ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def organization_timezone(organization_id):
    """Resolve the organization timezone, falling back to the default."""
    if not organization_id:
        return get_timezone(DEFAULT_TIMEZONE)

    settings = get_organization_settings(organization_id)
    name = (settings or {}).get("timezone")

    return get_timezone(name)


def to_utc_iso(value):
    """Serialize an aware datetime as a naive UTC ISO string."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("naive_datetime_requires_timezone")

    return (
        value.astimezone(UTC)
        .replace(tzinfo=None, microsecond=0)
        .isoformat()
    )


def parse_utc_iso(value):
    """Read a stored timestamp back as an aware UTC datetime."""
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def to_local(value, tz):
    """Convert a stored UTC timestamp to the given timezone."""
    parsed = parse_utc_iso(value)

    return None if parsed is None else parsed.astimezone(tz)


def local_datetime_to_utc_iso(date_text, time_text, tz):
    """
    Build a UTC timestamp from a local date and time entered by a user.

    Raises ``ValueError`` when the input cannot be parsed, so callers
    can surface a form error instead of storing an ambiguous value.
    """
    date_text = (date_text or "").strip()
    time_text = (time_text or "").strip() or "09:00"

    if len(time_text) == 5:
        time_text = f"{time_text}:00"

    naive = datetime.fromisoformat(f"{date_text}T{time_text}")

    return to_utc_iso(naive.replace(tzinfo=tz))


def local_date_bounds_utc(local_date, tz, *, days=1):
    """
    UTC bounds ``[start, end)`` covering whole local calendar days.

    Both ends are built from local midnight so a DST change never
    shortens or stretches the window, which is what makes "what do I
    have today" correct even though storage is in UTC.
    """
    start = datetime.combine(
        local_date,
        datetime.min.time(),
        tzinfo=tz,
    )
    end = datetime.combine(
        local_date + timedelta(days=days),
        datetime.min.time(),
        tzinfo=tz,
    )

    return to_utc_iso(start), to_utc_iso(end)


def format_local_time(value, tz):
    local = to_local(value, tz)

    return "" if local is None else local.strftime("%H:%M")


def format_local_datetime(value, tz):
    local = to_local(value, tz)

    return "" if local is None else local.strftime("%d/%m/%Y %H:%M")


def format_local_date_iso(value, tz):
    local = to_local(value, tz)

    return "" if local is None else local.date().isoformat()


__all__ = [
    "UTC",
    "format_local_date_iso",
    "format_local_datetime",
    "format_local_time",
    "get_timezone",
    "local_date_bounds_utc",
    "local_datetime_to_utc_iso",
    "now_utc",
    "now_utc_iso",
    "organization_timezone",
    "parse_utc_iso",
    "to_local",
    "to_utc_iso",
]
