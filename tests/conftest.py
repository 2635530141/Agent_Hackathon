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
    for name in ("_invitations", "_latest_batch_ids", "_draft", "_owner", "_rooms"):
        monkeypatch.setattr(store, name, deepcopy(getattr(store, name)))
    yield
    if get_runtime.cache_info().currsize:
        get_runtime().repository.close()
    get_runtime.cache_clear()


@pytest.fixture(autouse=True)
def offline_places(monkeypatch):
    """No live Google Maps, OpenStreetMap, or Omnara place picker in tests."""
    from app import places
    monkeypatch.setenv("STUDYMATE_OFFLINE", "1")
    places.clear_cache()
    yield
    places.clear_cache()


@pytest.fixture(autouse=True)
def no_live_room_ranking(monkeypatch):
    """Rendering /session ranks rooms with the model; no test may reach Omnara."""
    from app import advise, room_matching

    class Unavailable:
        def complete(self, *args):
            raise advise.AdviseUnavailable("no model in tests")

    monkeypatch.setattr(room_matching, "client", lambda: Unavailable())
    monkeypatch.setattr(store, "_room_rankings", {})
