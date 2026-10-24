from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from schedule_service.api import create_app
from schedule_service.domain import BusyBlock, Interval
from schedule_service.google_calendar import CalendarError, CalendarReceipt, CalendarWriteUncertain, calendar_event_id

NOW = datetime.fromisoformat("2026-10-24T20:00:00+00:00")
A = {"Authorization": "Bearer alice-test-token"}
B = {"Authorization": "Bearer bob-test-token"}
INTERNAL = {"X-Schedule-Service-Token": "matching-test-secret"}
SLOT = {"start": "2026-10-26T14:00:00-07:00", "end": "2026-10-26T15:00:00-07:00"}
WINDOW = {"start": "2026-10-26T14:00:00-07:00", "end": "2026-10-26T18:00:00-07:00"}


class FakeCalendar:
    def __init__(self):
        self.blocks = {}
        self.fetch_failure = False
        self.write_unknown = False
        self.created = {}
        self.write_calls = []

    def fetch_busy(self, user_id, window):
        if self.fetch_failure:
            raise CalendarError("Test failure", code="provider_error")
        return self.blocks.get(user_id, [])

    def create_event(self, user_id, session_id, subject, interval, place, payload_version=1):
        self.write_calls.append((user_id, session_id, subject, place, payload_version))
        event_id = calendar_event_id(user_id, session_id)
        self.created[event_id] = (subject, interval, place)
        self.blocks[user_id] = [BusyBlock(event_id, interval, "google_calendar", subject)]
        if self.write_unknown:
            raise CalendarWriteUncertain()
        return CalendarReceipt(event_id, "confirmed", "https://calendar.google.com/calendar/event?demo")


@pytest.fixture
def setup():
    calendar = FakeCalendar()
    app = create_app("sqlite://", auth_tokens={"alice-test-token": "alice", "bob-test-token": "bob"},
                     service_token="matching-test-secret", calendar_connector=calendar,
                     clock=lambda: NOW, signing_key="test-signing-key")
    client = TestClient(app)
    base = {"expected_version": 0, "timezone": "America/Los_Angeles",
            "weekly": [{"weekday": 0, "start": "14:00:00", "end": "18:00:00"}]}
    for auth in (A, B):
        assert client.put("/schedule/me", headers=auth, json=base).status_code == 200
    return client, app, calendar


def private_block(app, user_id, title="Private health appointment", connected=False):
    data, version = app.state.store.schedule(user_id)
    data.update(calendar_connected=connected, calendar_busy=[{"id": "private-event", "title": title, **SLOT}])
    app.state.store.save_schedule(user_id, data, version)


def session(client, **changes):
    data = dict(id="s1", organizer_id="alice", participant_ids=["alice", "bob"],
                accepted_ids=["alice", "bob"], subject="STAT153 revision", place="Demo Library A",
                version=1, request_id="request-1", **SLOT)
    data.update(changes)
    return client.put("/internal/confirmed-sessions/s1", headers=INTERNAL, json=data)


def test_authentication_and_no_cross_user_addressing(setup):
    client, _, _ = setup
    assert client.get("/schedule/me").status_code == 401
    assert client.get("/schedule/bob", headers=A).status_code == 404
    response = client.put("/schedule/me", headers=A, json={"expected_version": 1, "owner_id": "bob"})
    assert response.status_code == 422
    assert client.get("/schedule/me", headers=B).json()["owner_id"] == "bob"


def test_weekly_and_one_off_updates_are_versioned(setup):
    client, _, _ = setup
    response = client.put("/schedule/me", headers=A, json={"expected_version": 1,
        "weekly": [{"weekday": 0, "start": "14:00", "end": "18:00"}],
        "removals": [SLOT], "additions": [{"start": "2026-10-27T10:00:00-07:00", "end": "2026-10-27T11:00:00-07:00"}]})
    assert response.status_code == 200
    stale = client.put("/schedule/me", headers=A, json={"expected_version": 1})
    assert stale.status_code == 409
    slots = client.post("/schedule/me/availability", headers=A, json=WINDOW).json()["slots"]
    assert slots == [{"start": "2026-10-26T22:00:00+00:00", "end": "2026-10-27T01:00:00+00:00"}]


def test_matching_only_returns_free_projection(setup):
    client, app, _ = setup
    private_block(app, "bob")
    assert "Private health appointment" in client.get("/schedule/me", headers=B).text
    result = client.post("/internal/availability", headers=INTERNAL,
                         json={**WINDOW, "participant_ids": ["alice", "bob"]})
    assert result.status_code == 200
    assert "Private" not in result.text and "title" not in result.text and "private-event" not in result.text
    assert result.json()["slots"][0]["start"] == "2026-10-26T22:00:00+00:00"


def test_internal_tool_requires_separate_service_credentials(setup):
    client, _, _ = setup
    for headers in ({}, A):
        assert client.post("/internal/availability", headers=headers,
                           json={**WINDOW, "participant_ids": ["alice", "bob"]}).status_code == 403


