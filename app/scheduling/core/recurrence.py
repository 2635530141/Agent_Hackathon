"""Expand each local occurrence; exclude ambiguous/nonexistent endpoints explicitly."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .intervals import clip
from .schemas import Horizon, Interval, WeeklyRule


def local_candidates(wall: datetime, zone: ZoneInfo) -> list[datetime]:
    if wall.tzinfo is not None:
        raise ValueError("expected a naive wall datetime")
    instants = set()
    for fold in (0, 1):
        instant = wall.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None) == wall:
            instants.add(instant)
    return sorted(instants)


def expand(rules: list[WeeklyRule], horizon: Horizon) -> tuple[list[Interval], list[str]]:
    intervals = []
    warnings = []
    for rule in sorted(rules, key=lambda r: r.id):
        zone = ZoneInfo(rule.timezone)
        day = horizon.start.astimezone(zone).date() - timedelta(days=1)
        last = horizon.end.astimezone(zone).date()
        while day <= last:
            if (day.weekday() == rule.weekday
                and (rule.valid_from is None or day >= rule.valid_from)
                and (rule.valid_until is None or day <= rule.valid_until)):
                end_day = day + timedelta(days=int(rule.ends_next_day))
                starts = local_candidates(datetime.combine(day, rule.start_local), zone)
                ends = local_candidates(datetime.combine(end_day, rule.end_local), zone)
                if len(starts) != 1 or len(ends) != 1:
                    warnings.append(f"DST_OCCURRENCE_EXCLUDED:{rule.id}:{day.isoformat()}")
                elif starts[0] < ends[0]:
                    intervals.append(Interval(start=starts[0], end=ends[0]))
                else:
                    warnings.append(f"INVALID_OCCURRENCE_EXCLUDED:{rule.id}:{day.isoformat()}")
            day += timedelta(days=1)
    return clip(intervals, horizon), warnings
