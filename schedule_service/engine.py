"""Deterministic schedule math. No model, network calls, or calendar writes.

All calculations and returned availability use UTC, with half-open intervals.
Busy intervals always win over additions. Event titles only leave this module
through owner_conflicts; callers must restrict that function to the owner.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from math import isfinite
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from .domain import BusyBlock, Interval, ScheduleSnapshot, aware_utc

UTC = timezone.utc


def _utc_interval(interval: Interval) -> Interval:
    return Interval(aware_utc(interval.start), aware_utc(interval.end))


def _overlaps(left: Interval, right: Interval) -> bool:
    return (aware_utc(left.start) < aware_utc(right.end)
            and aware_utc(right.start) < aware_utc(left.end))


def _clip(interval: Interval, window: Interval) -> Interval | None:
    start = max(aware_utc(interval.start), aware_utc(window.start))
    end = min(aware_utc(interval.end), aware_utc(window.end))
    return Interval(start, end) if start < end else None


def _merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Union overlapping or directly adjacent free intervals."""
    ordered = sorted((_utc_interval(value) for value in intervals), key=lambda value: value.start)
    result: list[Interval] = []
    for value in ordered:
        if result and value.start <= result[-1].end:
            result[-1] = Interval(result[-1].start, max(result[-1].end, value.end))
        else:
            result.append(value)
    return result


def _local_time(value: datetime, zone: ZoneInfo) -> datetime:
    """Resolve a wall time only if it names exactly one instant.

    Attaching ZoneInfo alone silently accepts nonexistent and ambiguous times.
    A round trip detects gaps; differing valid fold offsets detect repetitions.
    """
    first = value.replace(tzinfo=zone, fold=0)
    second = value.replace(tzinfo=zone, fold=1)
    valid_first = first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value
    valid_second = second.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value
    if not valid_first and not valid_second:
        raise ValueError(
            f"Nonexistent local time {value.isoformat()} in {zone.key}; "
            "use an aware one-off slot with an explicit UTC offset"
        )
    if valid_first and valid_second and first.utcoffset() != second.utcoffset():
        raise ValueError(
            f"Ambiguous local time {value.isoformat()} in {zone.key}; "
            "use an aware one-off slot with an explicit UTC offset"
        )
    return first if valid_first else second


def _weekly_intervals(snapshot: ScheduleSnapshot, window: Interval) -> Iterable[Interval]:
    zone = ZoneInfo(snapshot.timezone)
    # The preceding day's overnight slot can extend into the requested window.
    day = window.start.astimezone(zone).date() - timedelta(days=1)
    last_day = window.end.astimezone(zone).date()
    while day <= last_day:
        for slot in snapshot.weekly:
            if slot.weekday != day.weekday():
                continue
            start_wall = datetime.combine(day, slot.start)
            end_day = day + timedelta(days=1) if slot.end < slot.start else day
            end_wall = datetime.combine(end_day, slot.end)
            # Do not reject a DST occurrence entirely outside this query. These
            # broad bounds include both possible offsets before strict resolution.
            earliest = min(start_wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
                           for fold in (0, 1))
            latest = max(end_wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
                         for fold in (0, 1))
            if earliest >= window.end or latest <= window.start:
                continue
            yield Interval(_local_time(start_wall, zone), _local_time(end_wall, zone))
        day += timedelta(days=1)


def compute_availability(snapshot: ScheduleSnapshot, window: Interval) -> list[Interval]:
    """Expand weekly/additional hours, then subtract all hard busy intervals.

    One-off additions extend free hours, but cannot override removals, exams or
    imported calendar events. Every returned interval is continuous and in UTC.
    Calendar blocks remain constraints even if a connector is now disconnected.
    """
    window = _utc_interval(window)
    free = _merge(
        clipped for value in (*_weekly_intervals(snapshot, window), *snapshot.additions)
        if (clipped := _clip(value, window)) is not None
    )
    busy = _merge(
        clipped for value in (
            *snapshot.removals,
            *(exam.interval for exam in snapshot.exams),
            *(block.interval for block in snapshot.calendar_busy),
        ) if (clipped := _clip(value, window)) is not None
    )
    result: list[Interval] = []
    for available in free:
        cursor = available.start
        for blocked in busy:
            if blocked.end <= cursor:
                continue
            if blocked.start >= available.end:
                break
            if blocked.start > cursor:
                result.append(Interval(cursor, blocked.start))
            cursor = max(cursor, blocked.end)
            if cursor >= available.end:
                break
        if cursor < available.end:
            result.append(Interval(cursor, available.end))
    return result


