from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from schedule_service.demo import demo_app


@pytest.fixture
def client():
    return TestClient(demo_app("sqlite://", datetime.fromisoformat("2026-10-24T13:00:00-07:00")))


def test_viewer_serves_without_external_assets(client):
    response = client.get("/demo")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "全部为模拟数据" in response.text
    assert "https://" not in response.text


def test_live_engine_returns_expected_common_windows(client):
    result = client.get("/demo/view-data").raise_for_status().json()
    assert result["synthetic"] and not result["google_connected"]
    assert [slot["start"] for slot in result["common"]] == [
        "2026-10-26T21:30:00+00:00", "2026-10-26T23:00:00+00:00"]


def test_switching_viewer_hides_other_persons_event_titles(client):
    alex = client.get("/demo/view-data?viewer=alex").raise_for_status()
    assert "Demo private appointment" in alex.text
    bella = client.get("/demo/view-data?viewer=bella").raise_for_status()
    assert "Demo private appointment" not in bella.text
    other = next(p for p in bella.json()["people"] if p["id"] == "demo-alex")
    assert "own_conflicts" not in other


def test_scenario_is_temporary_and_recomputes_without_changing_saved_schedule(client):
    modified = client.get("/demo/view-data?scenario=new_conflict").raise_for_status().json()
    assert len(modified["common"]) == 1
    assert modified["common"][0]["start"] == "2026-10-26T23:00:00+00:00"
    assert len(client.get("/demo/view-data").json()["common"]) == 2


def test_duration_and_viewer_are_validated(client):
    assert client.get("/demo/view-data?duration=30").status_code == 200
    assert client.get("/demo/view-data?duration=60").status_code == 200
    assert client.get("/demo/view-data?duration=90").json()["common"] == []
    assert client.get("/demo/view-data?viewer=real-user-id").status_code == 422
    assert client.get("/demo/view-data?duration=999").status_code == 422
