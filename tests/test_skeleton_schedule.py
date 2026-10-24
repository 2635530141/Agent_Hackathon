import importlib
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import schedule_core, store
from app.main import app


@pytest.fixture(autouse=True)
def reset_seed():
    importlib.reload(store)
    yield
    importlib.reload(store)


def test_skeleton_weekly_hours_are_free_and_calendar_is_subtracted():
    slots = schedule_core.availability("2026-10-29T16:00", "2026-10-29T21:00")
    assert slots == [
        {"starts_at": "2026-10-29T16:00:00-07:00", "ends_at": "2026-10-29T18:00:00-07:00"},
        {"starts_at": "2026-10-29T19:00:00-07:00", "ends_at": "2026-10-29T21:00:00-07:00"},
    ]
    assert schedule_core.own_conflict("2026-10-29T19:00", "2026-10-29T21:00", False) is None


def test_extra_free_time_never_overrides_hard_busy_and_removing_slot_recomputes():
    schedule_core.create_exception("2026-10-29T17:00", "2026-10-29T20:00", "free")
    schedule_core.create_exception("2026-10-29T19:30", "2026-10-29T20:30", "busy")
    slots = schedule_core.availability("2026-10-29T16:00", "2026-10-29T21:00")
    assert [slot["starts_at"][11:16] for slot in slots] == ["16:00", "19:00", "20:30"]
    assert [slot["ends_at"][11:16] for slot in slots] == ["18:00", "19:30", "21:00"]
    schedule_core.remove_exception(store.exceptions()[-1]["id"])
    assert len(schedule_core.availability("2026-10-29T16:00", "2026-10-29T21:00")) == 2


def test_matching_rows_hide_titles_and_preserve_free_exceptions():
    schedule_core.create_exception("2026-10-29T12:00", "2026-10-29T13:00", "free")
    rows = [row.model_dump() for row in schedule_core.list_busy_for_match()]
    assert all("title" not in row and row["source"] != "weekly" for row in rows)
    assert any(row["kind"] == "free" for row in rows)
    assert "CS 61A lecture" in str(schedule_core.list_busy())
    assert "CS 61A lecture" not in str(rows)


def test_alternative_keeps_requested_duration_and_is_rechecked():
    conflict = schedule_core.own_conflict("2026-10-29T18:00", "2026-10-29T21:00", False)
    assert conflict["title"] == "CS 61A lecture"
    start, end = (datetime.fromisoformat(conflict[key]) for key in ("alternative_start", "alternative_end"))
    assert (end-start).total_seconds() == 3*3600
    assert schedule_core.own_conflict(conflict["alternative_start"], conflict["alternative_end"], False) is None


def test_owner_override_only_skips_own_calendar_for_this_call():
    assert schedule_core.own_conflict("2026-10-29T18:00", "2026-10-29T21:00", True) is None
    assert schedule_core.own_conflict("2026-10-29T18:00", "2026-10-29T21:00", False)
    schedule_core.create_exception("2026-10-29T19:00", "2026-10-29T20:00", "busy")
    assert not schedule_core.own_conflict("2026-10-29T18:00", "2026-10-29T21:00", True)["can_study_anyway"]
    assert not schedule_core.own_conflict("2026-10-29T12:00", "2026-10-29T13:00", True)["can_study_anyway"]
    assert not schedule_core.own_conflict("2026-11-06T09:00", "2026-11-06T10:00", True)["can_study_anyway"]


def test_saved_hours_replace_old_seed_and_empty_hours_have_no_fake_alternative():
    schedule_core.save_weekly({"thu": ("19:00", "20:00")})
    assert len(schedule_core.availability("2026-10-29T16:00", "2026-10-29T21:00")) == 1
    assert all(row.source != "weekly" for row in schedule_core.list_busy())
    schedule_core.save_weekly({})
    conflict = schedule_core.own_conflict("2026-10-29T19:00", "2026-10-29T21:00", False)
    assert not conflict["has_alternative"] and conflict["alternative_start"] is None
    with TestClient(app) as client:
        response = client.get("/session?starts_at=2026-10-29T19:00&ends_at=2026-10-29T21:00")
    assert response.status_code == 200
    assert "No complete alternative" in response.text


@pytest.mark.parametrize("start,end", [
    ("bad", "2026-10-29T19:00"), ("2026-10-29T19:00", "2026-10-29T18:00"),
    ("2026-03-08T02:30", "2026-03-08T04:00"), ("2026-11-01T01:30", "2026-11-01T03:00"),
    ("2026-10-01T16:00", "2026-11-01T16:00"),
])
def test_bad_times_are_rejected_even_when_override_requested(start, end):
    with pytest.raises(ValueError):
        schedule_core.own_conflict(start, end, True)


def test_exam_priority_is_three_local_dates_and_deleting_exam_removes_block():
    exam = schedule_core.create_exam("cs61a", "2026-11-02T09:00", "2026-11-02T12:00", True)
    assert exam.priority_starts_at == "2026-10-30T00:00:00-07:00"
    assert exam.starts_at.endswith("-08:00")
    schedule_core.remove_exam(exam.id)
    assert not any(row.id == exam.id for row in schedule_core.list_busy_for_match())


def test_schedule_page_form_api_and_google_status():
    with TestClient(app) as client:
        page = client.get("/schedule")
        assert page.status_code == 200 and "Your available time" in page.text
        result = client.get("/api/schedule/availability", params={"starts_at": "2026-10-29T16:00", "ends_at": "2026-10-29T21:00"})
        assert len(result.json()["available"]) == 2 and "lecture" not in result.text
        assert client.get("/api/schedule/availability", params={"starts_at": "bad", "ends_at": "bad"}).status_code == 422
        saved = client.post("/schedule/hours", data={"thu_start": "19:00", "thu_end": "20:00"})
        assert saved.status_code == 200 and "Saved." in saved.text
        invalid = client.post("/schedule/hours", data={"thu_start": "19:00"})
        assert "needs both" in invalid.text
        calendar = client.post("/schedule/calendar")
        assert "Google Calendar is not connected" in calendar.text
        assert not schedule_core.calendar_status()["connected"]


def test_existing_skeleton_pages_render_with_shared_contracts():
    with TestClient(app) as client:
        for path in ("/", "/profile", "/matching", "/session", "/invite/demo", "/style"):
            assert client.get(path).status_code == 200, path
