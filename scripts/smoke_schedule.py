"""Exercise the running synthetic schedule API without any external provider."""
from datetime import datetime, timedelta
import json
import sys

import httpx


def main(base="http://127.0.0.1:8013"):
    owner = {"Authorization": "Bearer demo-alex-local"}
    peer = {"Authorization": "Bearer demo-bella-local"}
    internal = {"X-Schedule-Service-Token": "demo-matching-local"}
    with httpx.Client(base_url=base, timeout=10) as client:
        info = client.get("/demo-info").raise_for_status().json()
        assert info["synthetic"] is True
        health = client.get("/health").raise_for_status().json()
        assert not health["google_configured"]
        assert client.get("/schedule/me").status_code == 401
        own = client.get("/schedule/me", headers=owner).raise_for_status().json()
        assert own["calendar_busy"][0]["title"] == "Demo private appointment"
        body = {**info["sample_window"], "participant_ids": ["demo-alex", "demo-bella"], "duration_minutes": 60}
        shared = client.post("/internal/availability", headers=internal, json=body).raise_for_status().json()
        actual = [(datetime.fromisoformat(x["start"]), datetime.fromisoformat(x["end"])) for x in shared["slots"]]
        expected = [(datetime.fromisoformat(x["start"]), datetime.fromisoformat(x["end"])) for x in info["expected_common_windows"]]
        assert actual == expected
        assert "private" not in json.dumps(shared).lower() and "title" not in json.dumps(shared)
        start = datetime.fromisoformat(info["sample_window"]["start"])
        end = start + timedelta(hours=1)
        exact = {"start": start.isoformat(), "end": end.isoformat()}
        override = client.post("/schedule/me/check", headers=owner,
            json={**exact, "request_id": "demo-smoke", "study_anyway": True}).raise_for_status().json()
        assert override["allowed"] and override["override_token"]
        permitted = client.post("/internal/availability", headers=internal,
            json={**exact, "participant_ids": ["demo-alex", "demo-bella"], "organizer_id": "demo-alex",
                  "request_id": "demo-smoke", "owner_override_token": override["override_token"]}).raise_for_status().json()
        assert permitted["slots"] and permitted["organizer_override_applied"]
        accepted = dict(id="demo-smoke-session", organizer_id="demo-alex", participant_ids=["demo-alex", "demo-bella"],
                        accepted_ids=["demo-alex", "demo-bella"], subject="Synthetic smoke test", place="Demo place",
                        request_id="demo-smoke-free", version=1, **info["expected_common_windows"][0])
        client.put("/internal/confirmed-sessions/demo-smoke-session", headers=internal, json=accepted).raise_for_status()
        preview = client.get("/sessions/demo-smoke-session/calendar-preview", headers=owner).raise_for_status().json()
        assert client.get("/sessions/demo-smoke-session/calendar-preview", headers=peer).status_code == 404
        no_provider = client.post("/sessions/demo-smoke-session/calendar", headers=owner,
                                 json={"confirmed": True, "confirmation_token": preview["confirmation_token"]})
        assert no_provider.status_code == 503
        result = {"status": "PASS", "mode": "synthetic local HTTP smoke", "storage": health["storage"],
                  "monday": info["monday"], "shared_continuous_windows": shared["slots"],
                  "owner_only_titles": True, "single_request_override": True,
                  "organizer_only_preview": True, "unconfigured_calendar_write_blocked": True,
                  "real_google_accessed": False}
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8013")
