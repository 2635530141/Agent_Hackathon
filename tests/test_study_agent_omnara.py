"""HTTP contract replays; these tests do not prove live Omnara/model access."""
import json

import httpx
import pytest

from app import store
from app.study_agent.planner import OmnaraPlanner, PlannerError, settings
from app.study_agent.service import AgentService
from app.study_agent.tools import group_options

VALUES = {"OMNARA_TOKEN":"fixture-token", "OMNARA_ORG":"org_fixture", "OMNARA_PROJECT":"proj_fixture",
          "OMNARA_MODEL_PROVIDER":"configured-provider", "OMNARA_MODEL":"configured-model"}
GOAL = {"course_id":"cs61a","window_start":"2026-10-29T18:00","window_end":"2026-10-29T21:00",
        "duration_minutes":60,"place_type":"library"}


def test_native_custom_tool_replay_uses_real_tools_then_grounded_final():
    state = {"step":0,"outputs":{},"calls":[],"launch":None}
    names = ["get_profile","find_matches","check_availability","suggest_places","validate_proposal"]

    def handler(req):
        path = req.url.path
        if path.endswith("/agents"):
            state["launch"] = json.loads(req.content)
            return httpx.Response(201,json={"agent":{"id":"agt_isolated"},"agent_input":{"id":"input_current"}})
        if path.endswith("/events"):
            events = [{"sequence":1,"event_kind":"agent_input","agent_input_id":"input_current","turn_id":"current"}] if state["step"] == 0 else []
            if state["step"] == 5:
                events += [{"sequence":7,"event_kind":"model_output","turn_id":"current","stop_reason":"end_turn",
                            "content_blocks":[{"type":"reasoning","text":"hidden reasoning must not leak"},
                             {"type":"text","text":json.dumps({"action":"propose","evidence_id":state['outputs']['validate_proposal']['evidence_id']})}]}]
            return httpx.Response(200,json={"data":events,"has_more":False})
        if path.endswith("/tool-calls"):
            step = state["step"]
            args = {}
            if step >= 2:
                ranked = state["outputs"]["find_matches"]
                ids, start = next(group_options(ranked["candidates"], ranked["group_size"],60))
                args.update(candidate_ids=ids, starts_at=start)
            if step == 4:
                args["place_id"] = state["outputs"]["suggest_places"]["places"][0]["id"]
            return httpx.Response(200,json={"data":[{"id":f"call_{step}","turn_id":"current","name":names[step],"input":args}],"next_cursor":None})
        if path.endswith("/result"):
            result = json.loads(req.content)
            assert result["outcome"] == "succeeded"
            name = names[state["step"]]
            state["calls"].append(name)
            state["outputs"][name] = result["content_blocks"][0]["value"]
            state["step"] += 1
            return httpx.Response(201,json={})
        raise AssertionError(path)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = AgentService(planner_factory=lambda _:OmnaraPlanner(VALUES,client,sleep=lambda _:None))
    run = service.create(store.OWNER_ID,"Please find the best feasible option",mode="omnara",goal=GOAL)
    run = service.act(run["id"],store.OWNER_ID,run["version"],"start","retry")
    assert run["state"] == "proposed" and run["cloud_id"] == "agt_isolated"
    assert state["calls"] == names
    config = json.loads(state["launch"]["config_source"]["source"])
    assert "machine_sources" not in config and "subagents" not in config
    assert set(config["tools"]) == {"set_requirements",*names}
    assert "hidden reasoning" not in json.dumps(run)
    assert "fixture-token" not in json.dumps(run)