def test_owner_calendar_override_does_not_change_schedule_or_peer(setup):
    client, app, _ = setup
    private_block(app, "alice")
    body = {**SLOT, "request_id": "request-1"}
    result = client.post("/schedule/me/check", headers=A, json=body).json()
    assert not result["allowed"] and result["can_override_own_calendar"]
    result = client.post("/schedule/me/check", headers=A, json={**body, "study_anyway": True}).json()
    assert result["allowed"] and result["override_token"]
    assert "Private health appointment" in result["conflicts"][0]["title"]
    public = client.post("/internal/availability", headers=INTERNAL,
                         json={**SLOT, "participant_ids": ["alice", "bob"]}).json()
    assert public["slots"] == []
    assert client.get("/schedule/me", headers=A).json()["calendar_busy"]
    approved = {**SLOT, "participant_ids": ["alice", "bob"], "organizer_id": "alice",
                "request_id": "request-1", "owner_override_token": result["override_token"]}
    result_matching = client.post("/internal/availability", headers=INTERNAL, json=approved)
    assert result_matching.status_code == 200 and result_matching.json()["slots"]
    private_block(app, "bob", "Peer secret must remain a hard stop")
    peer_busy = client.post("/internal/availability", headers=INTERNAL, json=approved)
    assert peer_busy.status_code == 200 and peer_busy.json()["slots"] == []
    assert "secret" not in peer_busy.text


def test_new_own_calendar_conflict_invalidates_the_override(setup):
    client, app, _ = setup
    private_block(app, "alice")
    token = client.post("/schedule/me/check", headers=A,
                       json={**SLOT, "request_id": "r", "study_anyway": True}).json()["override_token"]
    payload, revision = app.state.store.schedule("alice")
    payload["calendar_busy"][0]["id"] = "new-conflicting-event"
    app.state.store.save_schedule("alice", payload, revision)
    assert client.post("/internal/availability", headers=INTERNAL, json={**SLOT,
        "participant_ids": ["alice", "bob"], "organizer_id": "alice", "request_id": "r",
        "owner_override_token": token}).status_code == 409


def test_exam_cannot_be_overridden(setup):
    client, _, _ = setup
    body = {"expected_version": 1, "weekly": [{"weekday": 0, "start": "14:00", "end": "18:00"}],
            "exams": [{**SLOT, "id": "exam1", "course_id": "STAT153", "title": "Midterm"}]}
    assert client.put("/schedule/me", headers=A, json=body).status_code == 200
    result = client.post("/schedule/me/check", headers=A,
                         json={**SLOT, "request_id": "r1", "study_anyway": True}).json()
    assert result["allowed"] is False and result["can_override_own_calendar"] is False
    assert result["override_token"] is None


@pytest.mark.parametrize("patch", [{"start": "2026-10-26T14:00:00"},
                                  {"end": "2026-10-26T13:00:00-07:00"},
                                  {"end": "2026-11-20T18:00:00-08:00"}])
def test_invalid_or_outside_horizon_intervals(setup, patch):
    client, _, _ = setup
    assert client.post("/schedule/me/availability", headers=A, json={**WINDOW, **patch}).status_code == 422


def test_bad_timezone_returns_validation_error_without_persisting(setup):
    client, _, _ = setup
    assert client.put("/schedule/me", headers=A, json={"expected_version": 1, "timezone": "Not/AZone"}).status_code == 422
    assert client.get("/schedule/me", headers=A).json()["version"] == 1


def test_calendar_busy_cannot_be_spoofed_through_public_update(setup):
    client, _, _ = setup
    assert client.put("/schedule/me", headers=A,
                      json={"expected_version": 1, "calendar_busy": [], "calendar_connected": False}).status_code == 422


def test_failed_calendar_refresh_retains_existing_blocks(setup):
    client, app, calendar = setup
    private_block(app, "alice", connected=True)
    calendar.fetch_failure = True
    response = client.post("/schedule/me/availability", headers=A, json=WINDOW)
    assert response.status_code == 503
    assert "Private health appointment" in client.get("/schedule/me", headers=A).text


def test_cache_does_not_claim_coverage_outside_imported_window(setup):
    client, app, calendar = setup
    private_block(app, "alice", connected=True)
    data, version = app.state.store.schedule("alice")
    data.update(calendar_refreshed_at=NOW.isoformat(), calendar_window={
        "start": NOW.isoformat(), "end": (NOW + timedelta(hours=1)).isoformat()})
    app.state.store.save_schedule("alice", data, version)
    calendar.fetch_failure = True
    assert client.post("/schedule/me/availability", headers=A, json=WINDOW).status_code == 503


def test_requires_all_acceptances_from_trusted_session_service(setup):
    client, _, _ = setup
    assert session(client, accepted_ids=["alice"]).status_code == 422
    assert client.put("/internal/confirmed-sessions/s1", headers=A, json={}).status_code == 403
    assert client.get("/sessions/s1/calendar-preview", headers=A).status_code == 404


