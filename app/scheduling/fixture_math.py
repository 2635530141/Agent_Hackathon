"""Pure adapter for David's explicit Person test inputs; same interval primitives."""
from .core.intervals import clip, subtract
from .core.recurrence import expand
from .core.schemas import Horizon, Interval, WeeklyRule
from .time import display_zone, parse_instant


def free_time(weekly, blocks, exams, start, end):
    span = Horizon(start=parse_instant(start), end=parse_instant(end))
    days = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    rules = [WeeklyRule(id=f"fixture-{d}", owner_id="fixture", weekday=days.index(d),
        start_local=s, end_local=e, timezone=display_zone()) for d, (s,e) in weekly.items() if s and e]
    opened, _ = expand(rules, span)
    additions = [Interval(start=parse_instant(b.starts_at), end=parse_instant(b.ends_at)) for b in blocks if b.kind == "free"]
    busy = [Interval(start=parse_instant(b.starts_at), end=parse_instant(b.ends_at))
            for b in blocks if b.source in ("exam","calendar","exception") and b.kind != "free"]
    busy += [Interval(start=parse_instant(e.starts_at), end=parse_instant(e.ends_at)) for e in exams]
    return [(i.start, i.end) for i in clip(subtract(opened+additions, busy), span)]
