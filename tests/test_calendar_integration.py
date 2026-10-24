from datetime import timedelta
from contextlib import contextmanager
import json
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from app import accounts, matching_core, schedule_core, store
from app.main import app
from app.scheduling.core.schemas import Actor, ExamInput, ExceptionInput, Horizon, Interval, PrivateEvent
from app.scheduling.core.service import AuthorizationError
from app.scheduling.demo import FIXTURE_HORIZON
from app.scheduling.runtime import build_runtime, get_runtime
from app.scheduling.time import local_input, parse_instant


@contextmanager
def demo_client():
    with TestClient(app) as client:
        response = client.post("/login", data={"email":accounts.TEST_EMAIL,
            "password":accounts.TEST_PASSWORD}, follow_redirects=False)
        assert response.status_code == 303
        yield client


def prepare():
    runtime = get_runtime()
    for kind in ("exams", "exceptions"):
        for item in runtime.repository.list_records(store.OWNER_ID, kind):
            runtime.repository.delete_record(store.OWNER_ID, kind, item.id)
    schedule_core.save_weekly({"mon":("13:00","18:00")})
    runtime.service.provider.events[store.OWNER_ID] = [PrivateEvent(id="synthetic-block",
        start=parse_instant("2026-10-26T14:00"), end=parse_instant("2026-10-26T15:00"),
        private_title="SYNTHETIC_OWNER_ONLY")]
    runtime.service.refresh_calendar(Actor(subject_id=store.OWNER_ID), FIXTURE_HORIZON)
    exam = schedule_core.create_exam("math54", "2026-10-26T16:00", "2026-10-26T17:00", False)
    return runtime, exam


def local_pairs(snapshot):
    return [(local_input(i.start)[11:],local_input(i.end)[11:]) for i in snapshot.available]


def snapshot():
    return schedule_core.effective_availability(store.OWNER_ID,"2026-10-26T00:00","2026-10-27T00:00")


def test_core_acceptance_and_matching_share_repository():
    runtime, _ = prepare()
    # No study hours: the whole day is free except the busy blocks (14:00 and 16:00).
    assert local_pairs(snapshot()) == [("00:00","14:00"),("15:00","16:00"),("17:00","00:00")]
    me = matching_core.load_inputs().me
    available = matching_core.free_time(me, parse_instant("2026-10-26T00:00"),parse_instant("2026-10-27T00:00"))
    assert available == [(i.start,i.end) for i in snapshot().available]


def test_exam_form_edit_updates_page_and_matching():
    runtime, exam = prepare()
    with demo_client() as client:
        response = client.post(f"/schedule/exams/{exam.id}/edit", data={"course_id":"math54",
            "starts_at":"2026-10-26T15:00","ends_at":"2026-10-26T17:30","priority_starts_at":"2026-10-24T00:00"})
        assert response.status_code == 200 and "Saved" in response.text
        body = client.get("/api/schedule/availability",params={"starts_at":"2026-10-26T00:00","ends_at":"2026-10-27T00:00"}).json()
        assert len(body["available"]) == 2 and len(body["priority_windows"]) == 1
        me = matching_core.load_inputs().me
        assert matching_core.free_time(me,parse_instant("2026-10-26T15:00"),parse_instant("2026-10-26T17:30")) == []
        assert "2026-10-26T17:30" in client.get("/schedule?day=2026-10-26").text


def test_restart_retains_edits_and_utc_instants():
    runtime, exam = prepare()
    url = str(runtime.repository.engine.url)
    before = snapshot().available
    runtime.repository.close()
    restarted = build_runtime(url)
    try:
        result = restarted.service.effective_availability(Actor(subject_id=store.OWNER_ID),store.OWNER_ID,
            Horizon(start=parse_instant("2026-10-26T00:00"),end=parse_instant("2026-10-27T00:00")))
        assert result.available == before
        saved = restarted.repository.list_records(store.OWNER_ID,"exams")[0]
        assert saved.start.utcoffset() == timedelta(0)
    finally:
        restarted.repository.close()


@pytest.mark.parametrize("anyway", [False,True])
def test_exam_and_invalid_time_cannot_be_overridden(anyway):
    prepare()
    assert "EXAM_BUSY" in schedule_core.own_conflict("2026-10-26T16:00","2026-10-26T17:00",anyway)["codes"]
    with pytest.raises(ValueError):
        schedule_core.own_conflict("2026-10-26T18:00","2026-10-26T17:00",anyway)
    with pytest.raises(ValueError):
        schedule_core.own_conflict("not-a-time","2026-10-26T17:00",anyway)


