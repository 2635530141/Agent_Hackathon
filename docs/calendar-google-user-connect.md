# Archived Google Calendar prototype

Retired from the normal StudyMate application on 2026-10-24 at the owner's request.
The connection card, setup page and normal-app OAuth/sync/export routes are removed.
Use the manual Schedule timetable; no Google client JSON or API key is needed.
The isolated adapters and tests below are historical compatibility code, not an enabled
main-app feature. Previously saved private credentials and calendars are not deleted.

---

# Google Calendar user connection

The Schedule page now exposes Google connection setup in the normal synthetic
demo. A separate **private read-only preview** reuses the existing login and the
same timetable, with persistent, isolated user accounts and scheduling data.
Ordinary users authorize their own Google account; they do not supply an API key.
The team's Web OAuth client is configured once on the host and kept out of Git.

## Run it

### Windows one-click local test

This launcher is for developers/host operators. Ordinary users never need an OAuth
JSON or API key: the team configures one shared host, then each user signs in and
consents separately. The local loopback preview below is not a public shared host.

Open `Start-Calendar.cmd` in the checkout. Python 3.12+ with the Windows launcher
is required; the script creates a missing `.venv` and installs pinned requirements
when the runtime dependencies are absent. Select a **Web application OAuth JSON**
obtained privately from the team administrator. The default port is 8017.
The selected file stays in place; only its local path is remembered under ignored
`data/private-calendar/`. Secrets are never copied into Git or sent to the browser.
On the private login page, create an account, then Connect Google Calendar.

The launcher checks client type, exact registered callback, occupied port and
missing keys for an existing database. Failure is explicit; it never substitutes
synthetic calendar data or replaces keys. The original demo command below keeps
its existing behavior. Non-Windows/headless operators can use
`python -m app.calendar_start --client-file /private/google-web-client.json`.
`--check` prints sanitized readiness without making a Google request or changing
the database. `STUDYMATE_GOOGLE_CLIENT_FILE` and
`STUDYMATE_CALENDAR_PRIVATE_DIR` are optional environment overrides.

### Google-side prerequisites

Local readiness cannot verify Google's permissions. Calendar API must be enabled,
the callback must match the OAuth client exactly, and each consenting account must
have test-user eligibility while the app is in Testing. School administrators may
also restrict third-party apps. A missing client file is a host setup issue;
`redirect_uri_mismatch` is an exact-callback issue; `access_denied`/403 requires
checking the account, Google consent settings and school policy. Never resolve
these errors by sharing someone's refresh token or committing credentials.

### Manual startup

Python 3.13.5 was used. In PowerShell from the repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-calendar-lock.txt
.\.venv\Scripts\python.exe -m app.scheduling.seed
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --no-access-log
```

These last two commands seed and start the existing **synthetic** demo, preserving
saved edits. It has a visible Google setup card but never loads real credentials.
Stop that process before using the same port for the private preview.

For real user-authorized reads, obtain the team's Web OAuth client JSON privately
from its owner, then run (no seed step for real data):

```powershell
.\.venv\Scripts\python.exe -m app.calendar_preview --client-file C:/private/google-web-client.json --port 8017
```

Open `http://127.0.0.1:8017/register`, create a local account (password at least 12
characters), and use **Connect Google Calendar**. Complete Google's consent as
the account owner. Refresh and Disconnect appear on Schedule. The private
preview is loopback-only; it is not a public deployment or campus verification.
`--port 8011` also works if that exact callback is registered and the port is free.
`--private-dir C:/private/studymate-calendar` selects a private runtime folder.
Default runtime location is ignored `data/private-calendar/`.

The launcher creates a persistent session signing secret and Fernet key only if
missing. Keep the runtime folder and keys to retain login identities, schedules
and refresh tokens across restarts. Never replace a live key to resolve an error.
Use host OS permissions to restrict the private folder to its operator.

Installed versions: FastAPI 0.162.0, Pydantic 2.14.1, SQLAlchemy 2.1.6, Authlib
1.9.1, cryptography 50.0.2, httpx 0.28.1, Uvicorn 0.54.0, pytest 9.1.1. Full pins
are in `requirements-calendar-lock.txt`.

## One team OAuth application

In a dedicated Google Cloud project, enable Calendar API and configure Branding,
an External testing audience and the intended, consenting test users. Create a
**Web application** client with exact redirect URIs:

```text
http://127.0.0.1:8011/calendar/google/callback
http://127.0.0.1:8017/calendar/google/callback
```

Declare only `https://www.googleapis.com/auth/calendar.events.readonly`. Browser
origins are unnecessary for this server OAuth flow. The local client secret,
encryption/session keys, DB and tokens are not in the repository. Teammates need
private host configuration and test-user eligibility to authorize their own
calendars. Pushing source code does not share a developer's private calendar or
activate credentials on a teammate's machine.

