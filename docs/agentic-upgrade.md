# StudyMate custom-tool upgrade

## Current result and history

This branch extends the existing study loop; it does not replace Calendar or David's scoring algorithm. The starting remote branch was `shuluun` at `363a5bd` (including `116ffa6`, the prior study-loop implementation). The clean, unmodified baseline was reproduced from that commit: **181 passed, 2 dependency warnings, 16.01 seconds**. No existing uncommitted work was discarded.

During this work the user separately requested the screenshot's final Calendar version be put on main. **Calendar-only PR #3 was merged**, yielding main `6e3daa6124216aea583928c811a42ed43ff731a3`. Main's file tree matches Calendar `a07b98a` exactly, excludes Agent code, and independently passed **140 tests in 17.04 seconds**. Its actual browser create/edit/remove, week/day view, effective-free updates, refresh and process-restart persistence were verified on port 8016. The user's existing server on port 8011 was retained.

The Agent upgrade remains an independent feature branch and Draft PR against main. Calendar is now a merged dependency. PR #2 remains open and was not merged wholesale; its existing Agent changes overlap this branch. Review this complete upgrade before deciding whether to supersede the overlapping PR.

After publication, Stephen updated main to `61c0223` with Profile changes. Those changes were preserved and normally merged into this feature branch. Latest clean main passed **176 tests, 2 warnings, 14.01 seconds**; the complete Agent + Profile + Calendar branch passed **236 tests, 2 warnings, 23.06 seconds**. `pip check`, `compileall` and diff checks passed. The current user-facing 8011 timetable was also rechecked through actual create -> edit -> refresh -> remove; only this audit's temporary record was removed, while the user's existing exam and extra-time records remained.

**Live Omnara is not verified.** Only the approved project/process configuration locations were checked. `OMNARA_TOKEN`, `OMNARA_ORG`, `OMNARA_PROJECT`, `OMNARA_MODEL_PROVIDER` and `OMNARA_MODEL` were all absent; there was no project `.env`. No credential values were printed or sought elsewhere. HTTP tests use fake Omnara with actual local scheduling/scoring/place functions and synthetic business data. No real model, Google, email, booking, machines, billing changes or deployment occurred.

## Implemented flow

`/session` offers basic, agentic and synthetic/test modes, plus a manual form. A request is persisted before planning. Requests may also be opened at `/agent/{id}`; Session can render that same request with `?agent_request_id=...`. The existing timetable and manual Session workflow remain available.

- **Interpret** receives the current date, IANA timezone and an explicit course/style/group/place whitelist. The fixed October 24 production prompt and shared `OMNARA_AGENT` history are removed. ISO times, explicit minutes and simple exclusions are captured conservatively before the model. Other language is interpreted by Omnara; missing course/window/duration produces exactly one backend question. Interpret has no business-tool permission, even when the model requests a call.
- **Advise** chooses among the four allowed read tools, then returns only a selection of candidate IDs, a start instant and a returned venue ID. The server constructs the final card from facts; arbitrary model-written invitation/reason text is rejected. This is an intentional minimum extension for privacy and grounding. The model can select a near-tie candidate or an explicitly permitted alternative type; the displayed comparison uses the actual score components.
- **Validate** is an independent backend call after model completion. Every selected person must come from this request's score result and evidenced slot. The entire group must have fresh common availability for the full duration, within the original window. Exams and all calendar/session blocks remain hard. Eligibility, course, travel radii, full-slot hours, venue provenance, content fingerprint and request revision are checked. Existing Matching now refuses infeasible venues instead of ranking them last and still making a card.
- **Approve** rereads evidence and binds approval to the exact request revision/content fingerprint. A separate **Confirm synthetic invitation** button uses the existing `/session/confirm` executor entry. Only server-loaded people/time/place/text are passed onward; browser candidate IDs/text are not authority.
- **Repair before Send** refreshes the synthetic snapshots through Calendar's existing hook, rereads the proposal and invalidates old approval if availability/eligibility/place/content changed. The same isolated agent receives a redacted reason (`PROPOSAL_EVIDENCE_CHANGED`, previous end, revision, new approval required), with the original goal and remaining budget. A replacement needs new approval. No stale invocation reaches the executor.
- **Stop** persists cancelled state first, blocks further local tools/executors and requests remote current-work cancellation. A failure is shown as remote cancellation unconfirmed. No archive/delete API is used. An automatic approval review rejected an earlier proposed permanent-archive binding; the shipped implementation uses the safer cancellation path.

