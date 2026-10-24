"""Start an explicitly synthetic schedule API. No real OAuth or calendar writes."""
from __future__ import annotations

import argparse
import os
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import Query
from fastapi.responses import HTMLResponse

from .api import create_app
from .domain import Interval
from .engine import compute_availability, intersect_availability, owner_conflicts
from .serialization import snapshot, interval_json
from .omnara_routes import mount_omnara_demo


def demo_app(database_url="sqlite:///./schedule-demo.db", reference=None):
    current = reference or datetime.now(timezone.utc)
    zone = ZoneInfo("America/Los_Angeles")
    today = current.astimezone(zone).date()
    monday = today + timedelta(days=(7 - today.weekday()) % 7)
    if monday == today and current.astimezone(zone).hour >= 13:
        monday += timedelta(days=7)
    def at(day, hour, minute=0):
        return datetime.combine(day, time(hour, minute), zone).isoformat()
    app = create_app(database_url, auth_tokens={"demo-alex-local": "demo-alex", "demo-bella-local": "demo-bella"},
                     service_token="demo-matching-local", signing_key="demo-local-only-not-for-deployment",
                     clock=(lambda: reference) if reference else None)
    # Demo must stay synthetic even if a parent shell has real Google settings.
    app.state.calendar = None
    app.state.vault = None
    seed = {
        "demo-alex": {"timezone": zone.key, "weekly": [{"weekday": 0, "start": "14:00:00", "end": "18:00:00"}],
            "additions": [], "removals": [], "exams": [{"id": "demo-exam", "course_id": "STAT153",
                "title": "Demo STAT153 exam", "start": at(monday, 17), "end": at(monday, 18), "priority_days": 7}],
            "calendar_busy": [{"id": "demo-private-block", "title": "Demo private appointment",
                               "start": at(monday, 14), "end": at(monday, 14, 30)}],
            "calendar_connected": False, "user_version": 1},
        "demo-bella": {"timezone": zone.key, "weekly": [{"weekday": 0, "start": "14:00:00", "end": "17:00:00"}],
            "additions": [], "removals": [{"start": at(monday, 15, 30), "end": at(monday, 16)}],
            "exams": [], "calendar_busy": [], "calendar_connected": False, "user_version": 1},
    }
    for user_id, payload in seed.items():
        try:
            app.state.store.schedule(user_id)
        except KeyError:
            app.state.store.save_schedule(user_id, payload, 0)

    @app.get("/demo-info")
    def info():
        return {"synthetic": True, "purpose": "Schedule module only; no real students or calendar connection",
                "reference_time": current.isoformat(), "monday": monday.isoformat(),
                "sample_window": {"start": at(monday, 14), "end": at(monday, 18)},
                "sample_auth": "Bearer demo-alex-local", "matching_header": "demo-matching-local",
                "expected_common_windows": [{"start": at(monday, 14, 30), "end": at(monday, 15, 30)},
                                             {"start": at(monday, 16), "end": at(monday, 17)}]}

    @app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
    def viewer_page():
        return HTMLResponse(Path(__file__).with_name("demo_view.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})

    @app.get("/demo/view-data")
    def viewer_data(viewer: Literal["alex", "bella"] = "alex",
                    duration: int = Query(default=60, ge=30, le=90, multiple_of=30),
                    scenario: Literal["normal", "new_conflict"] = "normal"):
        window = Interval(datetime.fromisoformat(at(monday, 14)), datetime.fromisoformat(at(monday, 18)))
        records = []
        for user_id in ("demo-alex", "demo-bella"):
            payload, version = app.state.store.schedule(user_id)
            records.append(snapshot(user_id, payload, version))
        if scenario == "new_conflict":
            # A temporary simulation; the saved records are not modified.
            records[1] = replace(records[1], removals=(*records[1].removals,
                                  Interval(datetime.fromisoformat(at(monday, 14, 30)),
                                           datetime.fromisoformat(at(monday, 15, 30)))))
        self_id = "demo-" + viewer
        people = []
        for record in records:
            person = {"id": record.owner_id, "name": "Alex" if record.owner_id == "demo-alex" else "Bella",
                      "is_self": record.owner_id == self_id,
                      "available": [interval_json(x) for x in compute_availability(record, window)]}
            if person["is_self"]:
                person["own_conflicts"] = [dict(source=x.source, title=x.title, **interval_json(x.interval))
                                            for x in owner_conflicts(record, window)]
            people.append(person)
        common = intersect_availability(records, window, duration)
        return {"synthetic": True, "date": monday.isoformat(), "timezone": zone.key,
                "duration_minutes": duration, "scenario": scenario, "window": interval_json(window),
                "people": people, "common": [interval_json(x) for x in common],
                "google_connected": False}
    mount_omnara_demo(app, viewer_data)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--database", default="sqlite:///./schedule-demo.db")
    parser.add_argument("--reference", help="Optional fixed aware ISO time for repeatable synthetic replay")
    args = parser.parse_args()
    reference = datetime.fromisoformat(args.reference) if args.reference else None
    if reference is not None and (reference.tzinfo is None or reference.utcoffset() is None):
        parser.error("--reference must include an offset")
    uvicorn.run(demo_app(args.database, reference), host="127.0.0.1", port=args.port, access_log=False)