Google testing mode limits the audience and refresh-token lifetime. Before a
general release, the team must supply a deployed HTTPS callback, domain/privacy
information, appropriate Google verification, managed secrets and its real auth
and data adapters. This change does not publish the app or enable billing.

## Behavior and boundaries

- Initial Google consent is read-only, with PKCE, single-use state bound to the
  logged-in owner and login session. Re-login changes the binding. Real/demo
  cookies use different names and signing keys. No fake user is seeded in the
  private DB, and synthetic DB markers are rejected before credential loading.
- OAuth completion performs the first read automatically. Refresh reads only
  the primary calendar over the next 14 local dates. Exact UTC bounds are shown;
  the DST fixture spans 337 hours. All pages must succeed before replacement.
- The timetable retains weekly hours, one-off additions/removals, exams, optional
  explicit priority starts, owner conflict detail and one equal-duration
  alternative. Private preview exam codes are owner-entered scheduling labels,
  normalized without spaces, not claims of verified course enrollment.
- Users can change their IANA display timezone. Existing weekly rules retain
  their saved timezone; new entries use the selected timezone. Connected dates
  outside the read horizon or stale/failed reads remain unknown, never free.
- Owner titles are visible only on authenticated owner projections. Matching
  receives sanitized intervals/metadata. Real account data never enters the
  fixture Matching/Profile/Session paths; those routes are gated in this preview.
- Disconnect clears local OAuth credentials and cached events, preserving manual
  hours/exams. It does not claim to revoke Google's grant; users can do that in
  their Google account. No real calendar events are written or deleted here.

## Verification

```powershell
New-Item -ItemType Directory -Path .tmp -Force | Out-Null
$env:PYTHONUTF8='1'
$env:TEMP=(Join-Path $PWD '.tmp')
$env:TMP=$env:TEMP
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/calendar-test-run
```

Pre-integration full run: **182 passed, 2 dependency deprecation warnings**, 27.39s.
The five new private-host tests cover two-user isolation, CSRF, session/state
binding, read-only scope, callback auto-sync, cache privacy, failed refresh,
disconnect and rebuilding the host with persisted accounts, tokens and rules.
The exact 13–18 / busy 14–15 / exam 16–17 example passes through the authenticated
HTTP host. Google HTTP in pytest is mocked, separately from live verification.

On October 24, 2026 a consenting owner completed real Google consent, returned
to Schedule and successfully read the primary calendar. The persisted provider
was `google`, status `synced`, coverage October 24–November 7 in Los Angeles.
No real event data or credentials are copied into this document or tests.
Final integrated test and publication results are appended to the progress log.

## Remaining team interfaces

Stephen supplies authoritative signed-in student/course IDs and timezone. Reuse
`GoogleActor` and the existing account/auth responsibility; do not add another
production identity table. The preview's `PersistentAccounts` is an optional
backend for the existing local login, without campus verification.

David consumes saved sanitized availability with server-established candidate
eligibility. Matching must not call Google. Marc supplies authoritative confirmed
reservations, current Session revision/participants/acceptances and approval
guards, and invokes refresh at request start and before Send. The preview has
no real Session lifecycle, so it reports manual + Google time only and cannot
authorize invitations or exports. Existing M3 export code remains separately
gated by authoritative Session data and explicit organizer confirmation.

Postgres, public deployment, real Session acceptance/export, writes/deletes and
cross-user production Matching remain unverified. Conservative exam and
override/group policies remain proposed; this change does not approve them.

## Team launcher verification (2026-10-24)

`..\Agent_Hackathon\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/team-start-verified -o faulthandler_timeout=45`
completed with **234 passed, 2 dependency deprecation warnings, 21.30s**. Eighteen
new checks cover readiness diagnostics, client selection precedence, redaction,
missing keys, occupied ports, callback mismatch, cancellation and the help page.
The Windows wrapper's `--check` path was also executed using the installed test
venv; it correctly reported an occupied port without stopping the other service.
Fresh Windows dependency installation and native file-picker interaction have
not been tested on a teammate's computer.

The new `app.calendar_start` entry was actually started on 8011 with the existing
private runtime and the consenting owner's Google configuration. A browser
refresh completed a real Google read and advanced Last sync to
2026-10-24 23:13:28 UTC, with approximately 1.7s click-to-render time. This is a
single observed run, not a latency guarantee or a teammate-account verification.
Only safe status/coverage metadata was retained; no Google write/delete occurred.

Before publication, main advanced to `5427e58` with teammates' new room matching
and requested-time boundary behavior. Those commits were merged without conflict.
The complete combined run passed **271 tests**, with 2 dependency deprecation
warnings, in **26.65s** using `--basetemp=.tmp/team-latest-main-verified`.
This patch does not expose the loopback preview publicly, alter the Google Cloud
audience, or claim to have tested an unknown teammate's account or machine.

References: [Google server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server),
[Calendar authorization](https://developers.google.com/workspace/calendar/api/auth),
[OAuth consent configuration](https://developers.google.com/workspace/guides/configure-oauth-consent).
