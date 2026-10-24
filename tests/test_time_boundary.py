"""The requested time is a hard boundary: parsing, the organizer's conflict, and joining rooms."""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app import accounts, schedule_core, store
from app.main import app
from app.routers import session as s
from app.scheduling.core.schemas import Actor
from app.scheduling.demo import FIXTURE_HORIZON
from app.scheduling.runtime import get_runtime


@pytest.fixture(autouse=True)
def fresh_calendar():
    get_runtime().service.refresh_calendar(Actor(subject_id=store.OWNER_ID), FIXTURE_HORIZON)


@pytest.mark.parametrize(
    "text, window",
    [
        ("7pm to 9pm wednesday", ("2026-10-28T19:00", "2026-10-28T21:00")),
        ("wednesday 7-9pm", ("2026-10-28T19:00", "2026-10-28T21:00")),
        ("wednesday 7-9", ("2026-10-28T19:00", "2026-10-28T21:00")),
        ("wednesday from 7 to 9", ("2026-10-28T19:00", "2026-10-28T21:00")),
        ("wednesday between 6 and 8pm", ("2026-10-28T18:00", "2026-10-28T20:00")),
        ("wednesday 10am to 12pm", ("2026-10-28T10:00", "2026-10-28T12:00")),
        ("CS 61A wednesday 3 to 5", ("2026-10-28T15:00", "2026-10-28T17:00")),
        ("wednesday at 7pm", ("2026-10-28T19:00", "2026-10-28T20:00")),
        ("wednesday evening", ("2026-10-28T18:00", "2026-10-28T21:00")),
    ],
)
def test_a_spoken_range_keeps_both_ends(text, window):
    assert s._local_window(text) == window


@pytest.mark.parametrize("text, place", [("in lib", "library"), ("at the library", "library"), ("not the lib", ""), ("liberal arts", "")])
def test_lib_means_library(text, place):
    assert s._mentioned_place(text) == place


def test_reported_session_keeps_wednesday_7_to_9_in_the_library():
    client = TestClient(app)
    client.post("/login", data={"email": accounts.TEST_EMAIL, "password": accounts.TEST_PASSWORD})
    client.post("/session/interpret", data={"sentence": "I want to study computer science in lib"})
    answer = client.post("/session/interpret", data={"sentence": "I want to study computer science in lib",
                                                       "reply": "7pm to 9pm wednesday", "followup": "1"}, follow_redirects=False)
    location = answer.headers["location"]
    assert "starts_at=2026-10-28T19%3A00" in location and "ends_at=2026-10-28T21%3A00" in location
    assert "place_type=library" in location
    page = client.get(location).text
    assert "You're busy then." not in page


def test_outside_usual_hours_does_not_block_a_chosen_time():
    assert "wed" not in {day for day, hours in schedule_core.weekly_hours().items() if hours[0]}
    assert schedule_core.own_conflict("2026-10-28T19:00", "2026-10-28T21:00", False) is None


def test_alternative_stays_inside_the_requested_window():
    conflict = schedule_core.own_conflict("2026-10-29T18:00", "2026-10-29T21:00", False)
    assert conflict["codes"] == ["CALENDAR_BUSY"]
    assert (conflict["alternative_start"], conflict["alternative_end"]) == ("2026-10-29T19:00", "2026-10-29T21:00")


@pytest.mark.parametrize(
    "starts_at, ends_at, code",
    [("2026-10-29T18:00", "2026-10-29T19:00", "CALENDAR_BUSY"), ("2026-11-06T09:00", "2026-11-06T12:00", "EXAM_BUSY")],
)
def test_no_alternative_when_the_whole_window_is_busy(starts_at, ends_at, code):
    conflict = schedule_core.own_conflict(starts_at, ends_at, False)
    assert conflict["codes"] == [code] and conflict["alternative_start"] is None


def at(value):
    return datetime.fromisoformat(value)


WINDOW = (at("2026-10-28T19:00"), at("2026-10-28T21:00"))


@pytest.mark.parametrize(
    "room, fits",
    [
        (("2026-10-28T19:30", "2026-10-28T20:30"), True),
        (("2026-10-28T18:00", "2026-10-28T20:00"), True),
        (("2026-10-28T20:30", "2026-10-28T22:00"), False),
        (("2026-10-28T18:00", "2026-10-28T19:30"), False),
        (("2026-10-29T16:00", "2026-10-29T17:00"), False),
        (("2026-10-28T19:00", "2026-10-28T19:30"), True),
    ],
    ids=["inside", "shares an hour", "30 minutes late", "30 minutes early", "another day", "short room inside"],
)
def test_a_room_must_fit_the_window(room, fits):
    assert s._fits(at(room[0]), at(room[1]), *WINDOW) is fits
