# Calendar M3 + interactive timetable

## Verified boundaries

M1.5/M2 baseline was rerun: 102 passed, two dependency deprecations. The current team main was fetched and remains c69e063 at the start of this phase. Existing Draft PR #2 is open, head shuluun, base main. Original standalone sources are untouched.

M3 executor code and mock tests are implemented. No real Google account has been authorized, read, written or deleted in this task. The current application uses the team's demo login and synthetic Profile/Invitation store, so live export remains unavailable. No deployed host, production identity isolation, Postgres or real Marc acceptance gateway has been verified. A mock success is not a live success.

## Timetable

/schedule retains StudyMate's cream background, forest green, serif headings and rounded components. Its primary UI is now a week/day timetable with a time gutter, date navigation, type colors, overlap lanes, effective-free markers, and an editor. Click a half-hour slot or drag down a day; select weekly study hours, extra study time, exam or unavailable time. Click existing blocks to edit. Calendar/confirmed-session blocks are read-only. Deleting a manual record requires a separate confirmation and never deletes a Google event.

Small screens initially use day view; week view remains available. A selected view is retained in browser session storage. Date headers navigate to that day's server-calculated availability. The editor supports keyboard focus, Escape, and a focus trap. Touch can tap slots; physical touch input has not been separately verified. There is no drag-to-reschedule exported Session. DST ambiguity is rejected by the same server time parser; DST dates are marked.

The editor uses the team's normal POST/redirect pattern, with a per-session CSRF token and origin check. JSON editor routes use the same save/remove functions and header CSRF. It does not create a second schedule store. Detailed original editors/private records remain in an expandable section and work without JavaScript.

Owner-only routes:

| Route | Behavior |
| --- | --- |
| GET /api/schedule/week?day=YYYY-MM-DD | Private timetable projection; never feed to Matching/model |
| POST /api/schedule/entry | Validated weekly/exam/free/busy save; optional existing record ID |
| DELETE /api/schedule/entry/{kind}/{id} | Remove only the current fixture owner's manual record |
| POST /schedule/timetable | Native editor form, same handlers, CSRF, redirect |

David continues using schedule_core.effective_availability / matching_free_time. The timetable's private labels/JSON are NOT matching output.

## Session read seam and actual team wiring

ExportSession is a Calendar read model, not a new Session table. It carries session_id, organizer_id, participant_ids, accepted_ids for the current revision, revision, UTC start/end, IANA timezone, public course/venue labels, and pending/confirmed/cancelled/expired status. Its content fingerprint binds revision, participants, time, timezone, course and place. Changing any of these invalidates previous approval.

TeamSessionGateway reads the team's existing latest invitation batch and produces a synthetic snapshot with a stable ID/content revision. Its inferred organizer approval, latest-batch-only reservations and global in-memory state are explicitly NOT real acceptance authority. It cannot authorize Google writes.

Marc's real host adapter must provide:

    AuthoritativeSessionGateway(read_session, confirmed_reservations, guard)

read_session returns a validated ExportSession(authoritative=True) from current backend records. accepted_ids must reflect authenticated acceptances of that revision, including the organizer's existing proposal approval. confirmed_reservations must include all active confirmed Sessions, not only the latest demo batch. guard(session_id) is the host's concurrency context/advisory lock preventing concurrent mutation through the final read and executor call. All mutations of those Sessions must cooperate with it. Keep cancellation/expiration tombstones readable for explicit event removal. A browser boolean or a fixture is never authority.

The existing Session interpretation entry and invitation Send handler now invoke refresh_before_request before calendar/matching checks. In the current host this refreshes only known synthetic fixtures. With a real integration it requires a server resolver, app.state.calendar_match_context_for_actor(actor) -> TrustedMatchContext, then refreshes eligible connected users through M2. Matching itself still only reads saved snapshots. This hook is actually called at both existing entry points in tests; no real team coordinator/identity has been exercised.

