"""The time Filter: the only hard constraint in code. Everything else is a fact on the record."""

import json

import pytest

from app import matching_core as m
from factories import ME, inputs, person, request

FACT_FIELDS = {
    "id", "first_name", "major", "year", "courses", "shared_courses", "style", "exams",
    "free_overlap", "other_free", "within_radius", "minutes_away", "bio",
}
FAR = {"anchor": "home", "anchor_lat": 37.8044, "anchor_lng": -122.2712, "radius_meters": 1000}


def filtered(*people, me=ME, eligible=None, **req):
    found, funnel, later, message = m.find_matches(request(**req), inputs(me, *people, eligible=eligible))
    return found, funnel, later, message


def ids(found):
    return [cand.person.profile.id for cand in found]


def test_course_radius_style_and_year_never_filter():
    people = [
        person("u_course", "Other Course", courses=("math54",)),
        person("u_far", "Far Away", **FAR),
        person("u_style", "Talker", study_style="talk"),
        person("u_year", "Fresh Man", year=1, major="Physics"),
        person("u_group", "Big Group", group_size=4),
    ]
    found, funnel, later, _ = filtered(*people)
    assert sorted(ids(found)) == sorted(p.profile.id for p in people)
    assert funnel == [("eligible", 5), ("free in your window", 5)]
    assert not later


@pytest.mark.parametrize(
    "blocked",
    [
        person("u_x", "No Hours", weekly={}),
        person("u_x", "Calendar", busy=[("calendar", "2026-10-27T18:30", "2026-10-27T21:30")]),
        person("u_x", "One Off", busy=[("exception", "2026-10-27T19:00", "2026-10-27T21:00", "busy")]),
        person("u_x", "Exam", exams=[{"id": "e", "course_id": "cs61a", "starts_at": "2026-10-27T19:00", "ends_at": "2026-10-27T21:00"}]),
    ],
    ids=["weekly hours", "calendar", "one-off block", "exam"],
)
def test_busy_time_is_a_hard_stop(blocked):
    assert ids(filtered(blocked, person("u_ok", "Ok Ok"))[0]) == ["u_ok"]


def test_other_persons_calendar_has_no_override():
    blocked = person("u_x", "Calendar", busy=[("calendar", "2026-10-27T19:00", "2026-10-27T21:00")])
    found, _, later, _ = filtered(blocked, study_anyway=True)
    assert later and found[0].facts["free_overlap"] == []


def test_own_calendar_counts_unless_study_anyway():
    me = person("u_me", "Maya Chen", busy=[("calendar", "2026-10-27T19:00", "2026-10-27T20:00")])
    [cand] = filtered(person("u_a", "Alex Kim"), me=me)[0]
    assert cand.facts["free_overlap"] == [{"starts_at": "2026-10-27T20:00-07:00", "ends_at": "2026-10-27T21:00-07:00"}]
    [cand] = filtered(person("u_a", "Alex Kim"), me=me, study_anyway=True)[0]
    assert cand.facts["free_overlap"] == [{"starts_at": "2026-10-27T19:00-07:00", "ends_at": "2026-10-27T21:00-07:00"}]


def test_needs_an_hour_or_the_whole_short_request():
    short = person("u_a", "Alex Kim", weekly={"tue": ("17:00", "19:50")})
    hour = person("u_b", "Bo Wu", weekly={"tue": ("17:00", "20:00")})
    assert ids(filtered(short, hour)[0]) == ["u_b"]
    half = person("u_c", "Cy Lam", weekly={"tue": ("19:00", "19:30")})
    assert ids(filtered(half, starts_at="2026-10-27T19:00", ends_at="2026-10-27T19:30")[0]) == ["u_c"]


def test_ordered_by_overlap_length_then_id_and_capped_at_twelve():
    hour = person("u_hour", "One Hour", weekly={"tue": ("20:00", "22:00")})
    people = [person(f"u_{i:02d}", f"P{i} X") for i in range(14)] + [hour]
    found = filtered(*people)[0]
    assert len(found) == m.FILTER_LIMIT
    assert ids(found) == [f"u_{i:02d}" for i in range(12)]
    two = filtered(hour, person("u_z", "Zed Full"))[0]
    assert ids(two) == ["u_z", "u_hour"]


