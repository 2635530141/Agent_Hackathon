"""Private host contract tests. OAuth and Google HTTP are mocked, never live."""
import json
import re
from datetime import datetime, timezone
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlsplit
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.main import create_app
from app.scheduling.live_host import build_private_host
from app.scheduling.google_config import READ_SCOPE
from app.scheduling.google_storage import GoogleStateError

NOW = datetime(2026,10,24,20,tzinfo=timezone.utc)
CALLBACK = "http://127.0.0.1:8017/calendar/google/callback"


@pytest.fixture
def private_host(tmp_path):
    client_file = tmp_path/"web.json"
    client_file.write_text(json.dumps({"web":{"client_id":"synthetic-oauth-client", "client_secret":token_urlsafe(24),
        "redirect_uris":[CALLBACK]}}))
    (tmp_path/"encryption.key").write_bytes(Fernet.generate_key())
    (tmp_path/"session.key").write_text(token_urlsafe(64))
    settings = dict(database_url=f"sqlite+pysqlite:///{(tmp_path/'private.db').as_posix()}",
        client_file=client_file, encryption_key_file=tmp_path/"encryption.key",
        session_secret_file=tmp_path/"session.key", redirect_uri=CALLBACK)
    host = build_private_host(**settings)
    host.active.oauth.clock = lambda:NOW
    host.active.provider.clock = lambda:NOW
    host.active.service.clock = lambda:NOW
    def google(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200,json={"access_token":"synthetic-access", "refresh_token":"synthetic-refresh",
                "token_type":"Bearer", "expires_at":NOW.timestamp()+3600, "scope":READ_SCOPE})
        assert request.method == "GET" and "/calendars/primary/events" in request.url.path
        return httpx.Response(200,json={"timeZone":"America/Los_Angeles", "items":[{
            "id":"synthetic-private-event", "summary":"SYNTHETIC_OWNER_TITLE", "status":"confirmed",
            "start":{"dateTime":"2026-10-26T14:00:00-07:00"}, "end":{"dateTime":"2026-10-26T15:00:00-07:00"}}]})
    transport = httpx.MockTransport(google)
    host.active.oauth.transport = transport
    host.active.provider.transport = transport
    yield host, settings
    host.repository.close()


def client(host):
    return TestClient(create_app(calendar_host=host),base_url="http://127.0.0.1:8017",follow_redirects=False)


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"',response.text)[1]


def signup(browser, suffix="one"):
    token = csrf(browser.get("/register"))
    result = browser.post("/register",data={"name":"Private Tester", "email":f"{suffix}@example.test",
        "password":"synthetic-password-only", "csrf_token":token})
    assert result.status_code == 303
    page = browser.get("/schedule?day=2026-10-26")
    assert page.status_code == 200
    return csrf(page)


def authorize(browser, token):
    response = browser.post("/schedule/calendar",data={"csrf_token":token})
    assert response.status_code == 303
    begin = browser.get(response.headers["location"])
    query = parse_qs(urlsplit(begin.headers["location"]).query)
    assert query["scope"] == [READ_SCOPE]
    assert "client_secret" not in query and "code_challenge" in query
    callback = browser.get("/calendar/google/callback",params={"code":"synthetic-code", "state":query["state"][0]})
    assert callback.status_code == 303
    return query["state"][0]


