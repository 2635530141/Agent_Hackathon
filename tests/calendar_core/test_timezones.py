from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from pydantic import ValidationError
from app.scheduling.core.recurrence import expand, local_candidates
from app.scheduling.core.schemas import Horizon, WeeklyRule
from test_intervals import CASES


def rule(**changes):
    return WeeklyRule.model_validate(dict(id="rule", owner_id="sample", weekday=6, start_local="15:00",
        end_local="16:00", timezone="America/Los_Angeles", **changes))


def test_supplied_DST_eval_C15():
    given = CASES["C15"]["given"]
    weekly = WeeklyRule(id="rule", owner_id="sample", weekday=given["weekday"],
        start_local=given["local_start"], end_local=given["local_end"], timezone=given["timezone"])
    horizon = Horizon(start="2026-10-24T07:00Z", end="2026-11-02T08:00Z")
    occurrences, warnings = expand([weekly], horizon)
    assert [i.start.isoformat().replace("+00:00", "Z") for i in occurrences] == CASES["C15"]["expected"]["utc_starts"]
    assert warnings == []
    assert all(i.start.astimezone(ZoneInfo(given["timezone"])).hour == 15 for i in occurrences)


@pytest.mark.parametrize("wall,count", [("2026-11-01T01:30", 2), ("2026-03-08T02:30", 0), ("2026-03-08T03:30", 1)])
def test_fold_gap_detection_C16(wall, count):
    assert len(local_candidates(datetime.fromisoformat(wall), ZoneInfo("America/Los_Angeles"))) == count


@pytest.mark.parametrize("start,end,day", [("01:30", "02:30", "2026-11-01"), ("02:30", "03:30", "2026-03-08")])
def test_ambiguous_or_nonexistent_occurrences_excluded_with_warning(start, end, day):
    weekly = WeeklyRule(id="rule", owner_id="sample", weekday=6, start_local=start, end_local=end, timezone="America/Los_Angeles")
    intervals, warnings = expand([weekly], Horizon(start=day+"T00:00-08:00", end=day+"T23:59-07:00"))
    assert intervals == []
    assert len(warnings) == 1 and warnings[0].startswith("DST_OCCURRENCE_EXCLUDED")


def test_overnight_clipping_and_inclusive_validity_dates():
    weekly = WeeklyRule(id="overnight", owner_id="sample", weekday=0, start_local="23:00", end_local="02:00",
        ends_next_day=True, timezone="America/Los_Angeles", valid_from="2026-10-26", valid_until="2026-10-26")
    intervals, warnings = expand([weekly], Horizon(start="2026-10-27T00:00-07:00", end="2026-10-28T00:00-07:00"))
    assert intervals[0].model_dump(mode="json") == {"start":"2026-10-27T07:00:00Z", "end":"2026-10-27T09:00:00Z"}
    assert warnings == []
    assert expand([weekly], Horizon(start="2026-11-02T00:00-08:00", end="2026-11-03T00:00-08:00"))[0] == []


@pytest.mark.parametrize("changes", [{"timezone":"Invalid/Zone"}, {"weekday":7}, {"weekday":True},
    {"start_local":"18:00", "end_local":"13:00"}, {"start_local":"13:00+01:00"},
    {"valid_from":"2026-11-02", "valid_until":"2026-10-26"}])
def test_invalid_weekly_rule(changes):
    values = dict(id="r", owner_id="s", weekday=0, start_local="13:00", end_local="18:00", timezone="America/Los_Angeles")
    with pytest.raises(ValidationError):
        WeeklyRule.model_validate(values | changes)
