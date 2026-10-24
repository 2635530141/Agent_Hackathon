"""Translate legacy/form wall strings at the boundary; core instants are UTC."""
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .core.recurrence import local_candidates
from .core.schemas import Horizon, Interval


def display_zone() -> str:
    return os.getenv("STUDYMATE_TIMEZONE", "America/Los_Angeles")


def parse_instant(value: str | datetime, zone_name: str | None = None) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if parsed.tzinfo is None:
            options = local_candidates(parsed, ZoneInfo(zone_name or display_zone()))
            if len(options) != 1:
                raise ValueError("This local time is ambiguous or missing at DST. Choose another time or supply an explicit offset.")
            parsed = options[0]
        return parsed.astimezone(timezone.utc)
    except (TypeError, OverflowError):
        raise ValueError("Enter a valid date and time.") from None


def local_input(value: str | datetime) -> str:
    return parse_instant(value).astimezone(ZoneInfo(display_zone())).isoformat(timespec="minutes")[:16]


def local_iso(value: str | datetime) -> str:
    return parse_instant(value).astimezone(ZoneInfo(display_zone())).isoformat(timespec="minutes")


def slot(starts_at, ends_at) -> Interval:
    return Interval(start=parse_instant(starts_at), end=parse_instant(ends_at))


def horizon(starts_at, ends_at) -> Horizon:
    return Horizon(start=parse_instant(starts_at), end=parse_instant(ends_at))
