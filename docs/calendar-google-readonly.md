# M2 read-only Google Calendar — implementation and live boundary

Historical M2 baseline below. The newer private user connection host and actual
read verification are documented in [Google user connection](calendar-google-user-connect.md).
The default synthetic app remains isolated; the private preview now supplies its
own explicit composition using the existing local login responsibility.

This file describes the M2 read-only baseline continued from M1.5 commit `0e1f0e9`, without importing the older shuluun implementation. M2 code and mocked HTTP/OAuth tests exist. **No real Google account has been connected or read.** The later M3 executor and incremental owned-event consent now exist; see [M3 handoff](calendar-writeback-handoff.md). Initial consent stays read-only and the current synthetic host still cannot write/delete real events.

## What is implemented

- Authlib Web OAuth using only `calendar.events.readonly`, offline consent and PKCE S256. State is random, expires after ten minutes, is single-use, and binds both authenticated app owner and the current server login session. State hashes and encrypted code verifiers are persisted, not raw codes or callback URLs.
- Fernet-encrypted access/refresh credentials, scoped to the owner, with credential generation/version checks. A refresh response omitting refresh_token preserves that existing connection's token. A new authorization must provide a new refresh token; without an identity scope there is no evidence that the old token belongs to a newly chosen Google account, so it is never silently carried over.
- Credential replacement and clearing old-account busy/title cache share one SQLite transaction. Disconnect deletes local credentials and OAuth states, removes cached busy/title data, preserves rules/exams, and invalidates older pending operations. It does not claim Google-side grant revocation.
- GET primary-calendar events only. Coverage is fourteen local calendar dates starting at today's local midnight; exact UTC bounds are returned. The October 24–November 7 Los Angeles fixture is 337 elapsed hours across DST, not 336. Scope alone does not constrain dates; the provider enforces this interval before any request.
- `singleEvents=true`, full pagination, bounded retries for transport/429/5xx failures and one token refresh attempt after 401. All pages are staged; incomplete reads never replace an old snapshot. A newer refresh cannot be overwritten by an older one.
- Canceled/transparent/self-declined/working-location events do not block; opaque tentative/needsAction events block conservatively. All-day end dates are exclusive and interpreted in the calendar's timezone. Repeated instances come from Google's singleEvents expansion, not a local guessed recurrence.
- Only the fields needed for intervals, owner summary and self response status are requested. No attendee identities/emails, descriptions or provider raw JSON are retained. Event identifiers are hashed before storage. Matching reads the saved projection without title-table/provider access.
- Refresh service for Marc's server coordinator; bounded concurrency and trusted eligible IDs. Matching does not call it. Failed/stale/uncovered state returns unknown and no eligible slots; only explicit disconnect returns manual-only. `google_current` in tests is a simulated state of the real-provider adapter, not evidence of a real Google run.
- Read-only routes mounted in the team application: GET `/calendar/google/connect`, GET `/callback`, POST `/refresh`, POST `/disconnect`. Auth/CSRF/callback/state checks are on the server. No arbitrary redirects, browser user IDs or front-end approval booleans establish authority. Callback/query and provider logging is redacted; library token-debug logging is disabled.

## Why the current page cannot connect a real account

Latest team main (`c69e063`) adds a demo login cookie, but Profile/calendar owner and invitation state are still synthetic and not isolated for real accounts. Calendar routes now require login and deny any registered demo account other than the seeded fixture owner; a demo login never becomes a trusted Google actor. The `/schedule` host therefore stays synthetic and displays **Google not configured / team identity required**. Its existing connection entry reaches OAuth only after the host injects BOTH verified server identity and an isolated live composition. Neither exists by default. Merely setting credential paths cannot enable it. Forged headers/body IDs and `trusted=true` fail tests.

`build_google_integration` rejects the seeded demo database before reading credential files. A real binding must use a separate authenticated repository and never the fixed fake owner. No second production identity system has been created: `GoogleActor` is an adapter value supplied by the team's verified session, not a login implementation.

Stephen must provide middleware/dependency that derives `GoogleActor(subject_id, timezone, session_binding, csrf_token)` from the real logged-in session, sets `request.state.calendar_actor`, and replaces the fake owner boundary across the owner page, Profile and matching request. Do not fabricate this from request headers or attach it only to an OAuth route while leaving the owner page globally shared. Marc must provide a real reservation gateway. Postgres and real auth integration remain unverified.

