# Calendar timezone and room conflict fixes

Base: `793e49b49e79e7b0530c14cd605ddfa67ff4c9c2` (main).

## Result

- Editing or deleting one timetable weekday only updates that weekday. Other
  rules retain their IDs, IANA timezones, validity dates and UTC occurrences.
  Submitting the detailed weekly form also preserves unchanged rules; new or
  changed hours use the selected display timezone.
- Room creation, joining and host time changes recheck saved availability before
  mutating Session state. Joining uses the stored room interval, and a time change
  checks every current member. Exams, ordinary busy blocks, unavailable hours and
  unknown calendars block the operation. `study_anyway` is not an override.
- Calendar checks use the authenticated actor and server-resolved members. Unknown
  synthetic identities cannot borrow the seeded owner's schedule. Conflict errors
  contain no private calendar title. A future live team host must supply its
  authenticated actor and trusted participant context; the private Google preview
  continues to gate Session routes.
- Existing room resize, host permissions, room switching, idempotent joining and
  leaving are retained. The timetable layout, CSS, JavaScript, Profile and Matching
  algorithms are unchanged. The existing conflict hint now says when no alternative
  is available. Older Calendar tests were aligned with the current Session markup.

## Verification

Executed from `team-integration/calendar-guard-fix` using the existing sibling venv:

```powershell
$env:PYTHONUTF8='1'
..\Agent_Hackathon\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/calendar-fix-verified -o faulthandler_timeout=45
```

Result: **216 passed, 2 dependency deprecation warnings, 21.02 seconds**. Includes
21 new parametrized checks for timezone persistence/rebuilds, exam and other busy
blocks, failed refresh, stored room time, all-member time changes, adjacent slots,
valid room workflows, permissions and unknown identity rejection. The core
13–18 / busy 14–15 / exam 16–17 case remains covered and passes.

Browser check at a dedicated synthetic localhost host (`127.0.0.1:8020`): saved
Monday 13–18 study hours, added a 16–17 exam through the existing timetable dialog,
verified the week display, and clicked Open a room for the exam interval. The
backend rejected the action and the current Session page showed the conflict and
a 17–18 alternative.

All tests use isolated temporary databases and fake Google HTTP. No live Google
reads/writes, credentials, original interactive databases or production migrations
are part of this change. Existing team-auth/Session integration dependencies and
the separately gated Google writeback remain outside these fixes.
