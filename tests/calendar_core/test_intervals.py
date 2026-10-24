import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from pydantic import ValidationError
from app.scheduling.core.intervals import (alternative, clip, contains, duration_filter,
                                         normalize, overlaps, subtract)
from app.scheduling.core.schemas import Horizon, Interval

CASES = {c["id"]: c for c in json.loads((Path(__file__).parents[1] / "fixtures/calendar_cases.json").read_text(encoding="utf-8"))["cases"]}


def intervals(pairs):
    return [Interval(start=f"2026-10-26T{s}:00-07:00", end=f"2026-10-26T{e}:00-07:00") for s, e in pairs]


@pytest.mark.parametrize("case_id", ["C01", "C02", "C03"])
def test_supplied_interval_evals(case_id):
    case = CASES[case_id]
    given = case["given"]
    free = intervals(given.get("free", given.get("weekly", [])) + given.get("added", []))
    busy = intervals(given.get("busy", []) + given.get("google_busy", []) + given.get("exam_busy", []) + given.get("unavailable", []))
    assert subtract(free, busy) == intervals(case["expected"]["free"])
    if "conflict" in case["expected"]:
        assert overlaps(free[0], busy[0]) is case["expected"]["conflict"]


def test_normalize_merge_then_subtract_and_filter_C33():
    free = intervals([["13:00", "14:00"], ["14:00", "18:00"], ["13:30", "14:30"]])
    busy = intervals([["13:30", "14:00"], ["15:00", "16:30"]])
    result = subtract(free, busy)
    assert [int((i.end-i.start).total_seconds()/60) for i in result] == [30, 60, 90]
    assert duration_filter(result, timedelta(minutes=90)) == intervals([["16:30", "18:00"]])


def test_nested_unsorted_and_covering_blocks():
    free = intervals([["13:00", "18:00"]])
    blocks = intervals([["16:00", "17:00"], ["14:30", "15:30"], ["14:00", "16:00"]])
    assert subtract(free, blocks) == intervals([["13:00", "14:00"], ["17:00", "18:00"]])
    assert subtract(free, intervals([["12:00", "19:00"]])) == []
    assert subtract([], blocks) == []


def test_clip_contains_and_earliest_equal_duration():
    free = intervals([["13:00", "14:00"], ["15:00", "16:00"], ["17:00", "18:00"]])
    slot = intervals([["14:00", "15:00"]])[0]
    assert alternative(free, slot) == free[1]
    assert alternative(free, intervals([["17:30", "18:00"]])[0]) == intervals([["17:30", "18:00"]])[0]
    assert alternative(free, intervals([["14:00", "16:00"]])[0]) is None
    assert not contains(free, slot)
    assert contains(free, free[0])
    assert clip(free, intervals([["13:30", "17:30"]])[0]) == intervals([["13:30", "14:00"], ["15:00", "16:00"], ["17:00", "17:30"]])


@pytest.mark.parametrize("start,end", [
    ("2026-10-26T13:00", "2026-10-26T14:00"),
    ("2026-10-26T13:00Z", "2026-10-26T13:00Z"),
    ("2026-10-26T14:00Z", "2026-10-26T13:00Z"),
])
def test_reject_invalid_concrete_intervals(start, end):
    with pytest.raises(ValidationError):
        Interval(start=start, end=end)


def test_horizon_bound_and_utc_duration_across_fold():
    with pytest.raises(ValidationError):
        Horizon(start="2026-01-01T00:00Z", end="2026-03-01T00:00Z")
    zone = ZoneInfo("America/Los_Angeles")
    a = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    b = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    interval = Interval(start=a, end=b)
    assert interval.end - interval.start == timedelta(hours=1)


def test_subtraction_properties_on_discrete_minute_grid():
    # Independent minute-set oracle tests overlapping, adjacent and nested intervals.
    origin = datetime.fromisoformat("2026-10-26T00:00:00+00:00")
    for mask in range(64):
        pairs = [(0, 5), (4, 10)]
        blocks = [(i, i+2) for i in range(6) if mask & (1 << i)]
        make = lambda xs: [Interval(start=origin+timedelta(minutes=a), end=origin+timedelta(minutes=b)) for a,b in xs]
        result = subtract(make(pairs), make(blocks))
        expected = set(range(10)) - {m for a,b in blocks for m in range(a,b)}
        actual = {m for m in range(10) if any(i.start <= origin+timedelta(minutes=m) < i.end for i in result)}
        assert actual == expected
        assert normalize(result) == result
