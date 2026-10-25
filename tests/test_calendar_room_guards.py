"""Room mutations use real scheduling logic with synthetic identities/providers."""
from copy import deepcopy
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import accounts, schedule_core, store
from app.main import app
from app.scheduling.core.schemas import Actor, ExamInput, ExceptionInput, PrivateEvent
from app.scheduling.demo import FIXTURE_HORIZON
from app.scheduling.runtime import get_runtime
from app.scheduling.time import parse_instant


DAY = "2026-10-26"
OWNER, GUEST = store.OWNER_ID, "u_jordan"


def instant(hour):
    return f"{DAY}T{hour:02d}:00"


def member(subject_id):
    return {"id":subject_id, "first_name":"Synthetic", "major":"CS", "year":2}


def room(host=OWNER, start=13, end=14):
    return store.create_room(member(host), "math54", "MATH 54", instant(start), instant(end),
                             "coffee", "Synthetic cafe", 3)


def exam(runtime, subject_id=OWNER):
    runtime.repository.create_record(subject_id, "exams", ExamInput(course_id="math54",
        start=parse_instant(instant(16)), end=parse_instant(instant(17))))


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(store, "_rooms", [])
    value = get_runtime()
    for subject_id in (OWNER, GUEST):
        for kind in ("exams", "exceptions"):
            for record in value.repository.list_records(subject_id, kind):
                value.repository.delete_record(subject_id, kind, record.id)
        schedule_core.save_weekly({"mon":("13:00", "18:00")}, student_id=subject_id)
        value.service.provider.events[subject_id] = []
        value.service.refresh_calendar(Actor(subject_id=subject_id), FIXTURE_HORIZON)
    return value


@pytest.fixture
def browser(runtime):
    with TestClient(app) as value:
        assert value.post("/login", data={"email":accounts.TEST_EMAIL, "password":accounts.TEST_PASSWORD},
                          follow_redirects=False).status_code == 303
        yield value


def open_room(browser, start=16, end=17, **extra):
    return browser.post("/session/rooms", data={"course_id":"math54", "starts_at":instant(start),
        "ends_at":instant(end), "place_type":"coffee", "group_size":"3", **extra}, follow_redirects=False)


@pytest.mark.parametrize("study_anyway", ["0", "1"])
def test_room_creation_cannot_override_exam(runtime, browser, study_anyway):
    exam(runtime)
    response = open_room(browser, study_anyway=study_anyway)
    assert response.status_code == 303 and "exam" in unquote(response.headers["location"])
    assert store.rooms() == []


def test_room_picker_checks_actual_selected_time(runtime, browser):
    exam(runtime)
    response = open_room(browser, 13, 14, room_day=DAY, room_start="16:00", room_end="17:00")
    assert response.status_code == 303 and store.rooms() == []


@pytest.mark.parametrize("block", ["manual", "calendar", "unknown", "invalid"])
def test_room_creation_rejects_other_calendar_failures(runtime, browser, block):
    start, end = 16, 17
    if block == "manual":
        runtime.repository.create_record(OWNER, "exceptions", ExceptionInput(kind="unavailable",
            start=parse_instant(instant(start)), end=parse_instant(instant(end))))
    elif block == "calendar":
        runtime.service.provider.events[OWNER] = [PrivateEvent(id="synthetic-private-block",
            start=parse_instant(instant(start)), end=parse_instant(instant(end)),
            private_title="SYNTHETIC_TITLE_MUST_STAY_PRIVATE")]
    elif block == "unknown":
        runtime.service.provider.failure_mode = "timeout"
    else:
        start, end = 17, 16
    response = open_room(browser, start, end)
    assert response.status_code == 303 and store.rooms() == []
    assert "SYNTHETIC_TITLE_MUST_STAY_PRIVATE" not in unquote(response.headers["location"])


def test_room_outside_former_study_hours_opens(runtime, browser):
    # 18:00-19:00 used to be outside study hours; with no busy block it is free now.
    response = open_room(browser, 18, 19)
    assert response.status_code == 303 and len(store.rooms()) == 1


