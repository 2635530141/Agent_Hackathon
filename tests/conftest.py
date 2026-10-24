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
    for name in ("_invitations", "_latest_batch_ids", "_draft", "_owner"):
        monkeypatch.setattr(store, name, deepcopy(getattr(store, name)))
    yield
    if get_runtime.cache_info().currsize:
        get_runtime().repository.close()
    get_runtime.cache_clear()
