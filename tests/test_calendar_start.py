"""Setup checks use fake credentials; never contact Google or open a native dialog."""
import json
import socket

import pytest
from fastapi.testclient import TestClient
from app import accounts, calendar_preview, calendar_start
from app.main import app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDYMATE_GOOGLE_CLIENT_FILE", raising=False)
    monkeypatch.setattr(calendar_start, "ROOT", tmp_path)
    monkeypatch.setattr(calendar_start, "port_available", lambda port: True)
    client = tmp_path / "fake-web.json"
    client.write_text(json.dumps({"web":{"client_id":"SYNTHETIC_ID_SENTINEL",
        "client_secret":"SYNTHETIC_SECRET_SENTINEL",
        "redirect_uris":["http://127.0.0.1:8017/calendar/google/callback"]}}))
    return client, tmp_path / "private"


def test_check_is_read_only_and_redacted(setup, capsys):
    client, private = setup
    assert calendar_start.main(["--check", "--client-file", str(client), "--private-dir", str(private)]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["ready"] is True
    assert "SYNTHETIC_ID_SENTINEL" not in output and "SYNTHETIC_SECRET_SENTINEL" not in output
    assert not private.exists()


@pytest.mark.parametrize("case,code", [
    ("missing", "CLIENT_FILE_REQUIRED"), ("desktop", "WEB_CLIENT_REQUIRED"),
    ("broken", "CLIENT_FILE_INVALID"), ("callback", "CALLBACK_MISMATCH"),
    ("incomplete", "CLIENT_FILE_INVALID"), ("localhost", "CALLBACK_MISMATCH"),
])
def test_setup_failures_have_safe_actionable_codes(setup, case, code):
    client, private = setup
    if case == "missing":
        client = None
    elif case == "desktop":
        client.write_text('{"installed":{"client_secret":"DO_NOT_PRINT"}}')
    elif case == "broken":
        client.write_text('invalid JSON DO_NOT_PRINT')
    elif case == "callback":
        data = json.loads(client.read_text()); data["web"]["redirect_uris"] = []
        client.write_text(json.dumps(data))
    elif case == "localhost":
        data = json.loads(client.read_text())
        data["web"]["redirect_uris"] = ["http://localhost:8017/calendar/google/callback"]
        client.write_text(json.dumps(data))
    else:
        data = json.loads(client.read_text()); del data["web"]["client_secret"]
        client.write_text(json.dumps(data))
    result = calendar_start.inspect_setup(client, 8017, private)
    assert result["ready"] is False and code in {i["code"] for i in result["issues"]}
    assert "DO_NOT_PRINT" not in json.dumps(result)
    assert not private.exists()


def test_missing_existing_keys_are_not_regenerated(setup):
    client, private = setup
    private.mkdir()
    database = private / "calendar.db"
    database.write_bytes(b"synthetic-existing-database")
    result = calendar_start.inspect_setup(client, 8017, private)
    assert "RUNTIME_KEYS_MISSING" in {i["code"] for i in result["issues"]}
    assert database.read_bytes() == b"synthetic-existing-database"
    assert not (private / "calendar.key").exists()


def test_occupied_port_does_not_switch_to_unregistered_callback(setup, monkeypatch):
    client, private = setup
    monkeypatch.setattr(calendar_start, "port_available", lambda port: False)
    result = calendar_start.inspect_setup(client, 8017, private)
    assert "PORT_IN_USE" in {i["code"] for i in result["issues"]}
    assert result["callback"] == "http://127.0.0.1:8017/calendar/google/callback"


@pytest.mark.parametrize("port", [-1, 0, 65536])
def test_invalid_ports_are_reported_without_binding(setup, port, monkeypatch):
    client, private = setup
    monkeypatch.setattr(calendar_start, "port_available", lambda port: pytest.fail("must not bind an invalid port"))
    result = calendar_start.inspect_setup(client, port, private)
    assert "INVALID_PORT" in {i["code"] for i in result["issues"]}


def test_saved_client_env_and_explicit_selection_precedence(setup, monkeypatch):
    client, private = setup
    assert calendar_start.resolve_client(None, private) is None
    private.mkdir()
    (private / "launch-client.json").write_text(json.dumps({"client_file":str(client)}))
    assert calendar_start.resolve_client(None, private) == client
    other = client.parent / "env.json"
    monkeypatch.setenv("STUDYMATE_GOOGLE_CLIENT_FILE", str(other))
    assert calendar_start.resolve_client(None, private) == other
    assert calendar_start.resolve_client(client, private) == client


def test_valid_launch_remembers_only_path_and_reuses_preview(setup, monkeypatch):
    client, private = setup
    calls = []
    monkeypatch.setattr(calendar_preview, "main", lambda argv: calls.append(argv))
    assert calendar_start.main(["--client-file", str(client), "--private-dir", str(private)]) == 0
    assert calls == [["--client-file", str(client), "--port", "8017", "--private-dir", str(private)]]
    settings = (private / "launch-client.json").read_text()
    assert json.loads(settings) == {"client_file":str(client.resolve())}
    assert "SYNTHETIC_SECRET_SENTINEL" not in settings


def test_cancelled_file_picker_stops_without_fake_success(setup, monkeypatch):
    _, private = setup
    monkeypatch.setattr(calendar_start, "choose_client", lambda: None)
    monkeypatch.setattr(calendar_preview, "main", lambda argv: pytest.fail("must not start"))
    assert calendar_start.main(["--choose-client", "--private-dir", str(private)]) == 2
    assert not private.exists()


def test_check_never_opens_picker_or_starts_server(setup, monkeypatch, capsys):
    _, private = setup
    monkeypatch.setattr(calendar_start, "choose_client", lambda: pytest.fail("no picker during check"))
    monkeypatch.setattr(calendar_preview, "main", lambda argv: pytest.fail("no server during check"))
    assert calendar_start.main(["--check", "--choose-client", "--private-dir", str(private)]) == 2
    assert json.loads(capsys.readouterr().out)["ready"] is False


def test_real_port_probe_detects_existing_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert calendar_start.port_available(listener.getsockname()[1]) is False


def test_team_setup_page_explains_account_and_google_boundaries():
    with TestClient(app) as browser:
        browser.post("/login", data={"email":accounts.TEST_EMAIL, "password":accounts.TEST_PASSWORD})
        response = browser.get("/schedule/google-setup")
        assert response.status_code == 200
        for instruction in ("Start-Calendar.cmd", "redirect_uri_mismatch", "Testing", "private local account"):
            assert instruction in response.text
