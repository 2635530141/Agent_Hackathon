"""Interval maths and free time. Tue 27 Oct 2026 is a Tuesday."""

from app import matching_core as m
from factories import at, person


def span(a, b):
    return (at(a), at(b))


def test_merge_joins_overlapping_and_touching():
    merged = m._merge(
        [span("2026-10-27T20:00", "2026-10-27T21:00"), span("2026-10-27T18:00", "2026-10-27T19:00"), span("2026-10-27T19:00", "2026-10-27T19:30")]
    )
    assert merged == [span("2026-10-27T18:00", "2026-10-27T19:30"), span("2026-10-27T20:00", "2026-10-27T21:00")]


def test_subtract_splits_and_removes():
    evening = [span("2026-10-27T18:00", "2026-10-27T21:00")]
    assert m._subtract(evening, [span("2026-10-27T19:00", "2026-10-27T20:00")]) == [
        span("2026-10-27T18:00", "2026-10-27T19:00"),
        span("2026-10-27T20:00", "2026-10-27T21:00"),
    ]
    assert m._subtract(evening, [span("2026-10-27T17:00", "2026-10-27T22:00")]) == []
    assert m._subtract(evening, [span("2026-10-27T08:00", "2026-10-27T09:00")]) == evening


def test_intersect():
    a = [span("2026-10-27T18:00", "2026-10-27T20:00"), span("2026-10-27T21:00", "2026-10-27T22:00")]
    b = [span("2026-10-27T19:00", "2026-10-27T21:30")]
    assert m._intersect(a, b) == [span("2026-10-27T19:00", "2026-10-27T20:00"), span("2026-10-27T21:00", "2026-10-27T21:30")]
    assert m._intersect(a, [span("2026-10-28T19:00", "2026-10-28T20:00")]) == []


def test_free_time_uses_weekly_hours_per_day():
    p = person("u", "U V", weekly={"tue": ("18:00", "21:00"), "thu": ("16:00", "17:00")})
    assert m.free_time(p, at("2026-10-26T00:00"), at("2026-10-30T00:00")) == [
        span("2026-10-27T18:00", "2026-10-27T21:00"),
        span("2026-10-29T16:00", "2026-10-29T17:00"),
    ]


def test_free_time_is_clipped_to_the_window():
    p = person("u", "U V", weekly={"tue": ("18:00", "21:00")})
    assert m.free_time(p, at("2026-10-27T19:30"), at("2026-10-27T20:00")) == [span("2026-10-27T19:30", "2026-10-27T20:00")]


def test_free_time_subtracts_calendar_exam_and_busy_one_offs():
    p = person(
        "u",
        "U V",
        weekly={"tue": ("17:00", "22:00")},
        busy=[
            ("calendar", "2026-10-27T17:00", "2026-10-27T18:00"),
            ("exception", "2026-10-27T19:00", "2026-10-27T19:30", "busy"),
        ],
        exams=[{"id": "e", "course_id": "cs61a", "starts_at": "2026-10-27T21:00", "ends_at": "2026-10-27T23:00"}],
    )
    assert m.free_time(p, at("2026-10-27T00:00"), at("2026-10-28T00:00")) == [
        span("2026-10-27T18:00", "2026-10-27T19:00"),
        span("2026-10-27T19:30", "2026-10-27T21:00"),
    ]


def test_free_one_off_adds_time_outside_weekly_hours():
    p = person("u", "U V", weekly={}, busy=[("exception", "2026-10-31T10:00", "2026-10-31T12:00", "free")])
    assert m.free_time(p, at("2026-10-31T00:00"), at("2026-11-01T00:00")) == [span("2026-10-31T10:00", "2026-10-31T12:00")]


def test_weekly_rows_in_busy_list_are_not_blocks():
    p = person("u", "U V", weekly={"thu": ("16:00", "21:00")}, busy=[("weekly", "2026-10-29T16:00", "2026-10-29T21:00")])
    assert m.free_time(p, at("2026-10-29T00:00"), at("2026-10-30T00:00")) == [span("2026-10-29T16:00", "2026-10-29T21:00")]


def test_no_weekly_hours_means_never_free():
    assert m.free_time(person("u", "U V", weekly={}), at("2026-10-26T00:00"), at("2026-11-08T00:00")) == []
