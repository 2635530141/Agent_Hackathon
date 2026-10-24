"""Schedule and calendar track (Shulun).

Weekly hours are when someone can study. Exams, calendar events, and busy
exceptions are blocks. Matching receives blocks without event titles.
Connecting Google and subtracting calendar time from weekly hours is still a stub.
"""

from datetime import datetime, timedelta

from app.contracts import BusyInterval, Exam, MatchBusyInterval
from app.store import (
    OWNER_ID,
    add_exam,
    add_exception,
    busy,
    calendar_connected,
    course_by_id,
    delete_exam,
    delete_exception,
    exams,
    next_id,
    replace_weekly,
    set_calendar_connected,
    weekly,
)

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABELS = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}
BLOCKING_SOURCES = ("exam", "calendar", "exception")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def list_busy() -> list[BusyInterval]:
    return [BusyInterval.model_validate(block) for block in busy()]


def list_busy_for_match(student_id: str = OWNER_ID) -> list[MatchBusyInterval]:
    return [
        MatchBusyInterval(
            id=block["id"],
            source=block["source"],
            starts_at=block["starts_at"],
            ends_at=block["ends_at"],
            kind=block.get("kind"),
        )
        for block in busy(student_id)
    ]


def list_exams(student_id: str = OWNER_ID) -> list[Exam]:
    views = []
    for exam in exams(student_id):
        priority_start = None
        if exam["priority"]:
            start = _parse(exam["starts_at"]) - timedelta(days=3)
            priority_start = start.isoformat(timespec="minutes")
        views.append(Exam(**exam, priority_starts_at=priority_start))
    return views


def save_weekly(hours: dict[str, tuple[str, str]]) -> None:
    cleaned: dict[str, tuple[str, str]] = {}
    for day in DAYS:
        start, end = hours.get(day, ("", ""))
        if bool(start) != bool(end):
            raise ValueError(f"{DAY_LABELS[day]} needs both a start and an end.")
        if start and end and start >= end:
            raise ValueError(f"{DAY_LABELS[day]} has to end after it starts.")
        cleaned[day] = (start, end)
    replace_weekly(cleaned)


def create_exam(course_id: str, starts_at: str, ends_at: str, priority: bool) -> Exam:
    course = course_by_id(course_id)
    if course is None:
        raise ValueError("Pick a course for the exam.")
    start = _parse(starts_at)
    end = _parse(ends_at)
    if end <= start:
        raise ValueError("The exam has to end after it starts.")
    exam_id = next_id("exam")
    exam = {
        "id": exam_id,
        "course_id": course_id,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "priority": priority,
    }
    add_exam(
        exam,
        {
            "id": exam_id,
            "source": "exam",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "title": f"{course['code']} exam",
        },
    )
    return list_exams()[-1]


def remove_exam(exam_id: str) -> None:
    delete_exam(exam_id)


def create_exception(starts_at: str, ends_at: str, kind: str) -> None:
    if kind not in ("free", "busy"):
        raise ValueError("A one-off slot is either free or busy.")
    start = _parse(starts_at)
    end = _parse(ends_at)
    if end <= start:
        raise ValueError("The slot has to end after it starts.")
    exception_id = next_id("ex")
    title = "Busy" if kind == "busy" else "Extra free time"
    add_exception(
        {
            "id": exception_id,
            "source": "exception",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "title": title,
            "kind": kind,
        }
    )


def remove_exception(exception_id: str) -> None:
    delete_exception(exception_id)


def connect_calendar() -> None:
    """Demo switch. Google OAuth replaces this."""
    set_calendar_connected(True)


def calendar_status() -> dict:
    return {
        "connected": calendar_connected(),
        "horizon": "next 14 days",
        "access": "read busy intervals; titles stay on this page",
    }


def own_conflict(starts_at: str, ends_at: str, study_anyway: bool) -> dict | None:
    """Organizer's own block. They may override it for this session only."""
    if study_anyway:
        return None
    start = _parse(starts_at)
    end = _parse(ends_at)
    if end <= start:
        raise ValueError("The session has to end after it starts.")
    for block in busy():
        if block["source"] not in BLOCKING_SOURCES:
            continue
        if block.get("kind") == "free":
            continue
        block_start = _parse(block["starts_at"])
        block_end = _parse(block["ends_at"])
        if start < block_end and end > block_start:
            alternative_start = block_end
            alternative_end = end if end > block_end else block_end + timedelta(hours=2)
            return {
                "title": block.get("title") or "Busy",
                "alternative_start": alternative_start.isoformat(timespec="minutes"),
                "alternative_end": alternative_end.isoformat(timespec="minutes"),
            }
    return None


def weekly_hours(student_id: str = OWNER_ID) -> dict[str, tuple[str, str]]:
    return weekly(student_id)
