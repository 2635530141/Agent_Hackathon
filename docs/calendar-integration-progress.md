# Calendar integration progress

2026-10-24. Original standalone M0/M1 preserved at the parent workspace. New team checkout starts at `origin/main` commit `03ef6c415acebe03458a02df110aa474161ac190`. Branch: `feat/shulun-calendar-integration`. Authenticated GitHub account has push access; no login action needed.

Verified baselines: original standalone 60 passed (1 dependency deprecation warning); unmodified team 21 passed (same warning). No team AGENTS.md exists at this baseline; inherited calendar scope/safety rules apply. Team auth and Postgres are not implemented; fixed owner and global Profile/Session state remain synthetic only.

M1.5 in progress: vendor the existing core under app/scheduling/core, use schedule_core.py as a compatibility facade, preserve starts_at/ends_at and the team UI. Add one scheduling repository read by owner routes and Matching, explicit local timezone conversion, persistent CRUD, hard blocks and full-duration alternatives. Ordinary organizer overrides stay disabled pending a request/revision authorization gateway. No production identity/session system, scorer rewrite, email provider or deployment.

M1.5 complete: 67 passed, 1 upstream deprecation warning (3.49s). Existing 21 team tests unchanged. Browser saved weekly hours, added/edited an exam and verified resulting free intervals; actual Uvicorn stop/start preserved both edits. Matching reads the same repository; cross-user/private-data and disabled-export gates tested. See calendar-integration-handoff.md.

Remote shuluun advanced to ee42763 during development. The user clarified to finish this new implementation independently and not import or be influenced by the older version. That branch and commit remain untouched. Version storage/push must preserve its history; never force-push or overwrite the older commit.

Next: independent M1.5 commit, then M2 read-only OAuth/provider code and mock tests. Fixed demo identity must never connect a real account. Real writes/deletes remain unavailable. Draft PR and final branch publication follow code/secret/regression review.
