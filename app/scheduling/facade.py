"""Persistent implementation behind app.schedule_core's compatible public API."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import delete, insert
from app import store
from app.contracts import BusyInterval, Exam, MatchBusyInterval
from .core.schemas import Actor, ExamInput, ExceptionInput, Horizon, TrustedMatchContext, WeeklyRuleInput
from .core.storage import records
from .runtime import get_runtime
from .time import display_zone, horizon, local_input, local_iso, parse_instant, slot

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABELS = dict(zip(DAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")))
BLOCKING_SOURCES = ("exam", "calendar", "exception")


def _exam_view(exam):
    return Exam(id=exam.id, course_id=exam.course_id, starts_at=local_iso(exam.start),
        ends_at=local_iso(exam.end), priority=exam.priority_start is not None,
        priority_starts_at=local_iso(exam.priority_start) if exam.priority_start else None)


def list_exams(student_id: str = store.OWNER_ID) -> list[Exam]:
    return [_exam_view(e) for e in get_runtime().repository.list_records(student_id, "exams")]


def _blocks(student_id, private=False):
    repository = get_runtime().repository
    bundle = repository.read_bundle(student_id)
    titles = {e.id: e.private_title for e in repository.private_events(student_id)} if private else {}
    out = []
    for block in bundle["busy"]:
        out.append(dict(id=block.id, source="calendar", starts_at=local_iso(block.start), ends_at=local_iso(block.end),
                        **({"title": titles.get(block.id)} if private else {})))
    for exam in bundle["exams"]:
        out.append(dict(id=exam.id, source="exam", starts_at=local_iso(exam.start), ends_at=local_iso(exam.end)))
    for exception in bundle["exceptions"]:
        out.append(dict(id=exception.id, source="exception", starts_at=local_iso(exception.start), ends_at=local_iso(exception.end),
                        kind="free" if exception.kind == "available" else "busy"))
    return sorted(out, key=lambda b: (b["starts_at"], b["id"]))


def list_busy() -> list[BusyInterval]:
    return [BusyInterval(**b) for b in _blocks(store.OWNER_ID, private=True)]


def list_busy_for_match(student_id: str = store.OWNER_ID) -> list[MatchBusyInterval]:
    return [MatchBusyInterval(**b) for b in _blocks(student_id)]


def weekly_hours(student_id: str = store.OWNER_ID) -> dict[str, tuple[str, str]]:
    values = {d: ("", "") for d in DAYS}
    for rule in get_runtime().repository.list_records(student_id, "weekly-rules"):
        values[DAYS[rule.weekday]] = (rule.start_local.isoformat(timespec="minutes"), rule.end_local.isoformat(timespec="minutes"))
    return values


def save_weekly(hours: dict[str, tuple[str, str]], student_id: str = store.OWNER_ID,
                timezone_name: str | None = None, overnight: dict | None = None) -> None:
    validated = []
    for number, day in enumerate(DAYS):
        start, end = hours.get(day, ("", ""))
        if bool(start) != bool(end):
            raise ValueError(f"{DAY_LABELS[day]} needs both a start and an end.")
        if start:
            validated.append(WeeklyRuleInput(weekday=number, start_local=start, end_local=end,
                timezone=timezone_name or display_zone(), ends_next_day=bool((overnight or {}).get(day))))
    repository = get_runtime().repository
    with repository.engine.begin() as connection:
        connection.execute(delete(records).where(records.c.owner_id == student_id, records.c.kind == "weekly-rules"))
        for rule in validated:
            connection.execute(insert(records).values(id=uuid4().hex, owner_id=student_id,
                kind="weekly-rules", payload=rule.model_dump(mode="json")))
        repository._bump(connection, student_id)


def create_exam(course_id: str, starts_at: str, ends_at: str, priority: bool,
                priority_starts_at: str | None = None) -> Exam:
    if store.course_by_id(course_id) is None or course_id not in store.owner()["course_ids"]:
        raise ValueError("Pick one of your courses for the exam.")
    if priority and not priority_starts_at:
        raise ValueError("Choose an explicit priority start; no default pre-exam duration is assumed.")
    value = ExamInput(course_id=course_id, start=parse_instant(starts_at), end=parse_instant(ends_at),
                     priority_start=parse_instant(priority_starts_at) if priority_starts_at else None)
    return _exam_view(get_runtime().repository.create_record(store.OWNER_ID, "exams", value))


def update_exam(exam_id: str, course_id: str, starts_at: str, ends_at: str, priority_starts_at: str | None = None):
    if store.course_by_id(course_id) is None or course_id not in store.owner()["course_ids"]:
        raise ValueError("Pick one of your courses for the exam.")
    value = ExamInput(course_id=course_id, start=parse_instant(starts_at), end=parse_instant(ends_at),
                     priority_start=parse_instant(priority_starts_at) if priority_starts_at else None)
    result = get_runtime().repository.patch_record(store.OWNER_ID, "exams", exam_id, value.model_dump())
    if result is None:
        raise ValueError("Exam not found for this owner.")
    return _exam_view(result)


def remove_exam(exam_id: str) -> None:
    get_runtime().repository.delete_record(store.OWNER_ID, "exams", exam_id)


def create_exception(starts_at: str, ends_at: str, kind: str) -> None:
    if kind not in ("free", "busy"):
        raise ValueError("A one-off slot is either free or busy.")
    value = ExceptionInput(start=parse_instant(starts_at), end=parse_instant(ends_at),
                           kind="available" if kind == "free" else "unavailable")
    get_runtime().repository.create_record(store.OWNER_ID, "exceptions", value)


def update_exception(exception_id: str, starts_at: str, ends_at: str, kind: str):
    if kind not in ("free", "busy"):
        raise ValueError("A one-off slot is either free or busy.")
    value = ExceptionInput(start=parse_instant(starts_at), end=parse_instant(ends_at),
                           kind="available" if kind == "free" else "unavailable")
    if get_runtime().repository.patch_record(store.OWNER_ID, "exceptions", exception_id, value.model_dump()) is None:
        raise ValueError("Slot not found for this owner.")


def remove_exception(exception_id: str) -> None:
    get_runtime().repository.delete_record(store.OWNER_ID, "exceptions", exception_id)


def effective_availability(student_id: str, starts_at: str, ends_at: str, duration_minutes: int = 1):
    """Trusted server-only matching API. Profile establishes the eligible pool."""
    from app.profile_core import eligible_student_ids
    context = TrustedMatchContext(eligible_subject_ids=frozenset([store.OWNER_ID, *eligible_student_ids()]))
    return get_runtime().service.effective_availability(context, student_id, horizon(starts_at, ends_at), duration_minutes)


def matching_free_time(student_id, start, end):
    snapshot = effective_availability(student_id, start, end)
    return [(i.start, i.end) for i in snapshot.available]


def own_conflict(starts_at: str, ends_at: str, study_anyway: bool) -> dict | None:
    requested = slot(starts_at, ends_at)  # Validate before considering any override.
    search = Horizon(start=requested.start, end=requested.start + timedelta(days=14))
    result = get_runtime().service.check_slot(Actor(subject_id=store.OWNER_ID), requested, search)
    if not result.blocked:
        return None
    titles = [c.owner_only_private_title for c in result.conflicts if c.owner_only_private_title]
    names = {"EXAM_BUSY":"Exam", "MANUAL_BUSY":"Unavailable", "OUTSIDE_STUDY_HOURS":"Outside study hours",
             "CONFIRMED_SESSION_BUSY":"Confirmed study session", "CALENDAR_UNKNOWN":"Calendar unknown", "CALENDAR_BUSY":"Calendar busy"}
    alternative = result.alternative_slot
    return {"title": "; ".join(titles) or ", ".join(dict.fromkeys(names[c.code] for c in result.conflicts)),
        "codes": [c.code for c in result.conflicts], "override_eligible": False,
        "alternative_start": local_input(alternative.start) if alternative else None,
        "alternative_end": local_input(alternative.end) if alternative else None,
        "no_alternative": alternative is None,
        "message": "Study anyway requires a server-side request/revision approval; it is currently disabled."}


def connect_calendar() -> None:
    raise ValueError("Google is not configured. Fixed demo identities cannot authorize real calendar access.")


def calendar_status() -> dict:
    return {"connected":False, "horizon":"synthetic fixture only", "access":"Primary calendar read-only is not connected",
            "mode":"synthetic", "message":"Synthetic schedules only. Google not configured; team identity integration required."}
