"""How dates and times are shown everywhere (DEC-45 item 13): as QuickBooks shows them, MM/DD/YYYY, and times in the
office's own time zone with AM and PM. Stored values stay ISO dates and UTC times; only what people read changes."""

from __future__ import annotations

from datetime import date, datetime


def day(value: date | datetime | None) -> str:
    """11/06/2026. A time is first turned into the office's local date."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.astimezone() if value.tzinfo else value
    return f"{value.month:02d}/{value.day:02d}/{value.year:04d}"


def clock(value: datetime) -> str:
    """2:35 PM, in the office's local time."""
    local = value.astimezone() if value.tzinfo else value
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def moment(value: datetime | None) -> str:
    """11/06/2026 2:35 PM, in the office's local time."""
    if value is None:
        return ""
    return f"{day(value)} {clock(value)}"
