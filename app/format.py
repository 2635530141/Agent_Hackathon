from datetime import datetime


def clock(value: datetime) -> str:
    hour = value.strftime("%I").lstrip("0") or "12"
    return f"{hour}:{value:%M} {value:%p}"


def pretty_point(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    return f"{parsed:%a} {parsed.day} {parsed:%b}, {clock(parsed)}"


def pretty_range(starts_at: str, ends_at: str) -> str:
    start = datetime.fromisoformat(starts_at)
    end = datetime.fromisoformat(ends_at)
    if start.date() == end.date():
        return f"{start:%a} {start.day} {start:%b}, {clock(start)}–{clock(end)}"
    return f"{pretty_point(starts_at)} – {pretty_point(ends_at)}"