def test_private_oauth_sync_core_example_privacy_and_disconnect(private_host):
    host,_ = private_host
    with client(host) as browser:
        token = signup(browser)
        assert "Test account" not in browser.get("/login").text
        entry = lambda body:browser.post("/api/schedule/entry",headers={"x-csrf-token":token},json=body)
        assert entry({"kind":"weekly", "starts_at":"2026-10-26T13:00", "ends_at":"2026-10-26T18:00"}).status_code == 200
        assert entry({"kind":"exam", "course_id":"CS 61A", "starts_at":"2026-10-26T16:00", "ends_at":"2026-10-26T17:00",
            "priority_starts_at":"2026-10-25T09:00"}).status_code == 200
        state = authorize(browser,token)
        assert browser.get("/calendar/google/callback",params={"code":"again", "state":state}).status_code == 400
        page = browser.get("/schedule?day=2026-10-26")
        assert "SYNTHETIC_OWNER_TITLE" in page.text and "google_current" in page.text
        snapshot = browser.get("/api/schedule/availability",params={"starts_at":"2026-10-26T13:00", "ends_at":"2026-10-26T18:00"})
        assert snapshot.status_code == 200
        intervals = snapshot.json()["available"]
        assert [(datetime.fromisoformat(i["start"]).hour, datetime.fromisoformat(i["end"]).hour) for i in intervals] == [(20,21),(22,23),(0,1)]
        assert "SYNTHETIC_OWNER_TITLE" not in snapshot.text and "token" not in snapshot.text and "@" not in snapshot.text
        match = browser.get("/api/schedule/busy/match")
        assert "SYNTHETIC_OWNER_TITLE" not in match.text
        check = browser.post("/schedule/check",data={"csrf_token":token,"starts_at":"2026-10-26T14:00", "ends_at":"2026-10-26T15:00"})
        assert check.status_code == 200 and "Alternative: 2026-10-26T15:00" in check.text
        assert browser.post("/schedule/calendar/disconnect",data={"csrf_token":token}).status_code == 303
        after = browser.get("/schedule?day=2026-10-26")
        assert "SYNTHETIC_OWNER_TITLE" not in after.text and "manual_only" in after.text
        assert "CS61A exam" in after.text


def test_private_two_users_csrf_state_and_timetable_isolation(private_host):
    host,_ = private_host
    with client(host) as one, client(host) as two:
        a,b = signup(one,"one"),signup(two,"two")
        assert one.post("/schedule/hours",data={"mon_start":"13:00", "mon_end":"18:00"}).status_code == 403
        assert one.post("/api/schedule/entry",headers={"x-csrf-token":b},json={"kind":"free","starts_at":"2026-10-26T13:00","ends_at":"2026-10-26T14:00"}).status_code == 403
        saved = one.post("/schedule/timetable",data={"csrf_token":a,"entry_kind":"free","starts_at":"2026-10-26T13:00","ends_at":"2026-10-26T14:00"})
        assert saved.status_code == 303
        assert any(e["kind"]=="free" for e in one.get("/api/schedule/week?day=2026-10-26").json()["entries"])
        assert two.get("/api/schedule/week?day=2026-10-26").json()["entries"] == []
        assert two.get("/api/schedule/week?day=2026-10-26&owner_id=u_maya").status_code == 403
        begin = one.get("/calendar/google/connect")
        state = parse_qs(urlsplit(begin.headers["location"]).query)["state"][0]
        assert two.get("/calendar/google/callback",params={"state":state,"code":"synthetic"}).status_code == 400
        assert one.get("/calendar/google/callback",params={"state":state,"code":"synthetic"}).status_code == 303
        assert "SYNTHETIC_OWNER_TITLE" not in two.get("/schedule").text
        for path in ("/matching","/session","/api/profile/me","/calendar/export"):
            assert one.get(path).status_code == 409
        assert one.post("/schedule/calendar/refresh",data={"csrf_token":a},headers={"origin":"https://attacker.invalid"}).status_code == 403


def test_accounts_and_rules_survive_rebuild_without_demo_seed(private_host):
    host,settings = private_host
    with client(host) as browser:
        token = signup(browser)
        assert browser.post("/schedule/hours",data={"csrf_token":token,"mon_start":"13:00","mon_end":"18:00"}).status_code == 303
        authorize(browser,token)
    rebuilt = build_private_host(**settings)
    try:
        with client(rebuilt) as browser:
            token = csrf(browser.get("/login"))
            assert browser.post("/login",data={"csrf_token":token,"email":"one@example.test","password":"synthetic-password-only"}).status_code == 303
            assert "13:00" in browser.get("/schedule?day=2026-10-26").text
            account = rebuilt.accounts.authenticate("one@example.test","synthetic-password-only")
            assert rebuilt.active.vault.load(account["id"])[0]["refresh_token"] == "synthetic-refresh"
            assert rebuilt.accounts.authenticate("maya@example.edu","studymate") is None
    finally:
        rebuilt.repository.close()


