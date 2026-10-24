"""Deterministic half-open UTC interval algebra, independent of storage/providers."""
from datetime import timedelta
from .schemas import Interval


def overlaps(a: Interval, b: Interval) -> bool:
    return a.start < b.end and b.start < a.end


def normalize(items: list[Interval]) -> list[Interval]:
    result: list[Interval] = []
    for item in sorted(items, key=lambda i: (i.start, i.end)):
        if result and item.start <= result[-1].end:
            last = result.pop()
            result.append(Interval(start=last.start, end=max(last.end, item.end)))
        else:
            result.append(Interval(start=item.start, end=item.end))
    return result


def clip(items: list[Interval], horizon: Interval) -> list[Interval]:
    return normalize([
        Interval(start=max(i.start, horizon.start), end=min(i.end, horizon.end))
        for i in items if overlaps(i, horizon)
    ])


def subtract(free: list[Interval], busy: list[Interval]) -> list[Interval]:
    result: list[Interval] = []
    blocks = normalize(busy)
    for interval in normalize(free):
        cursor = interval.start
        for block in blocks:
            if block.end <= cursor:
                continue
            if block.start >= interval.end:
                break
            if cursor < block.start:
                result.append(Interval(start=cursor, end=block.start))
            cursor = max(cursor, block.end)
            if cursor >= interval.end:
                break
        if cursor < interval.end:
            result.append(Interval(start=cursor, end=interval.end))
    return result


def contains(items: list[Interval], slot: Interval) -> bool:
    return any(i.start <= slot.start and i.end >= slot.end for i in normalize(items))


def alternative(items: list[Interval], slot: Interval) -> Interval | None:
    duration = slot.end - slot.start
    for free in normalize(items):
        start = max(free.start, slot.start)
        if start + duration <= free.end:
            return Interval(start=start, end=start + duration)
    return None


def duration_filter(items: list[Interval], duration: timedelta) -> list[Interval]:
    if duration <= timedelta(0):
        raise ValueError("duration must be positive")
    return [i for i in normalize(items) if i.end - i.start >= duration]
