# Schedule API integration

The shared skeleton now calls the engine through `app/schedule_core.py`. Its demo contracts and `/api/schedule/availability` endpoint are documented in [the skeleton guide](skeleton_schedule_guide.md). The authenticated multi-user API below remains a separate app and is not yet wired to the skeleton's in-memory store.

Use the FastAPI OpenAPI document at `/openapi.json` for exact input schemas. All absolute datetimes require offsets. Weekly slots use local wall times in the owner's IANA timezone; weekday 0 is Monday. Intervals are half-open. Queries are bounded to the next 14 days.

## Authentication and data ownership

`authenticated_user` is an injectable FastAPI dependency. In the local service it resolves configured bearer tokens to users, ignoring body-supplied identities. The team application should replace it with its real login dependency. An owner never supplies another user's id to an owner route.

`X-Schedule-Service-Token` protects internal matching and accepted-session projection. Keep it server-side. The caller is responsible for matching consent, eligibility and authoritative invitation responses. Never pass this credential or the private owner schedule to Omnara/model context.

| Route | Caller and effect |
| --- | --- |
| `GET /schedule/me` | Owner reads own saved hours, exams and imported busy titles |
| `PUT /schedule/me` | Owner saves full hours/exam data, with `expected_version`; omitted collections are cleared |
| `POST /schedule/me/availability` | Owner gets free intervals and exam priority metadata |
| `POST /schedule/me/check` | Owner sees own conflicts and next alternative; explicit `study_anyway` can issue a limited calendar-only override |
| `POST /internal/availability` | Trusted matching service gets shared continuous slots; no titles or full busy calendar |
| `PUT /internal/confirmed-sessions/{id}` | Trusted session service supplies versioned, fully accepted session projection |
| `POST /calendar/google/connect` | Owner begins read-only OAuth; `enable_calendar_write=true` requests incremental write scope |
| `GET /calendar/google/callback` | One-use state, PKCE and HttpOnly browser-flow nonce are verified before storing credentials |
| `POST /calendar/google/refresh` | Owner refreshes imported busy data |
| `DELETE /calendar/google/connection` | Owner deletes local credentials and imported event titles; Google account grant itself is not revoked |
| `GET /sessions/{id}/calendar-preview` | Organizer reviews exact accepted-session content; returns a short-lived confirmation token |
| `POST /sessions/{id}/calendar` | Organizer confirms that exact token and content; server revalidates and creates/verifies the private primary-calendar event |

## Matching example

```json
{
  "participant_ids": ["demo-alex", "demo-bella"],
  "start": "2026-10-26T14:00:00-07:00",
  "end": "2026-10-26T18:00:00-07:00",
  "duration_minutes": 60
}
```

Supply the current demo date returned by `/demo-info`; the fixed date above is only for replay with that reference clock. The result includes continuous common windows, versions and whether calendar evidence is connected/refreshed. `not_connected` means only published hours are known, not that Google has verified an empty calendar.

An owner-only check for an exact conflicting interval can return `override_token`. To use it, the trusted matching request includes that same `organizer_id`, `request_id`, interval and `owner_override_token`. It removes only that owner's calendar blocks for that one calculation. A new conflict, edited hours, different interval/request, expired token or changed owner invalidates it. Other users' busy times, exams and explicit removals remain hard stops. Pass the token on the corresponding trusted confirmed-session projection if that proposal is accepted.

After all humans accept, upsert `SessionProjection` with `id`, `organizer_id`, unique `participant_ids`, the same `accepted_ids`, `subject`, `place`, aware `start/end`, `request_id` and increasing `version`. Update the projection when the authoritative invitation state changes; do not present an old accepted version as current. This projection endpoint does not create invitations or impersonate acceptance.

The calendar preview token binds the exact session payload and version. `POST` requires `{"confirmed": true, "confirmation_token": "..."}`. The event id is stable per organizer/session; a repeat verifies the existing event, and changed content is rejected rather than silently updated. `202 UNKNOWN` means an outcome is unresolved, not successful or failed; preserve the session id for reconciliation.

## Failure and deployment behavior

Failed or partial Google reads retain previous busy data and return an error. A changed schedule returns 409 instead of overwriting a newer revision. Disconnect invalidates in-flight OAuth states and token refreshes. OAuth refresh credentials are never carried over to a newly authorized Google account without evidence they belong together.

Use a stable encryption key and signing key across production workers, HTTPS, least-privilege credentials and the team's migrations/authentication. The SQLite demonstration does not validate Postgres concurrency or a live provider. Import the module into the team's backend or route to this service server-to-server; avoid exposing the internal routes as browser tools.

Google references used for the adapter: [events list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), [events insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert), [events get](https://developers.google.com/workspace/calendar/api/v3/reference/events/get), [OAuth web server flow](https://developers.google.com/identity/protocols/oauth2/web-server).