The current executor creates **synthetic invitation records only** using the existing team store. It does not send real email, provide authoritative acceptance, or enable calendar writes. The dispatch claim prevents automatic retries after an ambiguous executor failure; unknown delivery must be resolved manually. An authoritative external transaction/guard remains necessary for production send-time race protection. Calendar M3's trusted identity, separate confirmation and idempotent writer gates are unchanged.

## Four external tool contracts

The exact JSON Schemas, output models, limits and timeout targets are in [`config/study-agent-tool-contracts.json`](../config/study-agent-tool-contracts.json). The deployable source template is [`config/omnara-study-agent.example.json`](../config/omnara-study-agent.example.json), generated from the same Python definitions. Config contains only these four custom tools with read permission, no built-ins, MCP servers, machines or subagents. Legacy demo-only validation helpers are not in the external registry.

| Tool | Input and authority | Safe result | Lifetime limit / target |
| --- | --- | --- | --- |
| get_profile | Empty object; current trusted request owner only | Allowed course IDs, style, group size, allowed place types | 1 / 1 second |
| find_matches | Optional previously scored exclusions and a narrowed not-before bound; saved goal is authoritative | Top-five IDs, sanitized first name/year/major, shared course, real full-duration intervals, scores/components, versions | 2, changed goal/arguments required / 2 seconds |
| suggest_places | Previously scored group, evidenced full slot and authorized type | Only feasible seed venues, public address/map/hours, synthetic provenance; no_feasible_place is normal | 2 / 1 second |
| check_booking | A venue ID returned in this request | unsupported, reserved=false | 1 / 1 second |

Both input and output Pydantic models forbid extra fields. Every tool has an explicit registry entry; there is no eval, arbitrary import/getattr dispatch, SQL, HTTP, shell, filesystem, email, calendar or booking-write capability. No tool accepts a target user ID. Private coordinates are used server-side for distance/fingerprints only. No raw Person/Profile/calendar/provider object is returned to Omnara. Missing opening-hour facts are unknown and cannot become a formal venue.

Current business adapters deliberately support only the fixed fictional Maya actor. Other registered demo users are denied, instead of receiving Maya's data. Repository and agent mapping ownership are tested, and parallel supported requests have independent cloud histories/ledgers. **Production concurrent-user business isolation remains unimplemented** until Stephen provides per-user Profile/session adapters. A second production identity system was not invented.

## Omnara protocol and recovery

Official references checked for this implementation:

