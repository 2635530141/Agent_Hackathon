"""The normal app exposes the manual timetable, with no Google integration."""
from datetime import timedelta
from fastapi.testclient import TestClient
from app import accounts, store
from app.main import app
from app.scheduling.runtime import get_runtime


def sign_in(browser):
    browser.post("/login",data={"email":accounts.TEST_EMAIL,"password":accounts.TEST_PASSWORD})


def test_normal_app_has_no_google_controls_or_routes():
    with TestClient(app) as browser:
        sign_in(browser)
        page=browser.get("/schedule?day=2026-10-26")
        assert page.status_code==200
        assert 'id="calendar-grid"' in page.text and 'id="calendar-create"' in page.text
        for removed in ("Google Calendar", "google-setup", "calendar_connection", "client JSON"):
            assert removed not in page.text
        for path in ("/schedule/google-setup", "/calendar/google/connect", "/calendar/google/callback"):
            assert browser.get(path).status_code==404
        for path in ("/schedule/calendar", "/schedule/calendar/refresh", "/schedule/calendar/disconnect"):
            assert browser.post(path).status_code==404
        assert not any(p.startswith("/calendar/") for p in browser.get("/openapi.json").json()["paths"])


def test_local_availability_stays_usable_after_ttl_and_outside_old_fixture_horizon():
    with TestClient(app) as browser:
        sign_in(browser)
        # Monday in December lies outside the old external-sync fixture window.
        browser.post("/schedule/hours",data={"mon_start":"13:00","mon_end":"18:00"})
        runtime=get_runtime()
        now=runtime.service.clock()
        runtime.service.clock=lambda:now+timedelta(days=1)
        page=browser.get("/schedule?day=2026-12-14")
        assert page.status_code==200 and "Availability needs recalculating" not in page.text
        result=browser.get("/api/schedule/availability",params={
            "starts_at":"2026-12-14T13:00","ends_at":"2026-12-14T18:00"}).json()
        assert len(result["available"])==1
        assert runtime.repository.read_bundle(store.OWNER_ID)["state"]["provider_kind"]=="fake"
