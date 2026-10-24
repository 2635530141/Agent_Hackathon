import json
import re
from fastapi.testclient import TestClient
from app import accounts, schedule_core, matching_core, store
from app.main import app
from app.scheduling.runtime import get_runtime
from app.scheduling.time import parse_instant


def login(client):
    client.post("/login",data={"email":accounts.TEST_EMAIL,"password":accounts.TEST_PASSWORD})
    html=client.get("/schedule").text
    data=json.loads(re.search(r'<script id="calendar-week-data" type="application/json">(.*?)</script>',html,re.S)[1])
    return {"X-CSRF-Token":data["csrf"]}


def test_timetable_edit_uses_same_persistent_data_as_matching():
    with TestClient(app) as client:
        headers=login(client)
        base={"starts_at":"2026-10-26T13:00","ends_at":"2026-10-26T18:00","kind":"weekly"}
        assert client.post("/api/schedule/entry",json=base,headers=headers).status_code==200
        exam={**base,"kind":"exam","course_id":"math54","starts_at":"2026-10-26T16:00","ends_at":"2026-10-26T17:00"}
        assert client.post("/api/schedule/entry",json=exam,headers=headers).status_code==200
        week=client.get("/api/schedule/week?day=2026-10-29").json()
        assert week["start"]=="2026-10-26" and len(week["days"])==7
        entry=next(e for e in week["entries"] if e["kind"]=="exam" and e["starts_at"]==exam["starts_at"])
        exam["id"]=entry["id"];exam["ends_at"]="2026-10-26T17:30"
        assert client.post("/api/schedule/entry",json=exam,headers=headers).status_code==200
        me=matching_core.load_inputs().me
        span=(parse_instant("2026-10-26T16:00"),parse_instant("2026-10-26T17:30"))
        assert matching_core.free_time(me,*span)==[]
        runtime=get_runtime();runtime.repository.close();get_runtime.cache_clear()
        restarted=client.get("/api/schedule/week?day=2026-10-26").json()
        assert any(e["id"]==entry["id"] and e["ends_at"]=="2026-10-26T17:30" for e in restarted["entries"])
        assert client.delete("/api/schedule/entry/exam/"+entry["id"],headers=headers).status_code==200
        assert all(e.id!=entry["id"] for e in schedule_core.list_exams())


def test_editor_csrf_invalid_dst_and_forged_owner_fail_closed():
    with TestClient(app) as client:
        headers=login(client)
        body={"kind":"free","starts_at":"2026-11-01T01:30","ends_at":"2026-11-01T03:00"}
        assert client.post("/api/schedule/entry",json=body).status_code==403
        assert client.post("/api/schedule/entry",json=body,headers=headers).status_code==422
        body.update(starts_at="2026-10-26T13:00",ends_at="2026-10-26T14:00")
        assert client.post("/api/schedule/entry",json={**body,"owner_id":"other"},headers=headers).status_code==422
        assert client.post("/api/schedule/entry",json=body,headers={**headers,"Origin":"https://wrong.invalid"}).status_code==403
        assert client.get("/api/schedule/week?day=2026-10-26&owner_id=other").status_code==403


def test_extra_free_and_weekly_delete_preserve_other_weekdays():
    with TestClient(app) as client:
        headers=login(client);before=schedule_core.weekly_hours()["thu"]
        body={"kind":"free","starts_at":"2026-10-26T19:00","ends_at":"2026-10-26T20:00"}
        assert client.post("/api/schedule/entry",json=body,headers=headers).status_code==200
        snap=schedule_core.effective_availability(store.OWNER_ID,body["starts_at"],body["ends_at"])
        assert len(snap.available)==1
        assert client.delete("/api/schedule/entry/weekly/0",headers=headers).status_code==200
        assert schedule_core.weekly_hours()["mon"]==("","") and schedule_core.weekly_hours()["thu"]==before


def test_week_dst_boundary_is_disclosed_and_private_projection_is_owner_only():
    with TestClient(app) as client:
        login(client)
        week=client.get("/api/schedule/week?day=2026-11-01").json()
        assert week["timezone"]=="America/Los_Angeles" and week["days"][-1]["dst"] is True
        for e in week["entries"]:
            assert 0<=e["top"]<=100 and e["height"]>0
        assert client.post("/logout").status_code==200
        assert client.get("/api/schedule/week?day=2026-11-01",follow_redirects=False).status_code==303


def test_native_timetable_form_csrf_save_and_explicit_remove():
    with TestClient(app) as client:
        headers=login(client)
        body={"entry_kind":"busy","starts_at":"2026-10-28T10:00","ends_at":"2026-10-28T11:30",
            "csrf_token":headers["X-CSRF-Token"],"operation":"save"}
        assert client.post("/schedule/timetable",data={**body,"csrf_token":""}).status_code==403
        response=client.post("/schedule/timetable",data=body)
        assert response.status_code==200 and "Saved." in response.text
        item=next(e for e in get_runtime().repository.list_records(store.OWNER_ID,"exceptions")
            if e.kind=="unavailable" and e.start==parse_instant(body["starts_at"]))
        response=client.post("/schedule/timetable",data={**body,"record_id":item.id,"operation":"delete"})
        assert response.status_code==200
        assert all(e.id!=item.id for e in get_runtime().repository.list_records(store.OWNER_ID,"exceptions"))


def test_existing_session_start_and_send_invoke_refresh_hook(monkeypatch):
    from app.routers import session
    calls=[]
    monkeypatch.setattr(session,"refresh_before_request",lambda request:calls.append("refresh"))
    monkeypatch.setattr(session,"interpret_request",lambda sentence,courses:{"course_id":"cs61a",
        "starts_at":"2026-10-29T19:00","ends_at":"2026-10-29T20:00","place_type":"coffee"})
    with TestClient(app) as client:
        login(client)
        assert client.post("/session/interpret",data={"sentence":"Synthetic request"},follow_redirects=False).status_code==303
        assert client.post("/session/confirm",data={"course_id":"cs61a","starts_at":"2026-10-29T19:00",
            "ends_at":"2026-10-29T20:00","place_type":"coffee"},follow_redirects=False).status_code==303
    assert calls==["refresh","refresh"]


def test_synthetic_refresh_retains_selected_day_and_rejects_non_date_redirect():
    with TestClient(app) as client:
        login(client)
        page = client.get("/schedule?day=2026-10-27")
        assert 'name="day" value="2026-10-27"' in page.text
        response = client.post("/schedule/calendar/refresh",data={"day":"2026-10-27"},follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/schedule?day=2026-10-27&saved=synthetic-refresh"
        invalid = client.post("/schedule/calendar/refresh",data={"day":"https://evil.example"},follow_redirects=False)
        assert invalid.headers["location"] == "/schedule?saved=synthetic-refresh"
