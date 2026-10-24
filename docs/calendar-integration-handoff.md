# Calendar integration v2 — M1.5

This version continues the main-based integration developed in this chat. The user's older `shuluun` version is preserved and is not the implementation baseline. No old provider/Omnara code has been imported. Base: team main `03ef6c415acebe03458a02df110aa474161ac190`.

## Delivered behavior

The existing `/schedule` page, team shared models and `starts_at`/`ends_at` names remain. `app/schedule_core.py` is a compatibility facade into `app/scheduling/`. The deterministic core is reused from this chat's M0/M1 under `app/scheduling/core/`, including its UTC/DST interval code, schemas, SQLite repository and explicit SQL migration. Fake identity/session controls from the standalone demo were not copied.

Owner forms and Matching now read one persistent calendar database. Weekly hours, additions, unavailable exceptions, exams and explicit priority starts have create/edit/remove flows; exam edits affect both the page and Matching. Calendar snapshots and confirmed reservations are subtracted. Priority never grants time. Owner alternatives retain the exact requested duration and avoid all known blocks. No feasible alternative is represented by null fields and a visible message.

`study_anyway` cannot skip parsing, exams, unavailable time, other users' conflicts or the configured study hours. Ordinary organizer calendar overrides remain disabled until Marc provides a current request/revision approval gateway. The old browser boolean is not approval. Export is disabled, including the prior synthetic acceptance button; there is no Google write capability.

## Stable calls

Existing signatures and shared model return types are preserved:

```python
weekly_hours(student_id=OWNER_ID) -> dict[str, tuple[str, str]]
list_exams(student_id=OWNER_ID) -> list[app.contracts.Exam]
list_busy() -> list[app.contracts.BusyInterval]  # owner/private only
list_busy_for_match(student_id=OWNER_ID) -> list[app.contracts.MatchBusyInterval]
save_weekly(hours, student_id=OWNER_ID, timezone_name=None, overnight=None) -> None
create_exam(course_id, starts_at, ends_at, priority, priority_starts_at=None) -> Exam
create_exception(starts_at, ends_at, kind) -> None  # free|busy
remove_exam(exam_id) / remove_exception(exception_id) -> None
own_conflict(starts_at, ends_at, study_anyway) -> dict | None
```

Added calls:

```python
update_exam(exam_id, course_id, starts_at, ends_at, priority_starts_at=None) -> Exam
update_exception(exception_id, starts_at, ends_at, kind) -> None
effective_availability(student_id, starts_at, ends_at, duration_minutes=1) -> AvailabilitySnapshot
matching_free_time(student_id, start, end) -> list[tuple[datetime, datetime]]
```

`effective_availability` is trusted server code; eligibility is derived from Profile. It is not an arbitrary browser directory. David's existing `Person` construction accepts an optional schedule reader; `load_inputs()` binds that reader to this API. Explicit test `Person` objects continue to work through a pure adapter using the same interval primitives. Scoring weights, ranking, place selection and original 21 matching tests remain unchanged. The scorer's existing group overlap policy is not redefined as full-duration matching; equal-duration behavior here refers to the owner's alternative calculation.

Example for David:

```python
from app import schedule_core
snapshot = schedule_core.effective_availability(
    student_id, "2026-10-26T13:00-07:00", "2026-10-26T18:00-07:00", 60)
# snapshot.available, snapshot.priority_windows, snapshot.calendar, snapshot.schedule_version
# No titles, emails, tokens or provider calls. Unknown verification is not verified free.
```

Owner conflict result preserves title/alternative keys and adds `codes`, `override_eligible=false`, `no_alternative`, and a safe explanation. It is private UI data and must never go to model input. Candidate calls use the sanitized core methods only. `AvailabilitySnapshot` returns UTC instants; form/shared string projections use the configured IANA local timezone. No fixed UTC-7 offset is used. Legacy proposal strings stay form-compatible.

## Persistence, identity and synthetic boundary

There is no team Postgres or real auth implementation at this baseline. `STUDYMATE_CALENDAR_DB` defaults to `data/calendar-synthetic.db`. Explicit v1 migration creates `smcal_*` tables only; no duplicate users/courses/requests/sessions tables. SQLite success is not Postgres verification. Imports do not create the database; the seed command or first calendar service access initializes this synthetic runtime.

