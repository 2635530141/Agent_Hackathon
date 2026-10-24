"""Immutable scheduling inputs; all absolute intervals are timezone aware.

Intervals are half open: a session ending at 14:00 does not conflict with one
starting at 14:00. Weekly slots are wall-clock times in the snapshot's IANA zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def aware_utc(value: datetime, label: str = "datetime") -> datetime:
    """Validate an absolute instant and return its UTC representation."""
    if not isinstance(value, datetime):
        raise ValueError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone aware")
    return value.astimezone(timezone.utc)


def _nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = aware_utc(self.start, "interval start")
        end = aware_utc(self.end, "interval end")
        # Comparing local datetimes with the same ZoneInfo can hide fold offsets.
        if start >= end:
            raise ValueError("interval start must be before end")


@dataclass(frozen=True, slots=True)
class WeeklySlot:
    weekday: int
    start: time
    end: time

    def __post_init__(self) -> None:
        if isinstance(self.weekday, bool) or not isinstance(self.weekday, int):
            raise ValueError("weekday must be an integer from 0 (Monday) to 6")
        if not 0 <= self.weekday <= 6:
            raise ValueError("weekday must be between 0 (Monday) and 6 (Sunday)")
        for label, value in (("start", self.start), ("end", self.end)):
            if not isinstance(value, time) or value.tzinfo is not None:
                raise ValueError(f"weekly {label} must be a local time without tzinfo")
        if self.start == self.end:
            raise ValueError("weekly start and end must differ; use explicit hours")


@dataclass(frozen=True, slots=True)
class BusyBlock:
    id: str
    interval: Interval
    source: str
    title: str = ""

    def __post_init__(self) -> None:
        _nonempty(self.id, "busy block id")
        _nonempty(self.source, "busy block source")
        if not isinstance(self.interval, Interval):
            raise ValueError("busy block interval must be an Interval")
        if not isinstance(self.title, str):
            raise ValueError("busy block title must be a string")


@dataclass(frozen=True, slots=True)
class Exam:
    id: str
    course_id: str
    interval: Interval
    priority_days: int = 7
    title: str = ""

    def __post_init__(self) -> None:
        _nonempty(self.id, "exam id")
        _nonempty(self.course_id, "exam course_id")
        if not isinstance(self.interval, Interval):
            raise ValueError("exam interval must be an Interval")
        if (isinstance(self.priority_days, bool)
                or not isinstance(self.priority_days, int)
                or self.priority_days < 0):
            raise ValueError("priority_days must be a non-negative integer")
        if not isinstance(self.title, str):
            raise ValueError("exam title must be a string")


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    owner_id: str
    timezone: str
    weekly: tuple[WeeklySlot, ...] = ()
    additions: tuple[Interval, ...] = ()
    removals: tuple[Interval, ...] = ()
    exams: tuple[Exam, ...] = ()
    calendar_busy: tuple[BusyBlock, ...] = ()
    calendar_connected: bool = False
    calendar_refreshed_at: datetime | None = None
    version: int = 1

    def __post_init__(self) -> None:
        _nonempty(self.owner_id, "owner_id")
        _nonempty(self.timezone, "timezone")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        for field, kind in (
            ("weekly", WeeklySlot), ("additions", Interval),
            ("removals", Interval), ("exams", Exam),
            ("calendar_busy", BusyBlock),
        ):
            try:
                values = tuple(getattr(self, field))
            except TypeError as exc:
                raise ValueError(f"{field} must be a collection of {kind.__name__}") from exc
            if any(not isinstance(value, kind) for value in values):
                raise ValueError(f"{field} must contain {kind.__name__} values")
            # Copy caller-owned mutable collections to preserve snapshot immutability.
            object.__setattr__(self, field, values)
        if not isinstance(self.calendar_connected, bool):
            raise ValueError("calendar_connected must be a boolean")
        if self.calendar_refreshed_at is not None:
            aware_utc(self.calendar_refreshed_at, "calendar_refreshed_at")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer")