def test_half_open_adjacent_and_full_duration_alternative_checks_all_blocks():
    runtime, _ = prepare()
    assert schedule_core.own_conflict("2026-10-26T15:00","2026-10-26T16:00",False) is None
    schedule_core.save_weekly({"mon":("13:00","20:00")})
    schedule_core.create_exception("2026-10-26T17:00","2026-10-26T17:30","busy")
    # The schedule page's check tool still offers the next full-length free slot.
    conflict = schedule_core.own_conflict("2026-10-26T14:00","2026-10-26T15:30",False,within_window=False)
    assert (conflict["alternative_start"],conflict["alternative_end"]) == ("2026-10-26T17:30","2026-10-26T19:00")
    assert parse_instant(conflict["alternative_end"])-parse_instant(conflict["alternative_start"]) == timedelta(minutes=90)
    # A study session never moves outside the requested window; nothing in it is free for an hour.
    session = schedule_core.own_conflict("2026-10-26T14:00","2026-10-26T15:30",False)
    assert session and (session["alternative_start"], session["alternative_end"]) == (None, None)


def test_no_alternative_renders_without_crashing():
    prepare()
    schedule_core.save_weekly({})
    with demo_client() as client:
        response = client.get("/session",params={"ready":"1","starts_at":"2026-10-26T16:00","ends_at":"2026-10-26T17:00","study_anyway":"1"})
        assert response.status_code == 200
        assert "No feasible alternative" in response.text
        assert "Try " not in response.text.split("overlaps this time.")[1].split("</p>")[0]


def test_study_hours_no_longer_limit_free_time():
    prepare()
    schedule_core.save_weekly({})
    assert local_pairs(snapshot()) == [("00:00","14:00"),("15:00","16:00"),("17:00","00:00")]
    schedule_core.create_exception("2026-10-26T13:00","2026-10-26T18:00","free")
    assert local_pairs(snapshot()) == [("00:00","14:00"),("15:00","16:00"),("17:00","00:00")]
    assert all(b.source != "weekly" for b in schedule_core.list_busy_for_match())


def test_matching_privacy_and_no_provider_call(monkeypatch):
    runtime, _ = prepare()
    def fail(*args):
        raise AssertionError("Matching must not read private titles or contact provider")
    monkeypatch.setattr(runtime.repository,"private_events",fail)
    monkeypatch.setattr(runtime.service.provider,"read_snapshot",fail)
    result = matching_core.load_inputs()
    matching_core.free_time(result.me,parse_instant("2026-10-26T00:00"),parse_instant("2026-10-27T00:00"))
    safe = json.dumps([b.model_dump() for b in schedule_core.list_busy_for_match()])
    assert all(k not in safe for k in ("title","SYNTHETIC_OWNER_ONLY","@","token"))


def test_owner_endpoint_denies_target_user_and_core_owner_context():
    runtime, _ = prepare()
    with demo_client() as client:
        assert client.get("/api/schedule/busy?user_id=u_jordan").status_code == 403
        assert client.get("/schedule?owner_id=u_jordan").status_code == 403
        assert "SYNTHETIC_OWNER_ONLY" in client.get("/api/schedule/busy").text
        assert "SYNTHETIC_OWNER_ONLY" not in client.get("/api/schedule/busy/match").text
    with pytest.raises(AuthorizationError):
        runtime.service.effective_availability(Actor(subject_id="u_jordan"),store.OWNER_ID,FIXTURE_HORIZON)


def test_sync_failure_preserves_snapshot_and_is_unknown():
    runtime, _ = prepare()
    old = runtime.repository.read_bundle(store.OWNER_ID)["busy"]
    runtime.service.provider.failure_mode = "timeout"
    runtime.service.refresh_calendar(Actor(subject_id=store.OWNER_ID),FIXTURE_HORIZON)
    assert runtime.repository.read_bundle(store.OWNER_ID)["busy"] == old
    assert snapshot().calendar.verification_level == "unknown" and snapshot().available == []