The old Profile/store still owns courses and the fixed fixture owner; scheduling writes no longer write its separate in-memory calendar lists. Those lists are used once for idempotent synthetic bootstrap. The seed imports existing fixture times and preserves the old demo's pre-exam metadata only for imported records. New priority defaults to unset. To retain the team's Wednesday Math 54 golden example without silently granting requests outside hours, the seed includes an explicitly fictional one-off availability on 2026-11-04 17–21. This is documented demo data, never a real-user rule.

Synthetic coverage is a fixed replay window ending 2026-11-23, not a Google coverage claim. Failed/stale/uncovered connected data gives unknown/no eligible slots, retaining cached busy data. Refresh the synthetic snapshot using the clearly labeled page button.

`demo_owner` permits only the existing fixed fake owner, rejects cross-user query targets, and ignores spoofed identity headers. It is NOT production authentication. Only fictional calendar data may be exposed in this host. `STUDYMATE_CALENDAR_MODE` values other than synthetic currently fail closed. Stephen must replace this boundary; do not attach real credentials to the fake owner.

`TeamSessionGateway` reads the team's existing accepted synthetic invitation batch as reservations; it never authorizes export and never creates a duplicate session lifecycle. Marc must supply authoritative IDs, versions and reservations; their upstream in-memory state still resets on restart. Schedule versions currently cover calendar edits/sync, not a future authoritative session revision. Approval/Send checks must include Marc's version once supplied.

## Installation and actual local validation

All commands are run by Codex in this task; the user does not need to enter them. Reproduction for teammates:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-calendar-dev.txt -c requirements-calendar-lock.txt
.\.venv\Scripts\python.exe -m app.scheduling.seed
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --no-access-log
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/calendar-tests
```

When the Windows sandbox TEMP is unwritable, set process TEMP/TMP to the project's `.tmp` before creating the virtual environment. API tests/Uvicorn require normal local socket access, unavailable inside this particular sandbox. Use the host's allowed execution path; do not disable protections.

Installed/tested: Python 3.13.5, FastAPI 0.162.0, Pydantic 2.14.1, SQLAlchemy 2.1.6, Uvicorn 0.54.0, Jinja2 3.1.6, python-multipart 0.0.32, pytest 9.1.1, httpx 0.28.1, tzdata 2026.5. Full resolved versions are in requirements-calendar-lock.txt.

Baseline before edits: standalone 60 passed; unmodified team 21 passed. Migrated tests and integration tests passed (latest exact count recorded in calendar-integration-progress.md). One Starlette/httpx deprecation warning remains; it is not hidden. Coverage includes the 13–18 minus 14–15 minus 16–17 acceptance, page/Matching synchronized exam edits, UTC restart persistence, half-open boundaries, hard exams/invalid inputs, same-duration alternatives, no-alternative rendering, privacy, denied cross-user targets, fake sync failure and existing team pages.

Actual browser: team `/schedule` renders with original shell/styles; saved Monday 13–18; added a Thursday 20–21 exam and observed free time shrink to 19–20; edited its end to 20:30 and observed 20:30–21 become free. These are fictional local verification records. Automated restart verification uses a new runtime over the same file; actual server restart verification is recorded in progress. No real calendar/model/email/network provider verification is claimed.

## Team handoff and remaining decisions

Stephen: stable user/course IDs, authenticated current-owner dependency and per-user timezone, eligibility/consent, cookie/CSRF/session conventions. David: use the saved-data availability API and verification metadata; never call Google or serialize owner views. Marc: authoritative confirmed reservations, request/session revision and acceptance policy; invoke refresh outside scoring at session start and before Send, then re-read/recheck. Export remains disabled pending authoritative current version, all required acceptances and a separate organizer confirmation. No live write/delete is authorized by this implementation task.

PROPOSED, not settled PRD policy: exams never override; other people's conflicts never override; only selected ordinary organizer calendar blocks may eventually receive narrow request/revision/slot approval. No such approval is implemented yet. Group acceptance rule remains unresolved, so real writes are prohibited. Priority has no default number of days. M2 primary-only 14-local-day reads and freshness coordination remain the next milestone; no fake test proves Google OAuth works.

Files: app/scheduling/**; compatibility app/schedule_core.py; calendar router/template; minimal Matching time/reader bridge; Session null-alternative/disabled-export guard and conflict partial; calendar tests/fixtures; requirements additions/dev/lock; .gitignore protections and this handoff/progress document. app/contracts.py, app/store.py, Profile, scoring weights, team styles and app/main.py are preserved in M1.5.
