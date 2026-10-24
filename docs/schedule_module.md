# Schedule module reference

For current team work, start with [the skeleton schedule guide](skeleton_schedule_guide.md). The shared `app.main:app` entry point now calls this module's deterministic engine. This document describes the separate API and its Alex/Bella demo; its data is not synchronized with the skeleton's in-memory Maya seed.

The team PRD assigns **Schedule and calendar** to shulun. This branch implements that module as a separate FastAPI service with deterministic time calculations, PostgreSQL-compatible storage, and an optional Google Calendar adapter. It does not implement Stephen's profile form or the team's matching, Omnara agent, places, and email modules.

The source PRD is retained unchanged in the desktop project at `01_plan/PRD.docx`. See [requirements and scope](schedule_requirements.md), [API integration](schedule_integration.md), and [validation status](schedule_validation.md).

## Run the synthetic demo

Requires Python 3.11 or later. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m schedule_service.demo
```

Open the [visual schedule viewer](http://127.0.0.1:8013/demo). Switch Alex/Bella, choose 30/60/90 minutes, and simulate Bella's new conflict to recompute the shared windows. Selecting a window only previews it. The viewer reads the real schedule engine, hides other people's event titles, and does not change saved schedules.

[Interactive API docs](http://127.0.0.1:8013/docs) and [demo setup](http://127.0.0.1:8013/demo-info) remain available. The demo binds only to localhost and disables Google. Its people, exams and imported busy blocks are synthetic; nothing is sent to an email address or calendar.

Use `Authorization: Bearer demo-alex-local` or `Bearer demo-bella-local` for owner routes. Trusted matching routes use `X-Schedule-Service-Token: demo-matching-local`. These public demo values must never authenticate real users.

The next demo Monday has weekly hours, one exam, and busy blocks. The shared continuous windows are 14:30–15:30 and 16:00–17:00; the private event title appears only to its owner. API times include offsets. `--reference 2026-10-24T13:00:00-07:00` fixes the clock for replay.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts='' -q
```

Tests use an isolated SQLite database and mocked Google HTTP responses. They do not establish that live Google OAuth or a running PostgreSQL server has been tested. In restricted Windows environments, Starlette's test client needs local socket permission even though no real Google request is made.

## Configure the real integration

Use `.env.example` as an environment-variable checklist. PostgreSQL is selected with `SCHEDULE_DATABASE_URL`. The module creates only its own namespaced tables on an empty database. Use the team's migration process before connecting an existing production database. Secrets and calendar tokens must remain server-side; tokens are encrypted with the configured Fernet key.

```powershell
.\.venv\Scripts\python.exe -m uvicorn schedule_service.app:app --host 127.0.0.1 --port 8013 --no-access-log
```

Keep `--no-access-log`: the custom trace logs route templates and timing, while a default access log could expose OAuth callback codes in query strings. For a real web deployment, replace the demo bearer-token mapping with team authentication, configure HTTPS and registered OAuth redirects, and keep the internal service credential away from browsers and models.

Calendar reading starts with `calendar.readonly`; writing requires a separate `calendar.events.owned` grant, a trusted record that every participant accepted, and an exact organizer confirmation. Only the organizer's primary calendar receives an event. No guest list is attached, and no email or reservation tool exists here.