def test_only_eligible_ids_and_never_yourself():
    assert ids(filtered(ME, person("u_a", "Alex Kim"), person("u_b", "Bo Wu"), eligible=["u_me", "u_b"])[0]) == ["u_b"]


def test_facts_have_exactly_the_documented_fields():
    exam = {"course_id": "math54", "starts_at": "2026-11-06T09:00", "ends_at": "2026-11-06T12:00", "priority": True,
            "priority_starts_at": "2026-10-27T09:00"}
    me = person("u_me", "Maya Chen", courses=("cs61a", "math54"), exams=[{"id": "e0", **exam}])
    other = person(
        "u_a", "Alex Kim", courses=("math54", "data8"), study_style="drill", major="Physics", year=2,
        anchor="home", anchor_lat=37.8790, anchor_lng=-122.2585, radius_meters=500,
        bio="x" * 400, exams=[{"id": "e1", **exam}],
    )
    [cand] = filtered(other, me=me)[0]
    facts = cand.facts
    assert set(facts) == FACT_FIELDS
    assert facts["courses"] == ["Math 54", "Data 8"]
    assert facts["shared_courses"] == ["Math 54"]
    assert facts["style"] == "drill practice questions"
    assert facts["exams"] == [{"course": "Math 54", "date": "2026-11-06", "in_priority_window": True, "same_exam_as_requester": True}]
    assert facts["within_radius"] == {"requester": True, "candidate": False}
    assert facts["minutes_away"] == {"requester": 10, "candidate": 10}
    assert len(facts["bio"]) == m.BIO_LIMIT


def test_other_free_lists_the_next_shared_windows_outside_the_request():
    [cand] = filtered(person("u_a", "Alex Kim"))[0]
    assert cand.facts["other_free"] == [
        {"starts_at": "2026-10-28T17:00-07:00", "ends_at": "2026-10-28T22:00-07:00"},
        {"starts_at": "2026-10-29T17:00-07:00", "ends_at": "2026-10-29T22:00-07:00"},
        {"starts_at": "2026-10-30T17:00-07:00", "ends_at": "2026-10-30T22:00-07:00"},
    ]


def test_nobody_free_returns_people_with_later_windows():
    blocked = person("u_a", "Alex Kim", busy=[("calendar", "2026-10-27T17:00", "2026-10-27T22:00")])
    found, funnel, later, _ = filtered(blocked)
    assert later
    assert funnel[-1] == ("free in your window", 0)
    assert found[0].facts["free_overlap"] == []
    assert found[0].facts["other_free"][0] == {"starts_at": "2026-10-28T17:00-07:00", "ends_at": "2026-10-28T22:00-07:00"}


@pytest.mark.parametrize(
    "people, message",
    [
        ((), "Nobody else is eligible to match yet."),
        ((person("u_a", "Alex Kim", weekly={}),), "Nobody is free then, or in the next two weeks."),
    ],
)
def test_messages_when_nobody_is_left(people, message):
    found, _, _, said = filtered(*people)
    assert (found, said) == ([], message)


def test_private_fields_never_reach_the_record():
    other = person("u_a", "Alex Kim", busy=[("calendar", "2026-10-28T09:00", "2026-10-28T10:00")])
    [cand] = filtered(other)[0]
    text = json.dumps(cand.facts)
    for private in ("anchor", "lat", "lng", "email", "street", "gender", "title", "radius_meters"):
        assert private not in text


def test_agent_duration_is_the_minimum():
    half = person("u_a", "Alex Kim", weekly={"tue": ("19:00", "19:30")})
    found, *_ = m.find_matches(request(), inputs(ME, half), duration_minutes=30)
    assert ids(found) == ["u_a"]
    with pytest.raises(ValueError):
        m.find_matches(request(), inputs(ME, half), duration_minutes=180)


def test_filter_preview_makes_no_model_call(monkeypatch):
    monkeypatch.setattr(m.advise_step, "advise", lambda *a, **k: pytest.fail("Advise was called"))
    result = m.filter_preview(request(), inputs(ME, person("u_a", "Alex Kim")))
    assert ids(result.candidates) == ["u_a"] and result.proposal is None