def owner_conflicts(snapshot: ScheduleSnapshot, requested: Interval) -> list[BusyBlock]:
    """Return private original conflict details. Only expose to the owner.

    Returning an original block (not a clipped one) lets the owner understand
    the full conflict. Touching endpoints are not overlaps.
    """
    candidates = [
        *(BusyBlock(f"manual-removal-{index}", interval, "manual", "One-off unavailable")
          for index, interval in enumerate(snapshot.removals)),
        *(BusyBlock(exam.id, exam.interval, "exam", exam.title or exam.course_id)
          for exam in snapshot.exams),
        *snapshot.calendar_busy,
    ]
    return sorted(
        (block for block in candidates if _overlaps(block.interval, requested)),
        key=lambda block: (aware_utc(block.interval.start), aware_utc(block.interval.end),
                           block.source, block.id),
    )


def priority_windows(snapshot: ScheduleSnapshot, window: Interval) -> list[dict]:
    """Return soft exam priority periods, never extra hard busy periods.

    Each period starts at local midnight N calendar dates before the exam's
    local date, and ends at the exam instant. This honors local days over DST.
    A rare ambiguous/nonexistent local midnight fails explicitly as for slots.
    """
    zone = ZoneInfo(snapshot.timezone)
    result: list[dict] = []
    for exam in snapshot.exams:
        exam_start = aware_utc(exam.interval.start)
        local_date = exam_start.astimezone(zone).date() - timedelta(days=exam.priority_days)
        priority_start = _local_time(datetime.combine(local_date, time.min), zone)
        if aware_utc(priority_start) >= exam_start:
            continue
        clipped = _clip(Interval(priority_start, exam_start), window)
        if clipped is not None:
            result.append({"exam_id": exam.id, "course_id": exam.course_id,
                           "start": clipped.start, "end": clipped.end})
    return sorted(result, key=lambda value: (value["start"], value["exam_id"]))


def _minutes(value: int | float, label: str) -> timedelta:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or value <= 0):
        raise ValueError(f"{label} must be a positive finite number of minutes")
    try:
        delta = timedelta(minutes=value)
    except OverflowError as exc:
        raise ValueError(f"{label} is too large") from exc
    if delta <= timedelta(0):
        raise ValueError(f"{label} must be at least one microsecond")
    return delta


def intersect_availability(
    snapshots: Sequence[ScheduleSnapshot], window: Interval, min_minutes: int | float,
) -> list[Interval]:
    """Return continuous shared free intervals meeting the minimum duration."""
    minimum = _minutes(min_minutes, "min_minutes")
    window = _utc_interval(window)
    common: list[Interval] | None = None
    for snapshot in snapshots:
        available = compute_availability(snapshot, window)
        if common is None:
            common = available
            continue
        intersections: list[Interval] = []
        left = right = 0
        while left < len(common) and right < len(available):
            overlap = _clip(common[left], available[right])
            if overlap is not None:
                intersections.append(overlap)
            if common[left].end <= available[right].end:
                left += 1
            else:
                right += 1
        common = intersections
        if not common:
            break
    return [value for value in (common or []) if value.end - value.start >= minimum]


def next_available(
    snapshots: Sequence[ScheduleSnapshot], window: Interval,
    duration_minutes: int | float, after: datetime | None = None,
) -> Interval | None:
    """Choose the earliest complete session at or after ``after`` (inclusive)."""
    duration = _minutes(duration_minutes, "duration_minutes")
    earliest = aware_utc(after, "after") if after is not None else aware_utc(window.start)
    for available in intersect_availability(snapshots, window, duration_minutes):
        start = max(earliest, available.start)
        if available.end - start >= duration:
            return Interval(start, start + duration)
    return None