def test_calendar_export_exact_confirmation_and_organizer_only(setup):
    client, _, calendar = setup
    assert session(client).status_code == 200
    assert client.get("/sessions/s1/calendar-preview", headers=B).status_code == 404
    assert client.post("/sessions/s1/calendar", headers=A, json={"confirmed": True,
                       "confirmation_token": "forged"}).status_code == 409
    preview = client.get("/sessions/s1/calendar-preview", headers=A)
    assert preview.status_code == 200
    assert preview.json()["attendees"] == []
    sent = client.post("/sessions/s1/calendar", headers=A, json={"confirmed": True,
                       "confirmation_token": preview.json()["confirmation_token"]})
    assert sent.status_code == 200
    assert sent.json()["calendar_owner_id"] == "alice" and sent.json()["guest_invites_sent"] is False
    assert calendar.write_calls[0][0] == "alice"


def test_content_change_invalidates_calendar_confirmation(setup):
    client, _, calendar = setup
    session(client)
    token = client.get("/sessions/s1/calendar-preview", headers=A).json()["confirmation_token"]
    assert session(client, place="Other place", version=2).status_code == 200
    assert client.post("/sessions/s1/calendar", headers=A,
                       json={"confirmed": True, "confirmation_token": token}).status_code == 409
    assert not calendar.write_calls


def test_new_peer_busy_block_stops_export_without_leaking_title(setup):
    client, app, calendar = setup
    session(client)
    token = client.get("/sessions/s1/calendar-preview", headers=A).json()["confirmation_token"]
    private_block(app, "bob", "Peer private diagnosis")
    result = client.post("/sessions/s1/calendar", headers=A,
                         json={"confirmed": True, "confirmation_token": token})
    assert result.status_code == 409 and "diagnosis" not in result.text
    assert not calendar.write_calls


def test_repeat_export_ignores_only_its_own_created_event(setup):
    client, app, calendar = setup
    # Both calendars connected and refreshed, with no events before the first write.
    for uid in ("alice", "bob"):
        data, version = app.state.store.schedule(uid)
        data["calendar_connected"] = True
        app.state.store.save_schedule(uid, data, version)
    session(client)
    token = client.get("/sessions/s1/calendar-preview", headers=A).json()["confirmation_token"]
    for _ in range(2):
        response = client.post("/sessions/s1/calendar", headers=A,
                               json={"confirmed": True, "confirmation_token": token})
        assert response.status_code == 200, response.text
    assert len(calendar.created) == 1


def test_unknown_export_is_observation_not_false_success(setup):
    client, _, calendar = setup
    session(client)
    token = client.get("/sessions/s1/calendar-preview", headers=A).json()["confirmation_token"]
    calendar.write_unknown = True
    result = client.post("/sessions/s1/calendar", headers=A,
                         json={"confirmed": True, "confirmation_token": token})
    assert result.status_code == 202 and result.json()["status"] == "UNKNOWN"


def test_disconnect_removes_only_owner_imported_data(setup):
    client, app, _ = setup
    private_block(app, "alice")
    private_block(app, "bob", "Bob secret")
    assert client.delete("/calendar/google/connection", headers=A).status_code == 200
    assert client.get("/schedule/me", headers=A).json()["calendar_busy"] == []
    assert "Bob secret" in client.get("/schedule/me", headers=B).text


def test_kill_switch_blocks_executor_and_schedule_edits(setup):
    client, app, calendar = setup
    session(client)
    token = client.get("/sessions/s1/calendar-preview", headers=A).json()["confirmation_token"]
    app.state.writes_enabled = False
    assert client.put("/schedule/me", headers=A, json={"expected_version": 1}).status_code == 503
    assert client.post("/sessions/s1/calendar", headers=A,
                       json={"confirmed": True, "confirmation_token": token}).status_code == 503
    assert not calendar.write_calls


def test_requests_have_safe_trace_identifiers(setup, caplog):
    import logging
    client, _, _ = setup
    with caplog.at_level(logging.INFO, logger="studymate.schedule"):
        response = client.get("/schedule/me?secret=not-to-log", headers=A)
    assert response.headers["X-Request-ID"]
    assert "not-to-log" not in caplog.text and "alice-test-token" not in caplog.text


def test_postgres_schema_compiles_without_claiming_database_execution(setup):
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    from schedule_service.storage import Base
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE" in ddl


def test_oauth_callback_is_bound_to_originating_browser():
    from cryptography.fernet import Fernet
    from test_oauth import FakeOAuth
    oauth = FakeOAuth()
    app = create_app("sqlite://", auth_tokens={"alice-test-token": "alice"}, oauth_client=oauth,
                     encryption_key=Fernet.generate_key().decode(), calendar_connector=FakeCalendar(), clock=lambda: NOW)
    client, outsider = TestClient(app), TestClient(app)
    client.put("/schedule/me", headers=A, json={"expected_version": 0})
    begin = client.post("/calendar/google/connect", headers=A, json={})
    assert begin.status_code == 200 and "HttpOnly" in begin.headers["set-cookie"]
    params = {"state": oauth.state, "code": "code"}
    assert outsider.get("/calendar/google/callback", params=params).status_code == 400
    assert oauth.exchanges == 0
    result = client.get("/calendar/google/callback", params=params)
    assert result.status_code == 200 and result.json()["connected"]
    assert "access-secret" not in result.text
    assert client.get("/calendar/google/callback", params=params).status_code == 400