For the Session card, the authenticated host must also resolve app.state.calendar_current_session_id(actor) from backend ownership. It must not accept an arbitrary front-end organizer or Session object.

## Explicit host composition

Stephen must replace the fixed Profile/calendar owner boundary across the host before attaching real credentials. A demo login cookie is not sufficient. His verified middleware supplies request.state.calendar_actor = GoogleActor(subject_id, timezone, session_binding, csrf_token), and server candidate eligibility/Session-ID resolvers above. Never construct it from query IDs, X-User headers or browser trusted flags.

    active = build_google_integration(
        isolated_repository, authoritative_sessions, profile_timezone_for_user,
        client_file=private_web_client_json_path,
        encryption_key_file=private_fernet_key_path,
        redirect_uri="http://127.0.0.1:8011/calendar/google/callback",
    )
    app.state.google_calendar_integration = active
    app.state.calendar_export_service = build_export_service(active, authoritative_sessions)

Both services must use the SAME authoritative reservation gateway and repository/transaction engine. The builder rejects a seeded demo database and a synthetic Session gateway. No environment flag silently turns the demo into live mode.

## Approval, OAuth and executor endpoints

Only two-person Sessions are export eligible in this phase. Both current acceptances, current organizer, active Session, current content and bound account are checked on the server. Group export is disabled pending the team's Q3 decision. Exams, manual unavailable time and other users' conflicts remain hard. No valid request/revision-scoped organizer override is available in the current team interface, so ordinary conflicts also remain blocked.

The Session card shows waiting/connection/permission/review/adding/added/unknown-or-failed/removing/removed states. Review displays course, exact time/timezone, public venue and the bound Google subject suffix + primary-calendar target. It requests no email/profile scope. Confirm add is independent of invitation acceptance, and confirm remove is a different action. The current synthetic host shows the disabled state; the positive executor flow is tested with server/HTTP doubles.

| Route | Meaning |
| --- | --- |
| GET /calendar/sessions/{id}/preview | Read current server summary/state; no Google writes |
| POST .../{id}/write-permission | Begin incremental consent, only after backend eligibility |
| POST .../{id}/approval | CSRF-confirm fingerprint; kind=export or delete; returns expiring approval_ref |
| POST .../{id}/export | Execute that approval, re-read and refresh/recheck |
| DELETE .../{id}/export | Separately approved removal of the trusted local association |

All mutations require the server actor, header CSRF and same-origin validation. JSON request schemas forbid extra accepted/organizer/calendar/event/Session fields. Only a route Session ID is accepted, then backend lookup and organizer validation determine access. Error messages contain codes, not private events.

