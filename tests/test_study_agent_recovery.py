"""Changed evidence and interrupted attempts cannot preserve an approval."""
from uuid import uuid4

import pytest

from app import store
from app.scheduling.runtime import get_runtime
from app.scheduling.core.schemas import ExceptionInput
from app.study_agent.service import AgentService
from app.study_agent.tools import StudyTools

GOAL = {"course_id":"cs61a","window_start":"2026-10-29T18:00","window_end":"2026-10-29T21:00",
        "duration_minutes":60,"place_type":"library"}


def act(service, run, action, patch=None):
    return service.act(run["id"],store.OWNER_ID,run["version"],uuid4().hex,action,patch)


def test_three_clarifications_do_not_exhaust_conflict_replanning():
    service = AgentService()
    run = act(service, service.create(store.OWNER_ID,"Study"),"retry")
    run = act(service,run,"answer",{"course_id":"cs61a"})
    run = act(service,run,"answer",{k:GOAL[k] for k in ("window_start","window_end")})
    run = act(service,run,"answer",{"duration_minutes":60,"place_type":"library"})
    assert run["state"] == "proposed"
    run = act(service,run,"simulate")
    run = act(service,run,"approve")
    assert run["state"] == "proposed" and run["planning_attempts"] == 2


def test_closed_place_invalidates_existing_approval_and_does_not_fallback_type(monkeypatch):
    service = AgentService()
    run = act(service,service.create(store.OWNER_ID,"Study",goal=GOAL),"retry")
    run = act(service,run,"approve")
    monkeypatch.setattr(store,"_places",[{**p,"closes":"18:00"} if p["kind"] == "library" else p for p in store.places()])
    run = act(service,run,"approve")
    assert run["state"] == "no_match" and run["approved_revision"] is None and run["proposal"] is None


def test_unexpected_tool_failure_leaves_visible_recoverable_error(monkeypatch):
    monkeypatch.setattr(StudyTools,"find_matches",lambda *_:(_ for _ in ()).throw(RuntimeError("private database data")))
    service = AgentService()
    run = act(service,service.create(store.OWNER_ID,"Study",goal=GOAL),"retry")
    assert run["state"] == "error" and "private database data" not in str(run)


def test_expired_planning_attempt_can_be_retried_after_restart():
    service = AgentService()
    run = service.create(store.OWNER_ID,"Study",goal=GOAL)
    run.update(state="planning",planning_deadline=0,attempt_id="interrupted")
    service.repository.save(run)
    run = act(service,run,"retry")
    assert run["state"] == "proposed" and run["attempt_id"] != "interrupted"
    assert any(e["kind"] == "recovered" for e in run["events"])


def test_cancel_removes_only_its_own_synthetic_conflict():
    service = AgentService()
    run = act(service,service.create(store.OWNER_ID,"Study",goal=GOAL),"retry")
    run = act(service,run,"simulate")
    block = run["demo_block"]
    assert any(r.id == block["id"] for r in get_runtime().repository.list_records(block["subject"],"exceptions"))
    run = service.cancel(run["id"],store.OWNER_ID)
    assert run["demo_block"] is None
    assert not any(r.id == block["id"] for r in get_runtime().repository.list_records(block["subject"],"exceptions"))


def test_model_cannot_relax_existing_hard_requirements():
    service = AgentService()
    run = service.create(store.OWNER_ID,"Study",goal=GOAL)
    tools = StudyTools(run)
    with pytest.raises(ValueError):
        tools.call("set_requirements",{"requirements":{"duration_minutes":15}})
    tools.call("get_profile",{})
    with pytest.raises(ValueError):
        tools.call("set_requirements",{"requirements":{"place_type":"library"}})


def test_thirty_minute_duration_in_three_hour_window_does_not_require_sixty():
    repository = get_runtime().repository
    repository.create_record("u_jordan","exceptions",ExceptionInput(
        start="2026-10-30T02:30Z",end="2026-10-30T04:00Z",kind="unavailable"))
    repository.create_record("u_sam","exceptions",ExceptionInput(
        start="2026-10-30T02:00Z",end="2026-10-30T04:00Z",kind="unavailable"))
    service = AgentService()
    run = act(service,service.create(store.OWNER_ID,"Study",goal={**GOAL,"duration_minutes":30}),"retry")
    assert run["state"] == "proposed"
    assert run["proposal"]["candidate_ids"] == ["u_jordan"]
    assert run["proposal"]["starts_at"] == "2026-10-30T02:00:00+00:00"
    assert run["proposal"]["ends_at"] == "2026-10-30T02:30:00+00:00"
