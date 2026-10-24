# Schedule and calendar requirements

Authority: the user-supplied PRD copied without changes to the desktop engineering folder `01_plan/PRD.docx` on 2026-10-24. Earlier locally drafted PRDs are background proposals; they do not override this team document.

The document explicitly assigns **2. Schedule and calendar: shulun**. This change provides the deterministic schedule implementation and optional calendar adapter, with the following integration limits. It does not establish that every live-calendar requirement is complete. The application's one-agent Python state machine belongs to the shared matching/session application.

| PRD requirement | Implementation |
| --- | --- |
| Exam with course, date and time; exam blocks learning | Owner exam records; hard subtraction from availability |
| Days before an exam are a priority window | Soft metadata from local midnight N days before the exam until its start |
| Recurring weekly hours | IANA-local weekday hours, including overnight slots |
| Add or remove individual slots | Absolute aware one-off additions and removals; busy constraints win |
| Google Calendar read-only for next two weeks | OAuth read grant and events-list adapter with pagination, transparent/canceled/declined filtering and all-day handling |
| Event titles visible only to owner | Authenticated standalone API isolates titles; skeleton match projections redact titles, but its owner page still uses a shared synthetic identity |
| Refresh busy times when a request starts | Freshness window and coverage checks; confirmed export re-fetches connected participants' calendars |
| Confirmed session added to organizer calendar only after confirmation | Trusted accepted-session projection, exact preview token, organizer-only executor, separate write scope, no attendees |
| Seed without Google | Isolated synthetic demo with a week of hours, exam and busy blocks |

## Decisions resolving conflicting wording

The document also uses “fully autonomous” in a general paragraph. Its concrete architecture, state table and user stories require the Approve button and an ordinary executor. Those specific requirements govern writes: the model has no calendar write tool and cannot approve on either student's behalf.

The feature list says exam hours cannot overlap a study session, while the session screen allows “study anyway” for one's own calendar conflict. This implementation treats exams and explicit manual removals as hard exclusions. Only an explicitly confirmed conflict from the organizer's own imported calendar can be overridden, for the exact request and interval. Other participants' calendar conflicts remain hard stops. The override never modifies saved hours.

Ambiguous or nonexistent recurring wall times over daylight-saving transitions are rejected. The owner must use an explicit offset in a one-off interval. Missing or failed calendar data is never converted into a claim that the student is free.

The PRD describes eventual email invites and optional booking. This module neither sends email nor reserves space. Those executors need their own exact approvals and team implementations. Real email/calendar accounts were not accessed during development.

## Remaining integration work

Stephen's profile module must provide user identity and matching consent. Matching must select eligible, opted-in participants before calling the internal availability tool. The session/invitation service must supply authoritative acceptance and cancellation updates. Internal API credentials are not proof of a human acceptance; they authenticate that trusted backend, which owns the acceptance evidence.

The team's Google OAuth project, registered redirect URI and credentials are not created by this code. The skeleton schedule form and matching integration are implemented. Live Google OAuth, refreshing real calendars through the skeleton, PostgreSQL deployment, authoritative invitation acceptance, email and Omnara inference remain unverified or unwired. Real Google integration must use the authenticated API, not the skeleton's shared demo identity.
