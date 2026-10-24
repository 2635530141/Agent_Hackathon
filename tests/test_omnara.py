import json
from datetime import datetime
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from schedule_service.demo import demo_app
from schedule_service.omnara import (
    OmnaraClient, OmnaraConnection, OmnaraError, StudyAdviceRunner, agent_config, official_auth_url,
)

IDENTITY = {"user": {"id": "user", "display_name": "Demo operator"}, "orgs": [{"id": "org", "name": "Demo org"}]}
MODEL = {"id": "model", "name": "granted-model", "provider_config": "provider"}
RESULTS = {
    "get_profile": {"id": "alex", "synthetic": True},
    "find_matches": {"candidates": [{"id": "bella", "name": "Bella", "slots": [{"start": "2026-10-26T21:30:00Z", "end": "2026-10-26T22:30:00Z"}]}]},
    "suggest_places": {"places": [{"id": "library", "name": "Demo library", "synthetic": True}]},
    "check_booking": {"supported": False},
}
PROPOSAL = {"candidate_id": "bella", "slot_index": 0, "place_id": "library",
            "reason_codes": ["shared_course", "complete_overlap"], "invite_draft": "Would you like to study?"}


def test_device_flow_is_browser_bound_poll_limited_and_never_returns_credentials(monkeypatch):
    monkeypatch.delenv("OMNARA_TOKEN", raising=False)
    now, requests, replies = [0], [], ["authorization_pending", "slow_down", "connected"]

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("oauth-authorization-server"):
            return httpx.Response(200, json={"device_authorization_endpoint": "https://app.omnara.com/api/auth/device/code",
                                            "token_endpoint": "https://app.omnara.com/api/auth/device/token"})
        if request.url.path.endswith("/code"):
            assert parse_qs(request.content.decode())["client_id"] == ["omnara-cli"]
            return httpx.Response(200, json={"device_code": "private-device-code", "user_code": "ABCD",
                "verification_uri": "https://app.omnara.com/activate", "expires_in": 900, "interval": 5})
        if request.url.path.endswith("/token"):
            assert parse_qs(request.content.decode())["device_code"] == ["private-device-code"]
            state = replies.pop(0)
            return httpx.Response(200, json={"token_type": "Bearer", "access_token": "private-provider-token"}) if state == "connected" else httpx.Response(400, json={"error": state})
        assert request.url.path == "/v1/me"
        assert request.headers["authorization"] == "Bearer private-provider-token"
        return httpx.Response(200, json=IDENTITY)

    connection = OmnaraConnection(httpx.Client(transport=httpx.MockTransport(handle)), clock=lambda: now[0])
    public = connection.start("browser-a")
    assert "private" not in json.dumps(public)
    assert connection.start("browser-a") == public
    with pytest.raises(OmnaraError, match="could not") as error:
        connection.poll("browser-b")
    assert error.value.code == "authorization_flow_missing"
    assert connection.poll("browser-a")["state"] == "pending"
    assert len(requests) == 2
    now[0] = 5
    assert connection.poll("browser-a")["state"] == "pending"
    now[0] = 10
    assert connection.poll("browser-a")["poll_after_seconds"] == 10
    now[0] = 20
    verified = connection.poll("browser-a")
    assert verified["connected"] and verified["orgs"][0]["id"] == "org"
    assert "private-provider-token" not in json.dumps(verified)
    assert connection.pending is None


@pytest.mark.parametrize("url", ["https://evil.test/auth", "http://app.omnara.com/auth", "https://user@app.omnara.com/", "https://app.omnara.com:invalid/", None])
def test_untrusted_device_endpoints_rejected(url):
    with pytest.raises(OmnaraError):
        official_auth_url(url)


def test_expired_device_flow_never_calls_token_endpoint(monkeypatch):
    monkeypatch.delenv("OMNARA_TOKEN", raising=False)
    connection = OmnaraConnection(clock=lambda: 1000)
    connection.pending = {"browser_nonce": "a", "expires": 1}
    with pytest.raises(OmnaraError) as error:
        connection.poll("a")
    assert error.value.code == "authorization_expired"
    assert connection.pending is None


def test_provider_errors_do_not_reflect_secret_response():
    client = OmnaraClient(httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(401, json={"detail": "private-account-data"}))), "private-token")
    with pytest.raises(OmnaraError) as error:
        client.me()
    assert error.value.code == "authorization_required"
    assert "private" not in str(error.value)


@pytest.mark.parametrize("change", [
    {"candidate_id": "invented"}, {"slot_index": 8}, {"slot_index": True}, {"place_id": "invented"},
    {"reason_codes": [{"bad": "shape"}]}, {"reason_codes": ["reserved"]}, {"invite_draft": 123},
])
def test_ungrounded_advice_is_rejected(change):
    with pytest.raises(OmnaraError):
        StudyAdviceRunner.validate({**PROPOSAL, **change}, RESULTS)


def test_no_match_only_when_computed_candidates_are_empty():
    with pytest.raises(OmnaraError):
        StudyAdviceRunner.validate({"status": "no_match"}, RESULTS)
    assert StudyAdviceRunner.validate({"status": "no_match"}, {**RESULTS, "find_matches": {"candidates": []}}) == {"status": "no_match"}


