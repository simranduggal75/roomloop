"""Time handling.

Contract with the outside world (constraint C1): timestamps are *naive local ISO
strings* ("2026-07-02T09:00:00"), local to the room's office. Internally we keep
UTC epoch seconds for overlap checks, because naive local times are ambiguous
around DST changes and cannot be compared across offices.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc

OFFICE_TZ = {
    "Berlin": ZoneInfo("Europe/Berlin"),
    "Denver": ZoneInfo("America/Denver"),
}


class NonexistentLocalTime(ValueError):
    """The wall-clock time falls in a DST spring-forward gap and never happens."""


def parse_local(value: str, field: str) -> datetime:
    """Parse a naive local ISO string. Offsets / 'Z' are rejected on purpose:
    silently converting them is exactly how bookings end up an hour off."""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an ISO datetime like 2026-07-02T09:00:00")
    if dt.tzinfo is not None:
        raise ValueError(f"{field} must be a naive local time with no UTC offset or 'Z'")
    return dt.replace(microsecond=0)


def format_local(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def to_epoch(local: datetime, tz: ZoneInfo) -> int:
    """Naive local wall-clock time in `tz` -> UTC epoch seconds.

    Fall-back overlap (a time that happens twice): the first occurrence is used.
    Spring-forward gap (a time that never happens): NonexistentLocalTime.
    """
    aware = local.replace(tzinfo=tz)  # fold=0 -> first occurrence
    utc = aware.astimezone(UTC)
    if utc.astimezone(tz).replace(tzinfo=None) != local:
        raise NonexistentLocalTime(f"{format_local(local)} does not exist in {tz.key}")
    return int(utc.timestamp())
