# Calendar integration execution record

2026-10-24. Original standalone M0/M1 stays at the parent workspace, untouched. Team checkout: `team-integration/Agent_Hackathon`. Local implementation branch: `feat/shulun-calendar-integration`; remote destination requested by the user: their existing `shuluun` branch. No main push, PR merge, deployment or real calendar operation.

## Checkpoints

- Standalone baseline: **60 passed**, 1 warning. Initial unmodified team main `03ef6c4`: **21 passed**, 1 warning.
- M1.5 commit `0e1f0e9`: shared persistent scheduling, compatibility facade, forms, sanitized Matching, hard conflicts and full-duration alternatives. **67 passed**, 1 warning. Browser saved weekly hours and edited an exam; actual server restart retained both.
- M2 commit `22e200d`: Google Web OAuth/provider/vault, atomic read-only sync, refresh coordinator and default-disabled live boundary. **99 passed**, 2 warnings with mocked Google/OAuth HTTP only. No real account connected/read; writes/deletes unavailable.
- Remote main advanced to `c69e063` (landing/login/Session). A clean detached worktree ran its unchanged suite: **20 passed, 1 failed**, because the API smoke test did not sign in after login was introduced. Merge `d9afb2c` preserves all new team code and Calendar guards. Only that original smoke test adds actual demo login; its assertions and the 20 algorithm tests stay intact.
- Final integrated suite: `.venv/Scripts/python.exe -m pytest -q --basetemp=.tmp/final-calendar-tests`: **102 passed, 2 warnings, 6.09s**. Starlette/Authlib httpx adapter deprecations remain visible. `.venv/Scripts/python.exe -m pip check`: **No broken requirements found**. Lock includes latest main's itsdangerous.
- Browser on latest main: demo login works; restart retained Monday 13–18 and the CS61A exam 20–20:30 on Oct 29 with explicit priority. Synthetic refresh yields 16–18, 19–20, 20:30–21. Session 20–20:30 with study_anyway=1 is blocked and offers 20:30–21. No model call was made; automated interpretation uses a mock.

## Version preservation and publication

The user requested this new implementation, independent of the older `shuluun` code, then a new version on their branch. Prior version: `ee427638b1e3aa6f6e3c0b4197ba9af826979886`. Preserve it as an ancestor using an intentional history-only merge, retaining the completed main-based v2 file tree. Then use a normal fast-forward push to `shuluun`, never force-push. Old code remains recoverable by commit; new code has separate commits. Latest main `c69e063` also remains an ancestor.

Publication confirmed: normal fast-forward push `ee42763..e33e073` to remote `shuluun`. `e33e07315b8dd3a310486e5509d02b99d48ec87c` has the exact v2 tree from tested code commit `2b85884`; the history-only merge changed no files. Both prior shuluun and latest main are ancestors. Audit passed for 50 scoped paths, forbidden credential/database paths and credential signatures; protected team Profile/contracts/store/styles/Omnara files match latest main and the working tree was clean.

Draft PR: https://github.com/2635530141/Agent_Hackathon/pull/2 . Verified OPEN, isDraft=true, head=shuluun, base=main, mergeStateStatus=CLEAN. GitHub connector returned 403 for PR creation; authenticated GitHub CLI created it successfully. The PR is attached to this Codex task. No remote CI checks are configured/reported; the 102-pass result is a local run, not a CI claim. This publication-log update is a subsequent documentation-only commit. No PR is merged or deployed.

## Remaining integration gates

Stephen: verified user/session/CSRF/timezone and real per-user Profile/calendar isolation. David: read saved sanitized availability; no provider calls in Matching. Marc: authoritative reservation/revision/acceptance gateway, refresh at Session start and before Send, version recheck and independent organizer export approval. Proposed override/group-acceptance policies need team agreement. Postgres, real OAuth UI/private reads and all real writes are unverified. See `calendar-integration-handoff.md` and `calendar-google-readonly.md` for exact calls/setup.

Executed seed: `.venv/Scripts/python.exe -m app.scheduling.seed` (existing edits preserved). Running locally: `.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011 --no-access-log`. Only synthetic data is served. No user terminal work is required.

## M3 and timetable continuation

Rechecked workspace/Git/PR and reran the baseline: 102 passed, two warnings. No real Google authorization or authoritative team Session API exists. The current demo login does not establish real per-user isolation.

Implemented M3: Session read model/adapter, server acceptance/revision/organizer gates, independently approved export/removal, encrypted stable Google-subject binding via incremental owned-events + openid consent, persistent operation/approval tables, UUID-derived stable event ID, leases/CAS, unknown-result reconciliation, etag deletion and durable delete intent. Group exports and conflict overrides remain disabled. The existing interpretation and Send handlers actually invoke the refresh hook; live gateway/identity/resolver wiring remains the team's dependency. Matching still reads saved data only.

Implemented timetable: original design tokens, week/day grid, date navigation, overlap lanes, effective-free markers, click/drag selection, weekly/exam/free/busy editing, explicit manual-record removal, keyboard/Escape/focus handling and native CSRF-protected POST/redirect forms. Original detailed editors remain accessible. No drag-rescheduling of exported Sessions.

Browser verified: created Oct 29 13–14, edited to 13–14:30; dragged Oct 28 10–11:30, changed it to unavailable, saved it, then explicitly removed that test record. Only fictional local test data changed. The Oct 29 extra-free block remains as a local verification example. No Google/model/mail call occurred.

Most recent pre-final check: 138 passed, two known dependency deprecations, 11.03s; node --check for both Calendar scripts passed; pip check passed. A subsequent regression test checks HTTP 202 explicitly returns status=unknown; final suite/publication result is recorded below after execution. See calendar-writeback-handoff.md for calls, migrations, configuration, unresolved policy and live-vs-mock boundaries. calendar-team-message.md is prepared text only; it has not been sent.

Final verified M3 suite: `.venv/Scripts/python.exe -m pytest -q --basetemp=.tmp/m3-final-confirmed` -> **140 passed, 2 warnings, 14.93s**. Both `node --check` commands pass; pip check reports no broken requirements. Added coverage includes HTTP 202 status=unknown and preventing an existing export from moving to a different Google account.

Actual final Uvicorn stop/start retained the Oct 29 13–14:30 manual addition, original weekly hours and exam. Browser refreshed synthetic snapshots and displayed all seven weekday columns with the original StudyMate design, study hours, effective-free markers, lecture and exam. Final screenshot is local/ignored at .tmp/calendar-m3-week.png. Physical touch, real OAuth consent/UI, live Google writes/deletes, Postgres and authoritative team integration remain unverified.

Final pre-push fetch: origin/main still c69e063; origin/shuluun still da61959 and PR #2 still OPEN/Draft. This phase will append its own scoped commit to shuluun and update that same PR, without force push, merge, release or deployment. Publication is confirmed below after the remote operation.

Publication confirmed: tested code commit **c3f9ec656d121b6238b9e5e5dc26f4bc8b22c043**, normal push da61959..c3f9ec6 to shuluun. Same PR https://github.com/2635530141/Agent_Hackathon/pull/2 updated to “Calendar: persistent scheduling, confirmed export and interactive timetable”. Verified OPEN, Draft=true, base=main, head=shuluun/c3f9ec6, CLEAN, no reported CI checks. Commit audit passed for 39 task paths; no credential/database paths or credential signatures, all unrelated Profile/Matching/store/Omnara/contracts/global styles unchanged. This publication-log/handoff clarification is a subsequent documentation-only commit. No PR merged, message sent, or deployment performed.