M2 initially requests calendar.events.readonly. Only an explicit write-permission action requests calendar.events.owned plus the existing read scope and openid. The [owned-events scope](https://developers.google.com/workspace/calendar/api/auth) limits mutation authority to owned calendars; this application further fixes every event URL to primary. No calendar-management, email or profile scope is requested. An RS256 Google ID token is verified against fixed Google JWKS, issuer, audience/azp, expiry/issue time and single-use state-bound nonce. Only its stable sub is kept encrypted with credentials; no raw ID token/email is persisted. Existing verified account changes are rejected. A first upgrade from old M2 credentials binds the newly verified account and clears the old snapshot atomically.

Consent denial/failure preserves the previous local read grant. Refresh retains both granted scopes and the verified subject. The OAuth callback only updates credentials/returns to the current Session card; it cannot create/delete an event or reuse a stale content confirmation.

## Durability, reconciliation and self-conflicts

Explicit migration core/migrations/003_export_operations.sql creates Calendar-only smcal_exports and smcal_export_approvals. It is applied by ExportRepository.migrate / build_export_service, not during import. Existing v1/v2 scheduling migration remains unchanged. No duplicate user, course, Session or invitation lifecycle tables.

There is one unique logical operation per Session/organizer/primary. Its UUID-derived, persisted stable lowercase-hex event ID meets [Google's insert ID rules](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert). Durable leases/CAS protect concurrent workers, with no Calendar DB transaction held across network calls. Store actual event ID, primary target, owner-only htmlLink, Session revision/content fingerprint, account binding/generation, safe status/error code, timestamps and deletion intent.

Before any new insert, GET the original ID. After a dropped insert response/409, reconcile that ID and verify the app marker and exact expected payload; never generate another event ID. Ambiguity returns HTTP 202 with status=unknown. A successful duplicate/restart retry returns the same event. A missing previously successful event is treated as removed and is not silently recreated. Delete intent survives timeout/restart and blocks export retry from recreating it.

Events contain only backend-confirmed course/time/public venue and a fixed safe description. No attendees, conference links, participant calendars, private titles, emails or private addresses. Google sendUpdates=none is explicit. Deletion fetches and verifies only the associated app-created event, then uses its etag/If-Match per [Google conditional modification](https://developers.google.com/workspace/calendar/api/guides/version-resources). Unrelated/changed resources are refused; repeated verified removal is idempotent. Removing does not change invitations or cancel the Session.

Preflight refresh checks all participants. Never-connected/disconnected candidates may use labeled manual data; a failed connected refresh remains unknown. Only the same trusted Session reservation can be excluded. If verifying an existing app export, its exact trusted Google ID/hash is excluded only for its owner after marker/payload/account verification; another event at the same time still blocks. Reconciliation of an already created event does not create a second event. Cross-system external changes are not one distributed transaction; no absolute race-prevention claim.

## Run/configuration and validation

No new account action is needed for the local synthetic page. No model API key is required by Calendar; do not trigger the team's unrelated Omnara interpret service during live Calendar testing.

    .\.venv\Scripts\python.exe -m pip install -r requirements-calendar-dev.txt -c requirements-calendar-lock.txt
    .\.venv\Scripts\python.exe -m app.scheduling.seed
    .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --no-access-log
    .\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/m3-final-verification
    node --check app/static/calendar.js
    node --check app/static/calendar-export.js

Seed is idempotent and preserves local edits. M3 has no new runtime package beyond installed M2 dependencies; joserfc>=1.7,<2 is now explicitly declared because the identity verifier imports it. Installed versions stay in requirements-calendar-lock.txt. Local DB/credentials, .env files and screenshots stay out of Git.

Configuration names (not secret values): STUDYMATE_CALENDAR_MODE (current host only synthetic), STUDYMATE_CALENDAR_DB, STUDYMATE_TIMEZONE. STUDYMATE_GOOGLE_CLIENT_FILE, STUDYMATE_CALENDAR_KEY_FILE and STUDYMATE_GOOGLE_REDIRECT_URI in .env.calendar.example document explicit host inputs; this file is not automatically loaded. Keys and client JSON are private local files outside Git.

Exact local callback: http://127.0.0.1:8011/calendar/google/callback . Deployed callback is NOT configured or verified because no deployed host was supplied. The team must supply its approved HTTPS hostname, then register the exact https://HOST/calendar/google/callback in Google Cloud and the host builder; do not assume an existing deployment. See calendar-google-readonly.md for Google Cloud pages. Add only owned-events + openid for the later explicit write-consent phase.

Actual test count, command result, browser evidence and final commits are recorded in calendar-integration-progress.md. Tests use generated credentials/keys, MockTransport and explicit Session doubles. They cover A–N from the request, OAuth callback/account verification, current-version/permission changes, lease/idempotency/restart, unknown insert/delete recovery, trusted self-exclusion, privacy, stale sync, editor CSRF/DST and the actual request/Send hook calls.

Real read/write/delete verification remains NOT DONE. Before a real smoke test, identity and Marc's gateway must be ready, then show the exact consenting account, test event title/date/time/place and action. The user performs Google consent and final confirmation or expressly approves that single test. Deleting that test event requires separate approval. No real mail or deployment is part of this task.
