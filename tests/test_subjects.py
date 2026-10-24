"""A session can name a course, a subject (a range of courses), or nothing at all (general study)."""

import pytest
from fastapi.testclient import TestClient

from app import accounts, room_matching, store
from app.main import app
from app.routers import session as s
from app.scheduling.core.schemas import Actor
from app.scheduling.demo import FIXTURE_HORIZON
from app.scheduling.runtime import get_runtime

THU = ("2026-10-29T18:00", "2026-10-29T21:00")


@pytest.fixture(autouse=True)
def fresh_calendar():
    get_runtime().service.refresh_calendar(Actor(subject_id=store.OWNER_ID), FIXTURE_HORIZON)


def understood(text):
    parsed, question = s._clarify({}, store.courses(), [{"role": "user", "text": text}])
    return (parsed or {}).get("course_id"), question


@pytest.mark.parametrize(
    "text, course_id",
    [
        ("CS 61A thursday 6 to 9", "cs61a"),
        ("Data 8 thursday 6 to 9", "data8"),
        ("computer science thursday evening", "subject:cs"),
        ("any math class wednesday 7-9pm", "subject:math"),
        ("data science thursday 6 to 9", "subject:data"),
        ("statistics thursday evening", "subject:data"),
        ("physics thursday evening", "subject:physics"),
        ("econ thursday evening", "subject:econ"),
        ("essay writing thursday evening", "subject:writing"),
        ("I just want to study thursday evening", "subject:any"),
        ("homework session wednesday 7 to 9 in the lib", "subject:any"),
    ],
)
def test_course_subject_or_general_study_never_asks_for_a_course(text, course_id):
    assert understood(text) == (course_id, "")


def test_time_is_still_asked_for():
    course_id, question = understood("I want to study physics")
    assert course_id is None and question == "You didn't specify a time. What time do you want to meet?"


@pytest.mark.parametrize(
    "course_id, family",
    [("cs61a", "cs"), ("cs61b", "cs"), ("math54", "math"), ("data8", "data"), ("Physics 7A", "physics"),
     ("subject:any", "any"), ("subject:cs", "cs")],
)
def test_every_course_belongs_to_a_subject(course_id, family):
    assert s._family(course_id) == family


def test_labels():
    assert s._course_record("subject:any")["code"] == "Any subject"
    assert s._course_record("subject:data")["code"] == "Any Data/Statistics course"
    assert room_matching._course_code("subject:any") == "Any subject"


def room_ids(course_id, *window):
    return [room["id"] for room in s.eligible_rooms(course_id, *window, "u_maya")]


def test_general_study_sees_rooms_of_every_subject():
    assert set(room_ids("subject:any", *THU)) == {"room_yali_thu", "room_doe_thu"}


def test_a_subject_sees_only_its_rooms():
    assert set(room_ids("subject:cs", *THU)) == {"room_yali_thu", "room_doe_thu"}
    assert room_ids("subject:math", *THU) == []
    assert room_ids("math54", *THU) == []


def test_a_general_room_welcomes_any_subject(monkeypatch):
    monkeypatch.setattr(store, "_rooms", store.rooms())  # This test adds a room; keep it out of other tests.
    host = {"id": "u_jordan", "first_name": "Jordan", "major": "EECS", "year": 3}
    store.create_room(host, "subject:any", "Any subject", "2026-10-29T19:00", "2026-10-29T21:00", "library", "Doe Library", 3)
    general = [room["id"] for room in store.rooms() if room["course_id"] == "subject:any"]
    assert general and set(general) <= set(room_ids("math54", *THU))
    assert set(general) <= set(room_ids("subject:physics", *THU))


def test_room_ranking_tells_the_model_it_is_general_study():
    facts = room_matching.request_facts("subject:any", *THU, "library", "")
    assert facts["course_code"] == "Any subject"


def test_session_page_for_general_study():
    client = TestClient(app)
    client.post("/login", data={"email": accounts.TEST_EMAIL, "password": accounts.TEST_PASSWORD})
    answer = client.post("/session/interpret", data={"sentence": "I just want to study thursday 6 to 9"}, follow_redirects=False)
    assert answer.status_code == 303 and "course_id=subject%3Aany" in answer.headers["location"]
    page = client.get(answer.headers["location"]).text
    assert "Any subject" in page and "Which course" not in page
