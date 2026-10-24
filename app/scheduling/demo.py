"""Only synthetic bootstrap/identity here. Never bind this owner to Google OAuth."""
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, Request
from app import store
from .core.providers.fake import FakeCalendarProvider
from .core.schemas import (Actor, Exam, ExceptionInput, Horizon, PrivateEvent,
                           ScheduleException, WeeklyRule)
from .time import display_zone, parse_instant

FIXTURE_HORIZON = Horizon(start="2026-10-24T07:00Z", end="2026-11-23T08:00Z")


def demo_owner(request: Request) -> Actor:
    # No target-user query, role string, or header can change the fixed fixture actor.
    session_user = request.session.get("user")
    if not session_user or session_user.get("id") != store.OWNER_ID:
        raise HTTPException(403, "SYNTHETIC_OWNER_ONLY")
    attempted = request.query_params.get("user_id") or request.query_params.get("owner_id")
    if attempted and attempted != store.OWNER_ID:
        raise HTTPException(403, "NOT_OWNER")
    return Actor(subject_id=store.OWNER_ID)


def fixture_records():
    from app.schedule_core import DAYS
    users = [store.OWNER_ID, *(p["id"] for p in store.others())]
    records = []
    private_events = {u: [] for u in users}
    for user in users:
        for day, (start, end) in store.weekly(user).items():
            if start and end:
                records.append(("weekly-rules", WeeklyRule(id=f"seed-{user}-{day}", owner_id=user,
                    weekday=DAYS.index(day), start_local=start, end_local=end, timezone=display_zone())))
        for exam in store.exams(user):
            # Preserve the old demo's displayed priority metadata on import only.
            priority = (parse_instant(exam["starts_at"]) - timedelta(days=3)) if exam.get("priority") else None
            records.append(("exams", Exam(id=exam["id"], owner_id=user, course_id=exam["course_id"],
                start=parse_instant(exam["starts_at"]), end=parse_instant(exam["ends_at"]), priority_start=priority)))
        for event in store.busy(user):
            if event["source"] == "calendar":
                private_events[user].append(PrivateEvent(id=event["id"], start=parse_instant(event["starts_at"]),
                    end=parse_instant(event["ends_at"]), private_title=event.get("title") or "Synthetic busy"))
    # Explicit fictional availability for the team's existing Wednesday Math 54
    # golden demo, previously matched outside declared hours. Never auto-grant a real request.
    records.append(("exceptions", ScheduleException(id="seed-math54-demo-hours", owner_id=store.OWNER_ID,
        kind="available", start=parse_instant("2026-11-04T17:00"), end=parse_instant("2026-11-04T21:00"))))
    return users, records, FakeCalendarProvider(private_events)


def seed_repository(repository, now=None):
    users, records, provider = fixture_records()
    snapshots = {u: provider.read_snapshot(u, FIXTURE_HORIZON) for u in users}
    changed = repository.seed_once("team-synthetic-v1", users, records, snapshots, now or datetime.now(timezone.utc))
    return changed, provider