def runner_fixture(*, tools=None, events=None, state="idle"):
    requests, now = [], [0]
    calls = tools if tools is not None else [{"id": "call-1", "name": "find_matches", "input": {}}]
    event_list = events if events is not None else [{"sequence": 1, "event_kind": "model_output", "stop_reason": "end_turn",
        "content_blocks": [{"type": "reasoning", "text": "private reasoning"}, {"type": "text", "text": json.dumps(PROPOSAL)}]}]
    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/agents"):
            assert request.headers["idempotency-key"] == "studymate-request-123"
            config = json.loads(request.content)["config_source"]["source"]
            assert set(config["tools"]) == set(RESULTS)
            assert "machine_sources" not in config and "subagents" not in config
            return httpx.Response(201, json={"agent": {"id": "agent-1"}})
        if request.url.path.endswith("/tool-calls"):
            return httpx.Response(200, json={"data": calls})
        if request.url.path.endswith("/result") or request.url.path.endswith("/cancel"):
            return httpx.Response(201, json={})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json={"data": event_list})
        return httpx.Response(200, json={"agent": {"activity": {"state": state}}})
    client = OmnaraClient(httpx.Client(transport=httpx.MockTransport(handle)), "token")
    runner = StudyAdviceRunner(client, clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0]+seconds))
    return runner, requests


def test_runner_serves_read_tools_and_validates_final_result_without_reasoning():
    runner, requests = runner_fixture()
    result = runner.run("org", "project", MODEL, RESULTS, "request-123")
    assert result["proposal"]["candidate_name"] == "Bella"
    assert result["trace"] == [{"tool": "find_matches", "status": "succeeded"}]
    assert "private reasoning" not in json.dumps(result)
    submitted = next(r for r in requests if r.url.path.endswith("/result"))
    assert json.loads(submitted.content)["content_blocks"][0]["value"] == RESULTS["find_matches"]


@pytest.mark.parametrize("calls,code", [
    ([{"id": "bad", "name": "send_email", "input": {}}], "tool_boundary_violation"),
    ([{"id": "bad", "name": "find_matches", "input": {"user": "invented"}}], "tool_boundary_violation"),
    ([{"id": str(i), "name": "find_matches", "input": {}} for i in range(7)], "tool_boundary_violation"),
    ([], "missing_evidence"),
])
def test_tool_limits_abort_and_cancel(calls, code):
    runner, requests = runner_fixture(tools=calls)
    with pytest.raises(OmnaraError) as error:
        runner.run("org", "project", MODEL, RESULTS, "request-123")
    assert error.value.code == code
    assert requests[-1].url.path.endswith("/cancel")
    assert sum(r.url.path.endswith("/result") for r in requests) <= 6


def test_model_turn_limit_cancels_at_eighth_unfinished_response():
    events = [{"sequence": i+1, "event_kind": "model_output", "stop_reason": "tool_use"} for i in range(8)]
    runner, requests = runner_fixture(events=events)
    with pytest.raises(OmnaraError) as error:
        runner.run("org", "project", MODEL, RESULTS, "request-123")
    assert error.value.code == "model_turn_budget"
    assert requests[-1].url.path.endswith("/cancel")


def test_request_timeout_cancels_bounded_runner():
    runner, requests = runner_fixture(events=[], state="running")
    with pytest.raises(OmnaraError) as error:
        runner.run("org", "project", MODEL, RESULTS, "request-123")
    assert error.value.code == "agent_timeout"
    assert requests[-1].url.path.endswith("/cancel")


def test_local_routes_require_auth_origin_and_never_upload_event_titles(monkeypatch):
    monkeypatch.delenv("OMNARA_TOKEN", raising=False)
    app = demo_app("sqlite://", datetime.fromisoformat("2026-10-24T13:00:00-07:00"))
    client = TestClient(app, base_url="http://127.0.0.1")
    headers = {"Authorization": "Bearer demo-alex-local"}
    assert client.get("/demo/omnara/status").status_code == 401
    assert client.get("/demo/omnara/status", headers={**headers, "Origin": "https://evil.test"}).status_code == 403
    assert not client.get("/demo/omnara/status", headers=headers).json()["connected"]
    with TestClient(app, base_url="http://evil.test") as other:
        assert other.get("/demo/omnara/status", headers=headers).status_code == 403
    app.state.omnara.resources = lambda org, project: {"models": [MODEL]}
    captured = []
    def run(self, org, project, model, tools, request_id):
        captured.append(tools)
        return {"proposal": {"status": "no_match"}}
    monkeypatch.setattr(StudyAdviceRunner, "run", run)
    body = {"org_id": "org", "project_id": "project", "model_id": "model", "request_id": "request-123"}
    first = client.post("/demo/omnara/advise", headers=headers, json=body)
    assert first.status_code == 200 and first.json()["data_mode"] == "synthetic"
    assert "appointment" not in json.dumps(captured) and "own_conflicts" not in json.dumps(captured)
    assert client.post("/demo/omnara/advise", headers=headers, json=body).status_code == 200
    assert len(captured) == 1
    assert client.post("/demo/omnara/advise", headers=headers, json={**body, "duration": 30}).status_code == 409
    assert client.post("/demo/omnara/advise", headers=headers, json={**body, "model_id": "unknown"}).status_code == 422
    assert client.post("/demo/omnara/advise", headers=headers, json={**body, "request_id": "request-30min", "duration": 30}).status_code == 200
    slot = captured[-1]["find_matches"]["candidates"][0]["slots"][0]
    assert (datetime.fromisoformat(slot["end"])-datetime.fromisoformat(slot["start"])).total_seconds() == 1800
