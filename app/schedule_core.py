"""Shulun's schedule track, adapted to the team's shared contracts and store.

The skeleton store remains a synthetic in-memory demo. Scheduling
uses the tested deterministic engine; Google is never faked as connected.
"""
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app import store
from app.contracts import BusyInterval, Exam, MatchBusyInterval
from schedule_service.domain import BusyBlock, Exam as ScheduleExam, Interval, ScheduleSnapshot, WeeklySlot
from schedule_service.engine import compute_availability, next_available, owner_conflicts

ZONE = ZoneInfo("America/Los_Angeles")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABELS = dict(zip(DAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")))
BLOCKING_SOURCES = ("exam", "calendar", "exception")


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("Enter a valid date and time.") from None
    if parsed.tzinfo is not None:
        return parsed.astimezone(ZONE)
    first, second = (parsed.replace(tzinfo=ZONE, fold=fold) for fold in (0, 1))
    valid = [x for x in (first, second) if x.astimezone(timezone.utc).astimezone(ZONE).replace(tzinfo=None) == parsed]
    if not valid or len({x.utcoffset() for x in valid}) > 1:
        raise ValueError("This local time is missing or repeated during daylight saving. Use a time with an explicit UTC offset.")
    return valid[0]


def _parse(value: str) -> datetime:
    return parse_time(value)


def _window(starts_at: str, ends_at: str) -> Interval:
    interval = Interval(_parse(starts_at), _parse(ends_at))
    if interval.end.astimezone(timezone.utc) - interval.start.astimezone(timezone.utc) > timedelta(days=14):
        raise ValueError("Choose a window of at most 14 days.")
    return interval


def snapshot_from_parts(student_id, weekly, busy_rows, exam_rows) -> ScheduleSnapshot:
    """Shared bridge for stored schedules and title-free matching fixtures."""
    busy_rows = [row.model_dump() if hasattr(row, "model_dump") else row for row in busy_rows]
    exam_rows = [row.model_dump() if hasattr(row, "model_dump") else row for row in exam_rows]
    hours = tuple(WeeklySlot(index, time.fromisoformat(start), time.fromisoformat(end))
                  for index, day in enumerate(DAYS)
                  if (start := weekly.get(day, ("", ""))[0]) and (end := weekly.get(day, ("", ""))[1]))
    known_exams = {row["id"] for row in exam_rows}
    exam_values = tuple(ScheduleExam(row["id"], row["course_id"],
        Interval(_parse(row["starts_at"]), _parse(row["ends_at"])), 3 if row.get("priority") else 0,
        title=f"{(store.course_by_id(row['course_id']) or {}).get('code', row['course_id'])} exam") for row in exam_rows)
    additions, removals, blocks = [], [], []
    for row in busy_rows:
        if row["source"] == "weekly" or row["id"] in known_exams:
            continue  # Weekly rows in the initial seed denote free hours, never busy.
        interval = Interval(_parse(row["starts_at"]), _parse(row["ends_at"]))
        if row["source"] == "exception":
            (additions if row.get("kind") == "free" else removals).append(interval)
        else:
            blocks.append(BusyBlock(row["id"], interval, row["source"], row.get("title") or "Busy"))
    return ScheduleSnapshot(student_id, ZONE.key, weekly=hours, additions=additions,
        removals=removals, exams=exam_values, calendar_busy=blocks, calendar_connected=False)


def schedule_snapshot(student_id: str = store.OWNER_ID) -> ScheduleSnapshot:
    """Read only the requested student's schedule; unknown ids have no hours."""
    return snapshot_from_parts(student_id, store.weekly(student_id), store.busy(student_id), store.exams(student_id))


def list_busy() -> list[BusyInterval]:
    return [BusyInterval.model_validate(row) for row in store.busy() if row["source"] != "weekly"]


def list_busy_for_match(student_id: str = store.OWNER_ID) -> list[MatchBusyInterval]:
    """Title-free hard blocks and free exceptions, kept distinct by kind."""
    return [MatchBusyInterval(id=row["id"], source=row["source"],
                starts_at=_parse(row["starts_at"]).isoformat(), ends_at=_parse(row["ends_at"]).isoformat(),
                kind="free" if row.get("kind") == "free" else "busy")
            for row in store.busy(student_id) if row["source"] in BLOCKING_SOURCES]


def list_exams(student_id: str = store.OWNER_ID) -> list[Exam]:
    result = []
    for row in store.exams(student_id):
        start = _parse(row["starts_at"])
        priority_start = datetime.combine(start.date()-timedelta(days=3), time.min, ZONE).isoformat() if row["priority"] else None
        result.append(Exam(**{**row, "starts_at": start.isoformat(), "ends_at": _parse(row["ends_at"]).isoformat()},
                           priority_starts_at=priority_start))
    return result


def save_weekly(hours: dict[str, tuple[str, str]]) -> None:
    cleaned = {}
    for day in DAYS:
        start, end = hours.get(day, ("", ""))
        if bool(start) != bool(end):
            raise ValueError(f"{DAY_LABELS[day]} needs both a start and an end.")
        if start:
            try:
                left, right = time.fromisoformat(start), time.fromisoformat(end)
            except ValueError:
                raise ValueError(f"{DAY_LABELS[day]} needs valid times.") from None
            if left.tzinfo or right.tzinfo or left >= right or any((left.second, left.microsecond, right.second, right.microsecond)):
                raise ValueError(f"{DAY_LABELS[day]} has to end after it starts, on the same day.")
            start, end = left.isoformat(timespec="minutes"), right.isoformat(timespec="minutes")
        cleaned[day] = (start, end)
    store.replace_weekly(cleaned)


def create_exam(course_id: str, starts_at: str, ends_at: str, priority: bool) -> Exam:
    course = store.course_by_id(course_id)
    if course is None:
        raise ValueError("Pick a course for the exam.")
    window = _window(starts_at, ends_at)
    exam_id = store.next_id("exam")
    row = {"id": exam_id, "course_id": course_id, "starts_at": window.start.isoformat(),
           "ends_at": window.end.isoformat(), "priority": priority}
    store.add_exam(row, {"id": exam_id, "source": "exam", "starts_at": row["starts_at"],
                         "ends_at": row["ends_at"], "title": f"{course['code']} exam"})
    return list_exams()[-1]


def remove_exam(exam_id: str) -> None:
    store.delete_exam(exam_id)


def create_exception(starts_at: str, ends_at: str, kind: str) -> None:
    if kind not in ("free", "busy"):
        raise ValueError("A one-off slot is either free or busy.")
    window = _window(starts_at, ends_at)
    store.add_exception({"id": store.next_id("ex"), "source": "exception", "starts_at": window.start.isoformat(),
                         "ends_at": window.end.isoformat(), "title": "Busy" if kind == "busy" else "Extra free time", "kind": kind})


def remove_exception(exception_id: str) -> None:
    store.delete_exception(exception_id)


def connect_calendar() -> None:
    raise ValueError("Google Calendar is not connected. This page uses synthetic calendar blocks. Real connection requires the team's sign-in and OAuth setup.")


def calendar_status() -> dict:
    return {"connected": False, "synthetic": True, "horizon": "next 14 days",
            "access": "Demo only; no Google account is connected"}


def availability(starts_at: str, ends_at: str, student_id: str = store.OWNER_ID) -> list[dict]:
    """Title-free, continuous free intervals for matching's hard filters."""
    return [{"starts_at": slot.start.astimezone(ZONE).isoformat(), "ends_at": slot.end.astimezone(ZONE).isoformat()}
            for slot in compute_availability(schedule_snapshot(student_id), _window(starts_at, ends_at))]


def own_conflict(starts_at: str, ends_at: str, study_anyway: bool) -> dict | None:
    """Check full coverage; override is local to this call and never skips exams."""
    window = _window(starts_at, ends_at)
    current = schedule_snapshot()
    conflicts = owner_conflicts(current, window)
    exams = [row for row in conflicts if row.source == "exam"]
    without_calendar = replace(current, calendar_busy=tuple(row for row in current.calendar_busy if row.source != "calendar"))
    def covered(snapshot):
        available = compute_availability(snapshot, window)
        return len(available) == 1 and available[0].start == window.start.astimezone(timezone.utc) and available[0].end == window.end.astimezone(timezone.utc)
    can_override = not exams and covered(without_calendar)
    if covered(current) or (study_anyway and can_override):
        return None
    duration = (window.end.astimezone(timezone.utc)-window.start.astimezone(timezone.utc)).total_seconds()/60
    try:
        horizon = Interval(window.start, window.start+timedelta(days=14))
    except OverflowError:
        raise ValueError("Choose a date at least 14 days before the end of the supported calendar.") from None
    alternative = next_available([current], horizon, duration)
    blocking = exams[0] if exams else (conflicts[0] if conflicts else None)
    return {"title": blocking.title if blocking else "Outside your study hours",
            "alternative_start": alternative.start.astimezone(ZONE).isoformat() if alternative else None,
            "alternative_end": alternative.end.astimezone(ZONE).isoformat() if alternative else None,
            "can_study_anyway": can_override, "has_alternative": alternative is not None}


def weekly_hours(student_id: str = store.OWNER_ID) -> dict[str, tuple[str, str]]:
    return store.weekly(student_id)