@pytest.mark.parametrize("posted_time", [False, True])
def test_join_checks_saved_room_time_not_submitted_time(runtime, browser, posted_time):
    exam(runtime)
    target = room(GUEST, 16, 17)
    before = deepcopy(store.rooms())
    data = {"course_id":"math54", "starts_at":instant(13), "ends_at":instant(14),
            "place_type":"coffee", "study_anyway":"1"} if posted_time else {}
    response = browser.post(f"/session/rooms/{target['id']}/join", data=data, follow_redirects=False)
    assert response.status_code == 303 and "exam" in unquote(response.headers["location"])
    assert store.rooms() == before


@pytest.mark.parametrize("blocked_subject", [OWNER, GUEST])
def test_change_room_time_checks_all_members_before_mutating(runtime, browser, blocked_subject):
    target = room()
    store.join_room(target["id"], member(GUEST))
    exam(runtime, blocked_subject)
    before = deepcopy(store.rooms())
    response = browser.post(f"/session/rooms/{target['id']}/time", data={
        "course_id":"math54", "starts_at":instant(13), "ends_at":instant(14), "place_type":"coffee",
        "room_day":DAY, "room_start":"16:00", "room_end":"17:00", "study_anyway":"1",
    }, follow_redirects=False)
    assert response.status_code == 303 and "exam" in unquote(response.headers["location"])
    assert store.rooms() == before


@pytest.mark.parametrize("start,end", [(15, 16), (17, 18)])
def test_room_creation_allows_half_open_adjacent_slots(runtime, browser, start, end):
    exam(runtime)
    assert open_room(browser, start, end).status_code == 303
    assert len(store.rooms()) == 1


def test_valid_create_resize_join_change_and_leave_still_work(runtime, browser):
    assert open_room(browser, 13, 14).status_code == 303
    target = store.rooms()[0]
    assert open_room(browser, 13, 14, group_size="4").status_code == 303
    assert len(store.rooms()) == 1 and store.rooms()[0]["capacity"] == 4
    response = browser.post(f"/session/rooms/{target['id']}/time", data={
        "course_id":"math54", "starts_at":instant(13), "ends_at":instant(14), "place_type":"coffee",
        "room_day":DAY, "room_start":"14:00", "room_end":"15:00",
    }, follow_redirects=False)
    assert response.status_code == 303 and store.rooms()[0]["starts_at"] == instant(14)
    guest_room = room(GUEST, 14, 15)
    response = browser.post(f"/session/rooms/{guest_room['id']}/join", follow_redirects=False)
    assert response.status_code == 303
    assert len(store.rooms()) == 1  # Existing switch behavior leaves the old overlapping room.
    assert {p["id"] for p in store.rooms()[0]["members"]} == {OWNER, GUEST}
    before = deepcopy(store.rooms())
    browser.post(f"/session/rooms/{guest_room['id']}/join", follow_redirects=False)
    assert store.rooms() == before
    browser.post(f"/session/rooms/{guest_room['id']}/leave", follow_redirects=False)
    assert [p["id"] for p in store.rooms()[0]["members"]] == [GUEST]


def test_rejected_room_change_preserves_host_permissions(runtime, browser):
    target = room(GUEST)
    before = deepcopy(store.rooms())
    response = browser.post(f"/session/rooms/{target['id']}/time", data={
        "course_id":"math54", "starts_at":instant(13), "ends_at":instant(14), "place_type":"coffee",
        "room_day":DAY, "room_start":"14:00", "room_end":"15:00",
    }, follow_redirects=False)
    assert response.status_code == 303 and "Only the host" in unquote(response.headers["location"])
    assert store.rooms() == before


def test_unknown_demo_account_cannot_borrow_owner_calendar(runtime, monkeypatch):
    monkeypatch.setattr(accounts, "_accounts", deepcopy(accounts._accounts))
    accounts.register("Synthetic Stranger", "stranger@example.test", "synthetic-password")
    with TestClient(app) as browser:
        browser.post("/login", data={"email":"stranger@example.test", "password":"synthetic-password"})
        response = open_room(browser, 13, 14)
        assert response.status_code == 303 and store.rooms() == []
