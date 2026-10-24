from datetime import datetime, time

from .domain import BusyBlock, Exam, Interval, ScheduleSnapshot, WeeklySlot


def interval(value: dict) -> Interval:
    return Interval(datetime.fromisoformat(value["start"]), datetime.fromisoformat(value["end"]))


def snapshot(owner_id: str, payload: dict, version: int) -> ScheduleSnapshot:
    return ScheduleSnapshot(
        owner_id=owner_id, timezone=payload["timezone"],
        weekly=tuple(WeeklySlot(x["weekday"], time.fromisoformat(x["start"]),
                                time.fromisoformat(x["end"])) for x in payload.get("weekly", [])),
        additions=tuple(interval(x) for x in payload.get("additions", [])),
        removals=tuple(interval(x) for x in payload.get("removals", [])),
        exams=tuple(Exam(x["id"], x["course_id"], interval(x), x.get("priority_days", 7),
                         x.get("title", "")) for x in payload.get("exams", [])),
        calendar_busy=tuple(BusyBlock(x["id"], interval(x), "calendar", x.get("title", "Busy"))
                            for x in payload.get("calendar_busy", [])),
        calendar_connected=payload.get("calendar_connected", False),
        calendar_refreshed_at=(datetime.fromisoformat(payload["calendar_refreshed_at"])
                               if payload.get("calendar_refreshed_at") else None),
        version=version,
    )


def interval_json(value: Interval) -> dict:
    return {"start": value.start.isoformat(), "end": value.end.isoformat()}
