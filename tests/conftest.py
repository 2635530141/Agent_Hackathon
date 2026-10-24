"""Isolated synthetic calendar DBs; never read/write the interactive demo database."""
from copy import deepcopy
import pytest
from app import store
from app.scheduling.runtime import get_runtime


@pytest.fixture(autouse=True)
def isolated_calendar(tmp_path, monkeypatch):
    get_runtime.cache_clear()
    monkeypatch.setenv("STUDYMATE_CALENDAR_DB", f"sqlite+pysqlite:///{(tmp_path/'calendar.db').as_posix()}")
    monkeypatch.setenv("STUDYMATE_CALENDAR_MODE", "synthetic")
    monkeypatch.setenv("STUDYMATE_AGENT_DB", str(tmp_path / "agent.db"))
    for name in ("_invitations", "_latest_batch_ids", "_draft", "_owner"):
        monkeypatch.setattr(store, name, deepcopy(getattr(store, name)))
    yield
    if get_runtime.cache_info().currsize:
        get_runtime().repository.close()
    get_runtime.cache_clear()


STATE = [name for name, value in vars(store).items() if name.startswith("_") and isinstance(value, (dict, list, int, str, bool))]


@pytest.fixture(autouse=True)
def fresh_store():
    """The store is module-level state; give every test the seed and put it back after."""
    saved = {name: deepcopy(getattr(store, name)) for name in STATE}
    yield
    for name, value in saved.items():
        setattr(store, name, value)


@pytest.fixture
def client():
    """Signed in as the demo owner, since every page sits behind the login."""
    from fastapi.testclient import TestClient

    from app import accounts
    from app.main import app

    client = TestClient(app)
    client.post("/login", data={"email": accounts.TEST_EMAIL, "password": accounts.TEST_PASSWORD})
    return client


@pytest.fixture(autouse=True)
def no_live_model(request, monkeypatch):
    """No test reaches Omnara. Advise uses the Filter-order test policy unless a test is
    marked real_advise, which runs the real retry/validation loop with a ScriptedModel."""
    from app import advise, matching_core
    from factories import first_free_advice

    def unavailable():
        raise advise.AdviseUnavailable("no model in tests")

    class Unavailable:
        def complete(self, *args):
            unavailable()

    monkeypatch.setattr(matching_core, "advise_client", lambda: Unavailable())
    if "real_advise" not in request.keywords:
        monkeypatch.setattr(advise, "advise", first_free_advice)
