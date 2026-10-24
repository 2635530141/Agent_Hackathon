# Study planning loop

This extends Calendar v2 on `shuluun`, starting from `da61959`. It preserves team Profile, Matching, Session and calendar guards. `/agent` is linked from Session and the shared navigation. No new invitation/session lifecycle, email sender, booking connector or calendar writer is introduced.

## Repository review before implementation

GitHub was fetched and inspected on October 24, 2026. `main` was `c69e063`, `shuluun` was `da61959` (7 commits ahead, 0 behind main), and `stephen` was `f74bacc` (3 unique commits, diverged from main). Stephen's profile/eligibility/geocoding changes were read but not merged or overwritten. PR #2 was an open draft, mergeable, with no reviews or inline review comments. There were no separate open issues. The only reported Actions run passed on the historical `ee42763`, not the current v2 head; v2 had zero check runs and no workflow file. Unmodified v2 was reproduced here: **102 passed, 2 dependency deprecation warnings**, Python 3.12. The first test attempt lacked the local `.tmp` parent; creating it resolved the environment error without changing source.

Current scope boundaries: seeded demo authentication, shared fixture Profile and in-memory Session are not production per-user identity. Live Google is gated. Existing Session sentence interpretation is separate legacy code; it is not the new custom-tool loop. The new loop does not turn its preview approval into the legacy Session's unchecked Send parameters.

## Behavior

1. Save an opaque, owner-bound request in SQLite, with course, exact time window, desired duration, place preference and exclusions. The window and duration are separate. Reuse known group size/study preferences; do not ask for them again.
2. Ask one missing-field question at a time. In local mode there are no read-tool calls until course, exact window and duration are provided. Unknown courses, invalid/reversed windows, windows shorter than duration and ambiguous DST wall times are rejected.
3. Execute read tools, retaining structured results and elapsed time. A proposal must reference the exact evidence ID returned by `validate_proposal`; model-supplied people, places and invitation prose are not accepted.
4. Present a preview with provenance and a separate approval button. On approval, reread scoring, full-duration availability, current eligibility, shared course, place feasibility and version/fingerprint evidence. Approval binds to this revision. Repeated confirmation is idempotent and a stale page cannot approve a new revision.
5. If evidence changes, invalidate the old proposal and approval, preserve the goal, and replan. No match means ask the student to change constraints, never silently shorten duration, reduce group size, leave the window or relax an exclusion.
6. Stop cancels local progress at a boundary and requests best-effort cloud cancellation. State survives a restart; an expired planning lease permits explicit retry, with attempt/version fencing against an older worker.

The approve button approves a **preview plan only**. There is no delivery, reservation or authoritative session acceptance here. Calendar export and organizer overrides remain disabled. Connecting this evidence revision to Marc's executor/reservation transaction remains a separate integration task; no claim of solving send-time race conditions is made.

## Tools

| Tool | Purpose | Permission / source |
| --- | --- | --- |
| `set_requirements` | Extract explicitly stated fields for this request | Request-state update only; prohibited during locked replanning |
| `get_profile` | Reuse known course/style/group/place preferences | Read; excludes email, address, coordinates and gender |
| `find_matches` | Deterministic top-five candidates and complete free intervals | Read; existing scorer and saved sanitized calendar API |
| `check_availability` | Check every minute for all participants, with versions and verification | Read; unknown is not free |
| `suggest_places` | Feasible open places inside everyone's radius | Read; seeded fixture hours/ratings, clearly unverified live |
| `validate_proposal` | Recheck IDs, full slot, place and evidence fingerprint | Read; produces a server-built canonical card and invitation preview |

Matching makes no provider calls. No private calendar title, participant email, street address or home coordinate enters model inputs or tool traces. Private coordinates are used only on the server for radius checks/fingerprint hashing. Tool results remain untrusted input, and the model has no external-write tools. Canonical copy contains only selected course, public first names, time and place facts.

`find_matches` gains an optional explicit duration; old callers retain their prior overlap policy and all scoring weights. This prevents a 30-minute goal in a three-hour search window from inheriting the legacy one-hour minimum, and filters full-duration feasibility before the top-five cutoff.

## Two explicit planner modes