def test_only_current_turn_final_text_is_read_and_max_tokens_continues():
    calls = []
    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/agents"):
            return httpx.Response(201,json={"agent":{"id":"agt_new"},"agent_input":{"id":"input_new"}})
        if req.url.path.endswith("/events"):
            return httpx.Response(200,json={"data":[
                {"sequence":1,"event_kind":"model_output","turn_id":"old","stop_reason":"end_turn","content_blocks":[{"type":"text","text":"bad"}]},
                {"sequence":2,"event_kind":"agent_input","agent_input_id":"input_new","turn_id":"new"},
                {"sequence":3,"event_kind":"model_output","turn_id":"new","stop_reason":"max_tokens","content_blocks":[{"type":"text","text":"bad partial"}]},
                {"sequence":4,"event_kind":"model_output","turn_id":"new","stop_reason":"end_turn","content_blocks":[{"type":"reasoning","text":"secret"},{"type":"text","text":'{"action":"clarify"}'}]}
            ]})
        raise AssertionError(req.url)
    planner = OmnaraPlanner(VALUES,httpx.Client(transport=httpx.MockTransport(handler)),sleep=lambda _:None)
    run = {"id":"request_1","version":1,"goal":{},"input":"CS 61A","notice":"","events":[],"revision":1,"cursor":0}
    result = planner.run(run,lambda *_:pytest.fail("No tools expected"))
    assert result == {"action":"clarify"} and run["cursor"] == 4


@pytest.mark.parametrize("failure", ["timeout","http","envelope","json"])
def test_remote_failures_are_redacted_and_cancelled(failure):
    paths = []
    def handler(req):
        paths.append(req.url.path)
        if req.url.path.endswith("/cancel"):
            return httpx.Response(200,json={})
        if req.url.path.endswith("/agents"):
            return httpx.Response(201,json={"agent":{"id":"agt_error"},"agent_input":{"id":"input_error"}})
        if failure == "timeout":
            raise httpx.ReadTimeout("secret server credentials",request=req)
        if failure == "http":
            return httpx.Response(500,text="secret credentials")
        if failure == "envelope":
            return httpx.Response(200,json=[])
        return httpx.Response(200,text="not json secret")
    planner = OmnaraPlanner(VALUES,httpx.Client(transport=httpx.MockTransport(handler)))
    run = {"id":"request_2","version":1,"goal":{},"input":"study","notice":"","events":[],"revision":1,"cursor":0}
    with pytest.raises(PlannerError) as exc:
        planner.run(run,lambda *_:None)
    assert "secret" not in str(exc.value) and paths[-1].endswith("/cancel")


def test_polling_has_a_hard_deadline_and_cancels_remote_work():
    now, paths = [0.0], []
    def handler(req):
        paths.append(req.url.path)
        if req.url.path.endswith("/agents"):
            return httpx.Response(201,json={"agent":{"id":"agt_idle"},"agent_input":{"id":"input_idle"}})
        return httpx.Response(200,json={"data":[],"has_more":False})
    planner = OmnaraPlanner(VALUES,httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda:now[0],sleep=lambda seconds:now.__setitem__(0,now[0]+seconds))
    run = {"id":"request_3","version":1,"goal":{},"input":"study","notice":"","events":[],"revision":1,"cursor":0}
    with pytest.raises(PlannerError):
        planner.run(run,lambda *_:None)
    assert len(paths) <= 42 and paths[-1].endswith("/cancel")


def test_missing_omnara_configuration_is_explicit_not_local_fallback(monkeypatch):
    for key in VALUES:
        monkeypatch.delenv(key,raising=False)
    with pytest.raises(PlannerError,match="not configured"):
        settings()


def test_two_requests_get_different_isolated_launches():
    keys = []
    def handler(req):
        if req.url.path.endswith("/agents"):
            keys.append(req.headers["Idempotency-Key"])
            return httpx.Response(201,json={"agent":{"id":f"agt_{len(keys)}"},"agent_input":{"id":"input"}})
        return httpx.Response(200,json={"data":[{"sequence":1,"event_kind":"agent_input","agent_input_id":"input","turn_id":"turn"},
            {"sequence":2,"event_kind":"model_output","turn_id":"turn","stop_reason":"end_turn","content_blocks":[{"type":"text","text":'{"action":"clarify"}'}]}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    ids = []
    for run_id in ("a","b"):
        run = {"id":run_id,"version":1,"goal":{},"input":"study","notice":"","events":[],"revision":1,"cursor":0}
        OmnaraPlanner(VALUES,client).run(run,lambda *_:None)
        ids.append(run["cloud_id"])
    assert ids == ["agt_1","agt_2"] and len(set(keys)) == 2