- [Custom tools](https://docs.omnara.com/tools/custom): `GET /tool-calls?state=ready&type=custom`, then `POST /tool-calls/{id}/result` with outcome and structured_data content blocks.
- [Permissions](https://docs.omnara.com/tools/permissions) and [config](https://docs.omnara.com/agents/configuration): explicit four-tool configuration; no provider integrations or compute resources.
- [Agent launch/lifecycle](https://docs.omnara.com/agents/overview): immutable reusable config_source, one isolated agent per request, launch idempotency and cancellation. A retried launch can return only the agent; the adapter recovers only a matching owned initial input from events.
- [Events](https://docs.omnara.com/events/streaming) and [input](https://docs.omnara.com/events/sending-input): save input/turn IDs and sequence cursor. Intermediate tool_use/max_tokens output is ongoing; only current-input end_turn JSON is finalized. Error/refusal/control cancellation is handled; unknown states reach the bounded timeout. Reasoning blocks are not saved or rendered.

SQLite persists request -> owner -> agent, input/turn history, cursor, exact goal, revisions, sanitized evidence and call budgets. `smagent_calls` binds `(agent, tool_call_id)` to owner/request/input signature, with executing/executed/submitted states and stored safe results. A duplicate ready call neither reruns nor reposts a submitted result. An interrupted read with no stored result returns a structured interrupted failure rather than rerunning silently. Uncertain POST submission is reconciled against the server's exact call state; 409 alone is not success. A changed signature/owner/agent mapping is refused.

Each request has **6 tool batches and 8 model responses total**, including Interpret, clarifications, edits and repair. Per-tool counts and normalized query signatures never reset on retry/restart. Active polling has a 20-second attempt limit, 60-second request total, at most 40 HTTP requests per attempt and bounded HTTP timeouts. Local SQLite/Python work uses elapsed checks; it is not forcibly preempted. Remote cancellation and model-count enforcement are cooperative polling controls, not a guaranteed provider-side cost ceiling. At most four planning attempts and eight clarification responses provide additional bounds. Unknown/provider/tool errors are visible failures, not empty matches. Basic/manual fallback is explicitly labelled and makes no model call.

Planning runs synchronously in the normal FastAPI thread pool with a persisted attempt lease and optimistic version fence. A crashed process leaves a visible planning request; an explicit retry after lease expiry resumes with the same agent/cursor/ledger/budget. **No reliable durable worker queue, multi-host distributed lock, SSE delivery guarantee or automatic resume is claimed.**

An identical-content Calendar refresh can change the snapshot sequence; this alone does not cause an endless approve/refresh loop. The approval fingerprint binds actual sanitized blocks, schedule/profile content, venue/goal/selection and revision; freshness and current snapshot metadata are still reread at every gate.

## Reproduction and configuration

Actual Windows runtime: Python 3.13.5; FastAPI 0.162.0; Pydantic 2.14.1; httpx 0.28.1; Uvicorn 0.54.0; pytest 9.1.1; SQLAlchemy 2.1.6; Authlib 1.9.1; joserfc 1.7.7. No new dependency or model framework was added. Existing lock/dev files are used.

From the team checkout:

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = Join-Path (Get-Location) '.tmp'
$env:TMP = $env:TEMP
.venv\Scripts\python.exe -m pip install -r requirements-calendar-dev.txt -c requirements-calendar-lock.txt
.venv\Scripts\python.exe -m app.scheduling.seed
$env:STUDYMATE_AGENT_DB = 'data/agent-upgrade-synthetic.db'
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8015 --no-access-log
.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/agentic-tests
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m app.study_agent.smoke
```

The smoke defaults to the named synthetic November 5 fixture window, not today's real schedule. It does not approve or dispatch. `--live` opts into one bounded real Omnara run only when all five authorized process environment variables exist; it uses synthetic business data and cannot provision compute or change billing. It prints status/mode/tool names/budgets, never credentials. Credential-less `--live` exits with not_configured, live_model_verified=false. A successful fake HTTP test must never be reported as live_model_verified.

`.env.agent.example` documents the five names; this host does not automatically load .env files. An authorized account/project owner must configure an existing model/provider and server bearer token securely. Config-source launch requires the appropriate Omnara project permissions. Never paste tokens into chat, commit them, or put them in browser fields. `OMNARA_AGENT` is ignored. Production Google OAuth is a separate integration and is not required for this smoke.

## Demonstration scenarios and evidence

Normal: basic mode, `CS 61A, 2026-11-05T18:00 to 2026-11-05T21:00, 90 minutes, library`. The actual browser produced Jordan / Doe Library / 18:00–19:30 with full 90 minutes and actual get_profile, find_matches, suggest_places timings; it remained after a real process restart. This was deterministic basic mode and synthetic business data, not a live model.

Repair: tests approve a 60-minute card, add an actual synthetic candidate busy block, and call the real Session confirmation path. No invitation is created from the stale approval. The same goal produces a new revision with approval cleared; the old confirmation returns 409. Explicit approval/confirmation of the replacement calls the synthetic executor once, and replay does not duplicate it. The tool budget remains cumulative.

The HTTP replay additionally exercises model -> ready custom call -> actual local tools -> structured results -> backend-validated proposal, independent parallel request histories, duplicate calls, interrupted receipts, launch reconciliation, explicit stop, errors and model limits. Missing fields, no match, 90-minute duration, unauthorized type/radius expansion, closed/unknown/outside venues, injection/forged IDs and original Calendar regressions are covered. A checkpoint passed **199 tests, 2 dependency warnings, 29.56 seconds**; pip check/compileall/diff checks passed. Synthetic CLI smoke produced a 90-minute proposal using get_profile/find_matches/suggest_places; credential-less live smoke returned not_configured without calling a model. Final counts are also recorded in the PR validation section.

## Requirements, extensions and pending decisions

**Original PRD:** four read tools and their per-request limits; Interpret/Advise split; fixed hard calendar/exam/radius constraints; synthetic place provenance; unsupported booking; current-user privacy; exact confirmation and send-time recheck/repair; 6/8 round limits; manual fallback.

**Minimum extensions in this branch:** explicit window versus duration in Goal (shared StudyRequest remains compatible); allowed alternative type list; independent typed decision/validator; persistent call ledger; cumulative cross-repair budgets; content/revision approval and a synthetic versioned Send seam; canonical server-generated invitation copy and score explanation.

**Still team decisions/work:** Stephen's real identity, session binding, timezone and per-user Profile adapter; Marc's authoritative revision/reservations and cooperating transaction guard, real email idempotency/delivery resolution and Google Session composition; David's production eligible pool/scoring source and any intentional changes to group policy; actual Places/hours adapter; per-user storage/retention and PostgreSQL migration; provider-side spend/rate controls and near-tie UX. No real identity, delivery, Google, venue or production-readiness claim is made by a passing synthetic test.