## Composition for the authenticated host

```python
from app.scheduling.google_setup import build_google_integration

# These are trusted host dependencies/config, not browser input:
active = build_google_integration(
    authenticated_schedule_repository, authoritative_session_gateway,
    profile_timezone_for_user,
    client_file=private_web_oauth_json_path,
    encryption_key_file=private_fernet_key_path,
    redirect_uri="http://127.0.0.1:8011/calendar/google/callback",
)
app.state.google_calendar_integration = active
# Auth middleware supplies request.state.calendar_actor as described above.
```

For refresh at session start and again before Send:

```python
statuses = active.refresh_calendars(trusted_eligible_context, selected_participant_ids)
# Re-read snapshots and perform the final current-version conflict check.
# An available interval alone is not permission to send or export.
```

These calls are implemented and mocked. They are **not yet invoked by a real Marc session coordinator**, and the fixed owner host is not a live-local auth implementation. No production readiness claim is made.

## One consolidated Google Cloud checklist, when identity is ready

No user action is required to keep the current synthetic page working. Once real identity isolation is implemented and the user authorizes a live read test, Codex can perform the local setup; the user only completes the necessary Google account/project consent operations. Do not paste secrets into chat.

1. In [Google Cloud API Library](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com), select the team's project and enable **Google Calendar API**.
2. In [Google Auth Platform](https://console.cloud.google.com/auth/overview), configure Branding (app name/support contact), Audience (testing audience plus the explicitly consenting test account), and Data Access. Request only `https://www.googleapis.com/auth/calendar.events.readonly`. Do not add Calendar write, Gmail, Contacts, profile or unrelated scopes.
3. In [OAuth Clients](https://console.cloud.google.com/auth/clients), create a **Web application** client. Add this exact authorized redirect URI: **`http://127.0.0.1:8011/calendar/google/callback`**. Keep the hostname consistent; localhost and 127.0.0.1 are different callback registrations. Production needs its separately agreed HTTPS URI.
4. Save the downloaded Web-client JSON in a private local configuration folder outside the source tree. Share only its local file location with Codex. Client secret values, tokens and the JSON must not be pasted into chat or committed.
5. Codex can generate the local encryption key into a separate private file, configure the explicit builder, start the authenticated local host and open Connect. The user chooses the intended test account and completes Google's consent screen. A real read is run only after this identity/configuration/consent sequence and separately reported. No events will be written or deleted.

No API key or model key is needed. The configuration template is `.env.calendar.example`; it documents names and does not auto-load secrets or turn the synthetic page into a live account.

## Files, migrations, checks and limits

M2 files are `app/scheduling/google_{config,storage,oauth,provider,integration,setup}.py`, the read-only router, core provider metadata/migration changes and the dedicated mock tests. Schema migration `002_provider_kind.sql` upgrades v1 to v2 without deleting scheduling records. Encrypted-credential/OAuth-state tables are created only by explicit live composition/vault migration. They use the same local transaction engine; a Postgres adapter and migrations remain a separate integration task.

Dependencies added: Authlib 1.9.1, cryptography 50.0.2 and their resolved dependencies; full versions are in `requirements-calendar-lock.txt`. Current Authlib/Starlette warn about migrating their httpx adapters to httpx2. Tests pass with the installed httpx 0.28.1; warnings are retained. Library compatibility is tested here, not asserted for all Python/OS versions.

Actual final test count, commands, commit and publication state are saved in `calendar-integration-progress.md`. The test suite exercises invalid/expired/replayed/mismatched state, PKCE/scope, refresh-token preservation/account-switch isolation, encrypted storage, disconnect races, primary-only date bounds, DST, all-day behavior, event filters, pagination/retries, atomic failure/recovery, CSRF, safe logs, stale snapshot generation, and the existing team routes. No real OAuth UI, private calendar read, network quota/latency, Postgres, deployment or real writes have been verified.

Primary references checked for this implementation: [Google Web OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), [event resource](https://developers.google.com/workspace/calendar/api/v3/reference/events), [Authlib HTTP clients/PKCE](https://docs.authlib.org/en/stable/oauth2/client/http/index.html).
