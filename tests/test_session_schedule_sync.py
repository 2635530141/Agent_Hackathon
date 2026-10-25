from urllib.parse import unquote
import pytest
from fastapi.testclient import TestClient
from app import accounts, store
from app.main import app
from app.scheduling.adapters import TeamSessionGateway
from app.scheduling.core.schemas import Actor
from app.scheduling.demo import FIXTURE_HORIZON
from app.scheduling.runtime import get_runtime
from app.scheduling.time import horizon

DAY="2026-10-26"
OWNER=store.OWNER_ID
GUEST="u_jordan"
OTHER="u_sam"


def seat(subject):
    return {"id":subject,"first_name":"Synthetic","major":"CS","year":2}


def room(host=OWNER,start=13,end=14):
    return store.create_room(seat(host),"math54","Math 54",f"{DAY}T{start:02d}:00",
        f"{DAY}T{end:02d}:00","coffee","Synthetic cafe",4)


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setattr(store,"_rooms",[])
    runtime=get_runtime()
    for subject in (OWNER,GUEST,OTHER):
        for kind in ("exams","exceptions"):
            for item in runtime.repository.list_records(subject,kind):
                runtime.repository.delete_record(subject,kind,item.id)
        runtime.service.provider.events[subject]=[]
        runtime.service.refresh_calendar(Actor(subject_id=subject),FIXTURE_HORIZON)
    with TestClient(app) as client:
        client.post("/login",data={"email":accounts.TEST_EMAIL,"password":accounts.TEST_PASSWORD})
        yield client


def sessions(browser):
    return [item for item in browser.get(f"/api/schedule/week?day={DAY}").json()["entries"]
            if item["kind"]=="session"]


def test_join_automatically_shows_room_and_leave_removes_it(browser):
    target=room(GUEST)
    assert sessions(browser)==[]
    response=browser.post(f"/session/rooms/{target['id']}/join",follow_redirects=False)
    assert "joined" in unquote(response.headers["location"]).lower()
    entries=sessions(browser)
    assert len(entries)==1 and entries[0]["id"]==target["id"]
    assert entries[0]["starts_at"]==DAY+"T13:00" and entries[0]["editable"] is False
    assert entries[0]["session_url"]=="/session?open="+target["id"]
    assert "Math 54" in entries[0]["label"]
    free=browser.get("/api/schedule/availability",params={"starts_at":DAY+"T13:00","ends_at":DAY+"T14:00"}).json()
    assert free["available"]==[]
    browser.post(f"/session/rooms/{target['id']}/leave",follow_redirects=False)
    assert sessions(browser)==[]


def test_host_create_and_repeated_join_are_not_self_conflicts(browser):
    response=browser.post("/session/rooms",data={"course_id":"math54","starts_at":DAY+"T13:00",
        "ends_at":DAY+"T14:00","place_type":"coffee","group_size":"3"},follow_redirects=False)
    assert "host" in unquote(response.headers["location"]).lower()
    target=store.rooms()[0]
    for _ in range(2):
        browser.post(f"/session/rooms/{target['id']}/join",follow_redirects=False)
    assert len(sessions(browser))==1


def test_host_time_change_updates_all_members_and_frees_old_time(browser):
    target=room();store.join_room(target["id"],seat(GUEST))
    response=browser.post(f"/session/rooms/{target['id']}/time",data={"course_id":"math54",
        "starts_at":DAY+"T13:00","ends_at":DAY+"T14:00","place_type":"coffee",
        "room_day":DAY,"room_start":"13:30","room_end":"14:30"},follow_redirects=False)
    assert "Time updated" in unquote(response.headers["location"])
    assert sessions(browser)[0]["starts_at"]==DAY+"T13:30"
    blocks=TeamSessionGateway().confirmed_reservations(GUEST,horizon(DAY+"T00:00",DAY+"T23:59"))
    assert len(blocks)==1 and blocks[0].start==horizon(DAY+"T13:30",DAY+"T14:30").start
    old=browser.get("/api/schedule/availability",params={"starts_at":DAY+"T13:00","ends_at":DAY+"T13:30"}).json()
    assert len(old["available"])==1


def test_other_members_other_room_still_blocks_time_change(browser):
    target=room();store.join_room(target["id"],seat(GUEST))
    busy=room(OTHER,15,16);store.join_room(busy["id"],seat(GUEST))
    response=browser.post(f"/session/rooms/{target['id']}/time",data={"course_id":"math54",
        "starts_at":DAY+"T13:00","ends_at":DAY+"T14:00","place_type":"coffee",
        "room_day":DAY,"room_start":"15:00","room_end":"16:00"},follow_redirects=False)
    assert "busy" in unquote(response.headers["location"])
    assert sessions(browser)[0]["starts_at"]==DAY+"T13:00"


def test_all_joined_rooms_are_projected_and_closed_room_disappears(browser):
    one=room(OWNER,13,14);two=room(OWNER,15,16)
    assert {entry["id"] for entry in sessions(browser)}=={one["id"],two["id"]}
    store.leave_room(one["id"],OWNER)
    assert [entry["id"] for entry in sessions(browser)]==[two["id"]]
    assert TeamSessionGateway().confirmed_reservations(OTHER,horizon(DAY+"T00:00",DAY+"T23:59"))==[]


def test_matching_reservations_contain_no_room_display_data(browser):
    room()
    value=TeamSessionGateway().confirmed_reservations(OWNER,horizon(DAY+"T00:00",DAY+"T23:59"))[0].model_dump()
    assert set(value)=={"session_id","start","end"}
