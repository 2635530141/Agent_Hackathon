"""Short day names and partial course codes are read by the rules, so no model call is needed."""

import pytest

from app import store
from app.routers import session as s


def understood(text):
    parsed, question = s._clarify({}, store.courses(), [{"role": "user", "text": text}])
    return parsed, question


def test_reported_sentence_needs_no_follow_up_question():
    parsed, question = understood("I want to study cs61 at lib in around 8 pm wed")
    assert question == ""
    assert (parsed["course_id"], parsed["starts_at"], parsed["ends_at"], parsed["place_type"]) == (
        "subject:cs", "2026-10-28T20:00", "2026-10-28T21:00", "library")


@pytest.mark.parametrize(
    "text, window",
    [
        ("wed 8pm", ("2026-10-28T20:00", "2026-10-28T21:00")),
        ("at 8pm wed", ("2026-10-28T20:00", "2026-10-28T21:00")),
        ("thurs 6-9pm", ("2026-10-29T18:00", "2026-10-29T21:00")),
        ("thu 6 to 9", ("2026-10-29T18:00", "2026-10-29T21:00")),
        ("tue at 7", ("2026-10-27T19:00", "2026-10-27T20:00")),
        ("fri 3 to 5", ("2026-10-30T15:00", "2026-10-30T17:00")),
        ("mon evening", ("2026-10-26T18:00", "2026-10-26T21:00")),
        ("wednesday 7pm", ("2026-10-28T19:00", "2026-10-28T20:00")),
    ],
)
def test_short_day_names(text, window):
    assert s._local_window(text) == window


def test_a_full_day_name_wins_over_a_word_like_sat():
    assert s._local_window("I sat in the library wednesday 7pm") == ("2026-10-28T19:00", "2026-10-28T20:00")


@pytest.mark.parametrize("text, course_id", [("cs61 wed 8pm", "subject:cs"), ("math 1a fri 3 to 5", "subject:math"),
                                             ("CS 61A wed 8pm", "cs61a"), ("study wed 8pm", "subject:any")])
def test_a_partial_course_code_keeps_its_subject(text, course_id):
    assert understood(text)[0]["course_id"] == course_id
