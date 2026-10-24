"""What the student said is a hard boundary for rooms; what they did not say is left to the ranking.

Seed (Thu 29 Oct): room_yali_thu (Leo, CS 61A, 6-8 PM, Yali's Cafe, room for 2) and
room_doe_thu (Jordan, any CS, 7-9 PM, Doe Library, room for 4). Jordan ticked quiet
focus; Leo ticked practice questions.
"""

import re

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
def fresh_calendar(monkeypatch):
    get_runtime().service.refresh_calendar(Actor(subject_id=store.OWNER_ID), FIXTURE_HORIZON)
    monkeypatch.setattr(store, "_rooms", store.rooms())


def kept(said, course_id="subject:cs"):
    rooms = s.rooms_you_can_join("u_maya", course_id, *THU)
    return [room["id"] for room in room_matching.hard_filter(rooms, s._asked_factors(said), "u_maya")]


@pytest.mark.parametrize(
    "said, rooms",
    [
        ("cs thursday 6 to 9", {"room_yali_thu", "room_doe_thu"}),
        ("cs thursday 6 to 9 in the lib", {"room_doe_thu"}),
        ("cs thursday 6 to 9 at a cafe", {"room_yali_thu"}),
        ("cs thursday 6 to 9, not a library", {"room_yali_thu"}),
        ("cs thursday 6 to 9 outside", set()),
        ("cs thursday 6 to 9 group of 4", {"room_doe_thu"}),
        ("cs thursday 6 to 9 with 2 people", {"room_yali_thu"}),
        ("cs thursday 6 to 9, at least 3 people", {"room_doe_thu"}),
    ],
    ids=["nothing extra", "library", "cafe", "not a library", "outdoor", "group of 4", "pair", "at least 3"],
)
def test_said_place_and_size_are_hard(said, rooms):
    assert set(kept(said)) == rooms


def test_said_style_is_hard_and_no_preference_hosts_fit(monkeypatch):
    profiles = {"u_jordan": ["quiet"], "u_leo": ["drill"]}
    real = room_matching.public_profile_of
    monkeypatch.setattr(room_matching, "public_profile_of",
                        lambda sid: real(sid).model_copy(update={"study_styles": profiles.get(sid, [])}))
    assert set(kept("cs thursday 6 to 9, somewhere quiet")) == {"room_doe_thu"}
    assert set(kept("cs thursday 6 to 9, practice problems")) == {"room_yali_thu"}
    assert set(kept("cs thursday 6 to 9, I want to talk it through")) == set()
    profiles["u_leo"] = []
    assert set(kept("cs thursday 6 to 9, I want to talk it through")) == {"room_yali_thu"}


def test_rooms_you_are_in_stay_visible():
    rooms = s.rooms_you_can_join("u_leo", "subject:cs", *THU)
    assert "room_yali_thu" in [r["id"] for r in room_matching.hard_filter(rooms, s._asked_factors("in the lib"), "u_leo")]


@pytest.mark.parametrize(
    "asked, room, fits",
    [
        ("cs61a", "cs61a", True), ("cs61a", "subject:cs", True), ("cs61a", "subject:any", True),
        ("cs61a", "cs61b", False), ("cs61a", "subject:math", False),
        ("subject:cs", "cs61b", True), ("subject:cs", "math54", False),
        ("subject:any", "math54", True),
    ],
)
def test_a_named_course_is_hard_and_a_subject_is_a_range(asked, room, fits):
    assert s._subject_fits(asked, room) is fits


def test_reasons_name_each_factor_that_was_asked():
    rooms = {r["id"]: r for r in s.rooms_you_can_join("u_maya", "cs61a", *THU)}
    asked = s._asked_factors("CS 61A thursday 6 to 9 in the lib, group of 4")
    lines = room_matching.reasons(rooms["room_doe_thu"], asked, "CS 61A", "Thu 29 Oct, 6:00 PM–9:00 PM")
    assert lines == [
        "Fits your time: 7:00 PM–9:00 PM, inside Thu 29 Oct, 6:00 PM–9:00 PM.",
        "Any CS course covers CS 61A.",
        "Doe Library is a library, as you asked.",
        "A room for 4, as you asked.",
        "2 seats open.",
    ]
    exact = room_matching.reasons(rooms["room_yali_thu"], room_matching.Asked(), "CS 61A", None)
    assert exact == ["CS 61A, as you asked.", "1 seat open."]


# Room settings


def login():
    client = TestClient(app)
    client.post("/login", data={"email": accounts.TEST_EMAIL, "password": accounts.TEST_PASSWORD})
    return client


KEEP = dict(course_id="subject:any", starts_at="2026-10-30T15:00", ends_at="2026-10-30T17:00", place_type="library",
            note="", study_anyway="0", sentence="study friday 3 to 5")


def open_room(client):
    page = client.post("/session/rooms", data={**KEEP, "group_size": "2"}).text
    return re.search(r'action="/session/rooms/([^/]+)/settings"', page).group(1), page


def settings(**fields):
    return {**KEEP, "room_course": "subject:cs", "room_place": "Yali's Cafe", "room_capacity": "4",
            "room_day": "2026-10-30", "room_start": "15:30", "room_end": "17:00", **fields}


def test_a_new_room_shows_its_settings():
    _, page = open_room(login())
    assert "Room settings" in page and "Save room settings" in page


def test_the_host_changes_subject_place_size_and_time():
    client = login()
    room_id, _ = open_room(client)
    page = client.post(f"/session/rooms/{room_id}/settings", data=settings()).text
    room = next(r for r in store.rooms() if r["id"] == room_id)
    assert "Room settings saved." in page
    assert (room["course_code"], room["place_name"], room["place_type"], room["capacity"], room["starts_at"]) == (
        "Any CS course", "Yali's Cafe", "coffee", 4, "2026-10-30T15:30")


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"room_capacity": "9"}, "A room is for 2 to 5 people."),
        ({"room_start": "17:00", "room_end": "16:00"}, "Choose a place, a day, and a start time earlier than the end time."),
        ({"room_place": "Nowhere"}, "Choose a place, a day, and a start time earlier than the end time."),
    ],
)
def test_bad_settings_are_refused(fields, message):
    client = login()
    room_id, _ = open_room(client)
    before = next(r for r in store.rooms() if r["id"] == room_id)
    page = client.post(f"/session/rooms/{room_id}/settings", data=settings(**fields)).text
    assert message in page and next(r for r in store.rooms() if r["id"] == room_id) == before


def test_a_room_never_shrinks_below_its_people():
    change = dict(course_id="subject:cs", course_code="Any CS course", place_type="library", place_name="Doe Library",
                  starts_at="2026-10-29T19:00", ends_at="2026-10-29T21:00")
    assert store.join_room("room_doe_thu", {"id": "u_ken", "first_name": "Ken", "major": "Physics", "year": 2}) == "joined"
    assert store.update_room_settings("room_doe_thu", "u_jordan", capacity=2, **change) == "crowded"
    assert store.update_room_settings("room_doe_thu", "u_jordan", capacity=3, **change) == "updated"


def test_only_the_host_can_change_a_room():
    page = login().post("/session/rooms/room_doe_thu/settings", data=settings(room_day="2026-10-29")).text
    assert "Only the host can change the room." in page