def test_session_gateway_reads_team_confirmed_reservations():
    runtime, _ = prepare()
    invitations = store.create_invitations([{"person_id":"u_jordan","first_name":"Jordan","starts_at":"2026-10-26T15:00","ends_at":"2026-10-26T16:00"}])
    assert len(snapshot().available) == 3
    store.set_person_status(invitations[0]["id"],"accepted")
    assert len(snapshot().available) == 2
    assert "CONFIRMED_SESSION_BUSY" in schedule_core.own_conflict("2026-10-26T15:00","2026-10-26T16:00",True)["codes"]


@pytest.mark.parametrize("path", ["/schedule","/session","/profile"])
def test_team_pages_still_render(path):
    with demo_client() as client:
        assert client.get(path).status_code == 200


def test_form_timezone_preserves_instant_and_rejects_dst_ambiguity():
    assert parse_instant("2026-10-26T13:00").isoformat() == "2026-10-26T20:00:00+00:00"
    assert parse_instant("2026-11-02T13:00").isoformat() == "2026-11-02T21:00:00+00:00"
    for value in ("2026-11-01T01:30","2026-03-08T02:30"):
        with pytest.raises(ValueError):
            parse_instant(value)
    assert parse_instant("2026-11-01T01:30-07:00") != parse_instant("2026-11-01T01:30-08:00")


def test_demo_google_connection_never_toggles_live():
    with demo_client() as client:
        response = client.post("/schedule/calendar")
        assert response.status_code == 404
    assert schedule_core.calendar_status()["connected"] is False


def test_export_disabled_even_after_demo_acceptance():
    invitations = store.create_invitations([{"person_id":"u_jordan","first_name":"Jordan","starts_at":"2026-10-29T19:00","ends_at":"2026-10-29T20:00"}])
    store.set_person_status(invitations[0]["id"],"accepted")
    with demo_client() as client:
        response = client.post("/session/calendar",data={"accepted":"true","approved":"true"})
        assert response.status_code == 200 and "Calendar export is disabled" in response.text
        assert not store.calendar_event_written()


def test_invalid_matching_time_and_dst_note_return_safe_message():
    with demo_client() as client:
        for value in ("invalid","2026-11-01T01:30"):
            response = client.get("/api/match",params={"starts_at":value,"ends_at":"2026-11-01T03:00","study_anyway":"1"})
            assert response.status_code == 200 and response.json()["proposal"] is None


def test_team_login_gate_and_other_demo_account_cannot_read_owner_private_view(monkeypatch):
    from copy import deepcopy
    from secrets import token_hex
    monkeypatch.setattr(accounts,"_accounts",deepcopy(accounts._accounts))
    email = token_hex(8)+"@example.invalid"
    password = token_hex(16)
    accounts.register("Synthetic Tester",email,password)
    with TestClient(app) as client:
        denied = client.get("/schedule",follow_redirects=False)
        assert denied.status_code == 303 and denied.headers["location"] == "/login"
        client.post("/login",data={"email":email,"password":password})
        assert client.get("/schedule").status_code == 403
        assert client.get("/api/schedule/busy").status_code == 403
        # A demo login does not establish a trusted Google actor.
        assert client.get("/calendar/google/connect").status_code == 404


def test_synthetic_refresh_updates_owner_and_candidate_pool():
    runtime = get_runtime()
    runtime.repository.begin_refresh("u_jordan")
    with demo_client() as client:
        assert client.post("/schedule/recalculate").status_code == 200
    assert runtime.repository.read_bundle("u_jordan")["state"]["connection_status"] == "synced"


def test_latest_session_interpretation_hands_time_to_calendar_with_mock_only(monkeypatch):
    from app.routers import session
    prepare()
    monkeypatch.setattr(session,"interpret_request",lambda sentence,courses:{
        "course_id":"math54","starts_at":"2026-10-26T16:00",
        "ends_at":"2026-10-26T17:00","place_type":"coffee"})
    with demo_client() as client:
        response = client.post("/session/interpret",data={"sentence":"Synthetic request"})
        assert response.status_code == 200
        assert "Exam overlaps this time." in response.text
        assert "EXAM_BUSY" in schedule_core.own_conflict("2026-10-26T16:00","2026-10-26T17:00",True)["codes"]
        assert 'name="sentence" value="Synthetic request"' in response.text  # New room forms retain the conversation.
        assert "<p>Synthetic request</p>" in response.text
