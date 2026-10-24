# Schedule validation status

Observed on 2026-10-24 in the local `shuluun` checkout.

## Passed

- Full automated suite: **181 passed**, with 12 additional unittest subtests. Command: `.venv\Scripts\python.exe -m pytest -o addopts='' -q`. One dependency deprecation warning came from Starlette's httpx test client; no test failed.
- Deterministic time engine: weekly and overnight hours, one-off additions/removals, exam conflicts, priority windows, complete shared intervals, timezones, and DST gaps/folds.
- Mocked Google adapter: paginated reads, all-day intervals, canceled/transparent/declined events, read failure, least-privilege OAuth, exact event receipt checks, stable IDs, unknown write outcomes and no attendees.
- API and credentials: authentication, owner-only titles, private projection, exact approval, stale versions, calendar-only per-request owner override, peer hard conflicts, token encryption, browser-bound one-use OAuth state, disconnect during authorization/refresh, and account-switch refresh-token isolation.
- Running service smoke: `scripts/smoke_schedule.py` against a real local Uvicorn process on `127.0.0.1:8013`. Shared windows were 2026-10-26 14:30–15:30 and 16:00–17:00 America/Los_Angeles. Own-title privacy, limited override, organizer-only preview and blocked unconfigured calendar write passed. The clock and all people/events were explicitly synthetic.
- PostgreSQL table DDL compiled using SQLAlchemy's PostgreSQL dialect. This is a schema compatibility check only.
- A second coding agent reviewed the authorization, stale-data, override and credential paths. Identified issues were fixed and regression cases added.

Visual checks confirmed 90-minute empty results, the new-conflict result shrinking to 16:00–17:00, private-title hiding on identity switch, and selected-window preview at `/demo`.

Latest integration: imported the team's skeleton from main (`03ef6c4`), reused shared contracts and adapted its schedule core to the engine. Fourteen new tests cover forms, actual available intervals, title-free matching data, DST, same-duration alternatives and non-overridable exams. Existing team pages render. A live browser edit from Thursday 21:00 to 20:30 recomputed the free interval; the seed value was restored. The skeleton page is local at `127.0.0.1:8014/schedule` and uses a separate synthetic seed from `/demo`.

Omnara: 24 mocked cases cover the official device flow, data projection, allowed custom tools, evidence validation, request reuse and budgets. Official discovery and device-code creation succeeded live; the page displayed the provider sign-in screen. The user deferred completing authorization, so no verified account or live model call is claimed. The model has no executor tools.

Additional packaging checks: pip dependency consistency passed and the project wheel built successfully. The GitHub Actions workflow covers Python 3.11/3.12 on Linux/Windows. Local results do not establish that remote CI has passed; check GitHub Actions for its current result. Local checks used Windows with Python 3.12.

## Not yet verified

No real Google account was connected, and no real calendar event was created. OAuth user experience and provider behavior still require a configured Google project and consented test account. No running PostgreSQL server was available; actual Postgres transactions, migrations and deployment have not been tested.

The imported skeleton includes the team's deterministic scoring implementation. Its 21 original matching tests pass unchanged, along with 16 new schedule/matching/session integration cases. Real Places data, authenticated users, authoritative invitation acceptance, email, live Omnara inference and end-to-end product evals remain incomplete. Skeleton memory flags are not real consent or delivery. These test counts must not be presented as the original team's 26/36 product evals passing.

These results describe the tested shuluun implementation. See the GitHub branch and Actions run for publication and remote CI status. No production deployment or real-account acceptance is claimed.