**Local rule demo** makes no model call. It uses a small parser for known course codes, two ISO timestamps, `60 minutes`, place types and `not a library`. Other wording leads to the missing-field form. This policy and conflict simulator execute real tools against saved synthetic data; they are not evidence of LLM reasoning or a live provider connection.

**Omnara** creates one isolated agent per request with only the six custom tools. It retains the cloud agent ID/cursor across clarifications and replanning, posts structured tool results, and accepts only current-turn final `end_turn` text. Partial/max-token outputs continue; reasoning blocks are neither displayed nor retained. Unconfigured, failed or timed-out cloud mode shows an error, with no silent local fallback. Config is built from an explicit model/provider name, never a shared global conversation. The existing `OMNARA_AGENT` is not reused.

Server environment: see `.env.agent.example`. These variables are read from the process environment, not automatically loaded from a file. Model/provider names must already exist in the authorized Omnara project. No credential is sent to a browser. The adapter follows [custom tools](https://docs.omnara.com/tools/custom), [agent launch](https://docs.omnara.com/agents/overview), [config](https://docs.omnara.com/agents/configuration) and [events](https://docs.omnara.com/events/streaming). HTTP replays verify the contract path; **live model access has not been verified**.

## Budgets, failures and evidence

Each planning attempt has at most 6 dispatched custom calls, 8 cloud model responses, 40 HTTP requests and a 20-second planning deadline (best-effort cancellation can add up to 2 seconds). A request allows at most 4 complete planning attempts and 8 clarification responses. Replanning starts a new bounded attempt; this is an explicit revision-level interpretation of the PRD's six-round limit, not an unlimited conversation. There is no asserted dollar ceiling: provider prices and charges are not yet integrated. Local rule mode has zero model-token cost. Live p95 latency/cost targets remain unmeasured.

Unknown tools/arguments, invented entities and invalid proposals are rejected. Invalid tool calls return safe error evidence; cloud results mark them failed. Tool loops stop at the budget; provider failures stop the request. No place fitting the hard preference yields no match. Stale/incomplete calendar evidence yields no eligible slot; the student can explicitly refresh the synthetic snapshot (not a Google refresh). Unexpected internal failure leaves a visible retryable error. No exception body or hidden reasoning is copied to the user trace.

CSRF tokens and origin checks protect new mutations. Run IDs are opaque; repository reads bind owner. Only the existing seeded fictional owner can access these routes. This does not repair the team's demo cookie secret or implement production campus authentication. Databases are ignored by Git. Request input is limited to 1000 characters; event history is capped at 100 entries. A production retention/deletion policy, per-user identity and PostgreSQL transaction checks remain pending.

## Reproduce and demonstrate

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-calendar-dev.txt -c requirements-calendar-lock.txt
New-Item -ItemType Directory -Path .tmp -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/agent-tests
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8014 --no-access-log
```

Log in using the displayed synthetic account, then open `/agent`. Start with `CS 61A` to see clarification, or `CS 61A, 2026-10-29T18:00 to 2026-10-29T21:00, 60 minutes, not a library` for a complete request. Refresh synthetic snapshots after five minutes of idle time. Review tool evidence, approve, open **Conflict demo controls**, simulate a new candidate conflict, and recheck. A different person/time must appear with the old approval cleared; approve the replacement separately. Remove the simulated block afterward. The simulator is a human-operated demo control and is not an agent tool.

Evaluation covers complete requests; missing course/window/duration; no overlap/full-duration enforcement; hard place exclusion; groups; calendar unknown; changed evidence and approval invalidation; idempotent/stale confirmation; persistence/owner isolation; private-field redaction; forged entities and tools; looping, timeouts and cancellation; CSRF/origin checks; and native Omnara HTTP replays. These are deterministic offline checks, not the PRD's online acceptance-rate or full live-provider golden evaluation. Exact current test/browser/publication evidence is recorded after validation.

Pre-integration validation against `da61959`: **142 passed, 2 dependency deprecation warnings**; pip check and diff check passed. Browser completed course-only -> window clarification -> duration clarification -> actual tools -> 19:00–20:00 proposal -> approve -> simulated candidate conflict -> 20:00–21:00 replacement with approval cleared. Both slots are within the original 18:00–21:00 window, with the full 60 minutes. The browser check also corrected whole-document replacement to ordinary navigation and collapsed the requirement editor once a proposal exists. No real provider action occurred.
