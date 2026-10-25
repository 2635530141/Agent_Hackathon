# Session → Schedule integration

Built from main `e2b1f58`, retaining the existing timetable and Session room lifecycle.

- Creating or joining a room immediately makes its interval a reservation for each current member. Leaving, closing an empty room, and host rescheduling are reflected from the same Session source.
- The visible Schedule page checks its authenticated week endpoint every five seconds, and checks again when returning to the tab. Polling pauses while editing or dragging a time range. Network errors show a retry status rather than clearing the timetable.
- Room blocks show course and venue in the owner's timetable. They are read-only there, with a link to manage the source room. Matching reservations still contain only session ID and UTC start/end.
- Room mutations ignore the room being changed for its own members. Joining/switching only ignores the actor's old room seats that Session itself will remove. Exams, manual busy time, and other members' reservations remain hard conflicts.
- No new Session database, Google connection, calendar writes, invitations, ranking changes, or place-search changes were introduced.

## Verification (2026-10-24)

From `team-integration/calendar-team-start`, using Python 3.13.5 in the existing environment:

```powershell
& ..\Agent_Hackathon\.venv\Scripts\python.exe -m pytest -q tests/test_session_schedule_sync.py tests/test_calendar_room_guards.py --basetemp=.tmp/session-sync-targeted --tb=short
# 23 passed
node --check app/static/calendar.js
& ..\Agent_Hackathon\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/session-sync-final --junitxml=.tmp/session-sync-results-final.xml --tb=short
# 383 passed, 2 dependency deprecation warnings
```

Browser verification used local synthetic Maya data on port 8020: create a Math 54 room, observe the Schedule block, open its read-only source link, leave the room, and observe automatic removal without reloading Schedule. The obsolete test expectation for a completed Session conversation paragraph was updated to the current room view; its exam-conflict and retained-input assertions remain.

Local preview startup, preserving the existing Calendar SQLite data:

```powershell
$env:PYTHONUTF8='1'
$env:STUDYMATE_CALENDAR_MODE='synthetic'
$env:STUDYMATE_CALENDAR_DB='sqlite+pysqlite:///C:/Users/shulu/Desktop/StudyMate_Calendar_Standalone_v2/team-integration/calendar-team-start/data/calendar-main-demo.db'
& ..\Agent_Hackathon\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8020 --no-access-log
```

## Remaining integration dependency

Follow-up account fix: normal signed-in accounts now bind Calendar to their own ID in request-local context. They start with their own empty local schedule, not Maya's fixture records. Existing owner-target rejection, CSRF, and cross-account record protection remain in place. The retired private Google host still uses its separate authenticated context.

Session currently stores rooms in process memory (`store._rooms`). Restarting the server resets that source, although Calendar's SQLite edits persist. Production restart durability and multi-worker consistency require the team's persistent Session repository and authenticated membership adapter. This change does not claim those dependencies are delivered, or any real Google integration.