def test_private_failure_keeps_calendar_unknown_and_cache(private_host):
    host,_ = private_host
    with client(host) as browser:
        token = signup(browser)
        authorize(browser,token)
        host.active.provider.transport = httpx.MockTransport(lambda request:httpx.Response(403,json={"error":"synthetic failure"}))
        result = browser.post("/schedule/calendar/refresh",data={"csrf_token":token,"day":"2026-10-26"})
        assert result.status_code == 303
        page = browser.get("/schedule?day=2026-10-26")
        assert "SYNTHETIC_OWNER_TITLE" in page.text and "unknown" in page.text
        assert browser.get("/api/schedule/availability?starts_at=2026-10-26T13:00&ends_at=2026-10-26T18:00").json()["available"] == []


def test_private_host_rejects_wrong_host_and_forged_demo_cookie(private_host):
    host,_ = private_host
    with TestClient(create_app(calendar_host=host)) as browser:
        assert browser.get("/").status_code == 403
    with client(host) as browser:
        assert browser.post("/register",data={"name":"Test User","email":"fake@example.test","password":"test-password-only"}).status_code == 403
        assert browser.get("/schedule").status_code == 303


@pytest.mark.parametrize("operation", ["add", "edit", "delete", "bulk"])
def test_weekly_edits_after_timezone_switch_preserve_other_days(private_host, operation):
    host, settings = private_host
    with client(host) as browser:
        token = signup(browser)
        headers = {"x-csrf-token": token}
        for day, start, end in [("2026-10-26", "13:00", "18:00"), ("2026-10-27", "09:00", "12:00")]:
            assert browser.post("/api/schedule/entry", headers=headers, json={
                "kind":"weekly", "starts_at":f"{day}T{start}", "ends_at":f"{day}T{end}",
            }).status_code == 200
        account = host.accounts.authenticate("one@example.test", "synthetic-password-only")
        monday = next(r for r in host.repository.list_records(account["id"], "weekly-rules") if r.weekday == 0)
        before = monday.model_dump()
        assert before["timezone"] == "America/Los_Angeles"
        assert browser.post("/schedule/timezone", data={"timezone":"America/New_York", "csrf_token":token}).status_code == 303
        if operation == "delete":
            response = browser.delete("/api/schedule/entry/weekly/1", headers=headers)
        elif operation == "bulk":
            response = browser.post("/schedule/hours", data={"csrf_token":token,
                "mon_start":"13:00", "mon_end":"18:00", "tue_start":"10:00", "tue_end":"12:00"})
        else:
            response = browser.post("/api/schedule/entry", headers=headers, json={
                "kind":"weekly", "id":"1" if operation == "edit" else None,
                "starts_at":"2026-10-27T10:00", "ends_at":"2026-10-27T12:00",
            })
        assert response.status_code in {200, 303}
        rules = host.repository.list_records(account["id"], "weekly-rules")
        assert next(r for r in rules if r.weekday == 0).model_dump() == before
        if operation == "delete":
            assert all(r.weekday != 1 for r in rules)
        else:
            assert next(r for r in rules if r.weekday == 1).timezone == "America/New_York"
        week = browser.get("/api/schedule/week?day=2026-10-26").json()
        # Same real interval: 13:00 LA remains 16:00 New York after editing Tuesday.
        assert any(e["kind"] == "weekly" and e["day"] == 0 and e["starts_at"] == "2026-10-26T16:00"
                   and e["ends_at"] == "2026-10-26T21:00" for e in week["entries"])
    rebuilt = build_private_host(**settings)
    try:
        assert next(r for r in rebuilt.repository.list_records(account["id"], "weekly-rules") if r.weekday == 0).model_dump() == before
    finally:
        rebuilt.repository.close()
