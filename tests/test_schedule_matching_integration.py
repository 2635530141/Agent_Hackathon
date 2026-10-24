"""Exercise the current team's matching against real schedule edits, not fixtures alone."""
import importlib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import matching_core, schedule_core, store
from app.contracts import StudyRequest
from app.main import app


@pytest.fixture(autouse=True)
def reset_seed():
    importlib.reload(store)
    yield
    importlib.reload(store)


def request(**changes):
    return StudyRequest(**{"course_id": "cs61a", "starts_at": "2026-10-27T19:00",
                           "ends_at": "2026-10-27T21:00", "place_type": "library", **changes})


def test_student_id_interfaces_read_each_students_schedule_without_owner_fallback():
    assert schedule_core.weekly_hours("u_jordan")["tue"] == ("18:00", "22:00")
    assert schedule_core.weekly_hours()["tue"] == ("18:00", "21:00")
    jordan = schedule_core.list_busy_for_match("u_jordan")
    assert [row.id for row in jordan] == ["cal_j1"]
    assert "Lab section" not in str(jordan) and "CS 61A lecture" not in str(jordan)
    assert schedule_core.list_exams("u_sam")[0].id == "exam_sam"
    assert schedule_core.weekly_hours("unknown") == {}
    assert schedule_core.list_busy_for_match("unknown") == []
    assert schedule_core.list_exams("unknown") == []


def test_matching_loads_all_seed_students_and_accepts_offset_or_local_requests():
    inputs = matching_core.load_inputs()
    assert len(inputs.people) >= 4
    local = matching_core.match(request())
    absolute = matching_core.match(request(starts_at="2026-10-28T02:00:00Z", ends_at="2026-10-28T04:00:00Z"))
    assert [row.id for row in local.proposal.people] == [row.id for row in absolute.proposal.people]
    assert local.proposal.starts_at == absolute.proposal.starts_at == "2026-10-27T19:00"


def test_extra_free_slot_outside_weekly_hours_is_used_by_matching_then_removal_takes_effect():
    wednesday = request(starts_at="2026-10-28T19:00", ends_at="2026-10-28T20:00")
    assert matching_core.match(wednesday).later
    schedule_core.create_exception(wednesday.starts_at, wednesday.ends_at, "free")
    added = store.exceptions()[-1]["id"]
    result = matching_core.match(wednesday)
    assert result.proposal and not result.later
    assert result.proposal.starts_at == wednesday.starts_at
    schedule_core.remove_exception(added)
    assert matching_core.match(wednesday).later


def test_owner_override_does_not_bypass_exam_manual_busy_or_weekly_hours_in_matching():
    base = request(study_anyway=True)
    schedule_core.create_exam("cs61a", base.starts_at, base.ends_at, True)
    result = matching_core.match(base)
    assert result.proposal is None or result.proposal.starts_at != base.starts_at
    schedule_core.remove_exam(store.exams()[-1]["id"])
    schedule_core.create_exception(base.starts_at, base.ends_at, "busy")
    result = matching_core.match(base)
    assert result.proposal is None or result.proposal.starts_at != base.starts_at
    schedule_core.remove_exception(store.exceptions()[-1]["id"])
    schedule_core.save_weekly({})
    assert matching_core.match(base).proposal is None


def test_owner_override_skips_only_own_calendar_and_still_honors_peer_calendar():
    # Maya's Thursday 18-19 calendar block can be overridden; Priya's 19-21 cannot.
    result = matching_core.match(request(starts_at="2026-10-29T18:00", ends_at="2026-10-29T21:00", study_anyway=True))
    assert result.proposal
    assert result.proposal.people[0].id == "u_jordan"
    assert result.proposal.starts_at == "2026-10-29T18:00"
    for candidate in result.candidates:
        if candidate.person.profile.id == "u_priya":
            assert all(end <= matching_core._parse("2026-10-29T19:00") for start, end in candidate.free)


def test_new_exam_priority_data_reaches_team_scoring_without_timezone_error():
    result = matching_core.match(request(course_id="math54", starts_at="2026-11-04T18:00", ends_at="2026-11-04T20:00"))
    assert result.proposal and result.proposal.people[0].first_name == "Ana"
    assert "priority window" in result.proposal.reason


