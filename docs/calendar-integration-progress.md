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