@pytest.mark.parametrize("start,end,note", [
    ("2026-11-01T01:30", "2026-11-01T03:00", ""),
    ("2026-03-08T02:30", "2026-03-08T04:00", ""),
    ("bad", "2026-10-27T21:00", ""),
    ("2026-10-01T19:00", "2026-11-01T21:00", ""),
    ("9999-12-30T12:00", "9999-12-30T13:00", ""),
])
def test_matching_api_returns_safe_failure_for_bad_time_or_horizon(start, end, note):
    with TestClient(app) as client:
        response = client.get("/api/match", params={"starts_at": start, "ends_at": end, "note": note})
    assert response.status_code == 200 and response.json()["proposal"] is None
    assert response.json()["message"]


def test_session_alternative_links_keep_valid_times_for_new_matching():
    with TestClient(app) as client:
        conflict = schedule_core.own_conflict("2026-10-29T18:00", "2026-10-29T21:00", False)
        response = client.get("/session", params={"starts_at": conflict["alternative_start"], "ends_at": conflict["alternative_end"]})
    assert response.status_code == 200
    local_start = schedule_core.parse_time(conflict["alternative_start"]).replace(tzinfo=None).isoformat(timespec="minutes")
    assert f'name="starts_at" value="{local_start}"' in response.text


def test_offset_note_resolves_berkeley_day_and_local_place_hours():
    result = matching_core.match(request(starts_at="2026-10-28T02:00Z", ends_at="2026-10-28T04:00Z", note="Thursday 7-9pm instead"))
    assert result.proposal.starts_at == "2026-10-29T19:00"
    assert result.proposal.place.name == "Doe Library"


def test_fold_format_retains_offset_instead_of_ambiguous_local_time():
    value = datetime.fromisoformat("2026-11-01T08:30:00+00:00")
    assert matching_core._iso(value) == "2026-11-01T01:30-07:00"


def test_latest_team_session_waits_for_every_invitee_then_explicit_demo_calendar_confirmation():
    owner = store.owner()
    store.replace_owner({**owner, "group_size": 3})
    fields = {"course_id": "cs61a", "starts_at": "2026-10-29T19:00", "ends_at": "2026-10-29T21:00",
              "place_type": "library", "note": "", "study_anyway": "0", "invite": "Let's study together."}
    with TestClient(app) as client:
        response = client.post("/session/confirm", data=fields)
        assert response.status_code == 200
        invitations = store.latest_invitations()
        assert len(invitations) == 2 and all(row["status"] == "pending" for row in invitations)
        assert all(row["message"] == fields["invite"] for row in invitations)
        client.post("/session/calendar", data=fields)
        assert not store.calendar_event_written()
        client.post(f"/invite/{invitations[0]['id']}/accept")
        client.post("/session/calendar", data=fields)
        assert not store.calendar_event_written()
        client.post(f"/invite/{invitations[1]['id']}/accept")
        assert store.batch_all_accepted() and not store.calendar_event_written()
        confirmed = client.post("/session/calendar", data=fields)
        assert store.calendar_event_written() and "Google write is not connected" in confirmed.text


def test_latest_session_send_rechecks_new_exam_and_declined_invite_blocks_demo_calendar():
    fields = {"course_id": "cs61a", "starts_at": "2026-10-27T19:00", "ends_at": "2026-10-27T21:00",
              "place_type": "library", "study_anyway": "1"}
    exam = schedule_core.create_exam("cs61a", fields["starts_at"], fields["ends_at"], True)
    with TestClient(app) as client:
        assert client.post("/session/confirm", data=fields).status_code == 200
        assert store.latest_invitations() == []
        schedule_core.remove_exam(exam.id)
        client.post("/session/confirm", data=fields)
        invitations = store.latest_invitations()
        assert invitations
        client.post(f"/invite/{invitations[0]['id']}/decline")
        client.post("/session/calendar", data=fields)
        assert not store.calendar_event_written()
