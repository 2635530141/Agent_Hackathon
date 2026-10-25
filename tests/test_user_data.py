"""Per-account data in a database: accounts, profiles, courses, rooms, and schedules.

A temporary SQLite file stands in for Supabase Postgres; the code paths are the same
SQLAlchemy calls. Postgres-only details (URL handling, upserts) are checked directly.
"""

from contextlib import contextmanager
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import accounts, db, profile_core, store
from app.main import app
from app.scheduling.context import local_subject


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDYMATE_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'users.db').as_posix()}")
    db._engine_for.cache_clear()
    store._seeded.clear()
    store._cache.clear()
    yield
    restart()


def restart():
    """What a server restart forgets: engines, caches, and the in-memory working copies."""
    for engine in [db._engine_for(db.url())]:
        engine.dispose()
    db._engine_for.cache_clear()
    store._seeded.clear()
    store._cache.clear()


@contextmanager
def signed_in(user_id):
    token = local_subject.set(user_id)
    try:
        yield
    finally:
        local_subject.reset(token)


def filled(**changes):
    return {"major": "Physics", "year": "2", "courses": "Physics 7A, Math 54", "street_address": "2425 Durant Ave",
            "radius_meters": "1500", "group_min": "2", "group_max": "3", "study_styles": ["talk"],
            "place_types": ["library"], **changes}


# Accounts


def test_accounts_are_unique_and_survive_a_restart(database):
    first = accounts.register("Ada Lovelace", "ada@example.edu", "password-123")
    second = accounts.register("Alan Turing", "alan@example.edu", "password-123")
    assert first["id"] != second["id"] and first["id"].startswith("user_")
    with pytest.raises(ValueError, match="already has an account"):
        accounts.register("Ada Again", "ADA@example.edu", "password-123")
    restart()
    assert accounts.authenticate("ada@example.edu", "password-123")["id"] == first["id"]
    assert accounts.authenticate("ada@example.edu", "wrong-password") is None
    assert accounts.authenticate("maya@example.edu", "studymate")["id"] == "u_maya"
    assert {first["id"], second["id"], "u_maya"} <= set(accounts.all_ids())


# Profiles


def test_a_new_account_starts_with_its_own_blank_profile(database):
    ada = accounts.register("Ada Lovelace", "ada@example.edu", "password-123")
    with signed_in(ada["id"]):
        profile = profile_core.private_profile()
    assert profile.id == ada["id"] and profile.name == "Ada Lovelace" and profile.email == "ada@example.edu"
    assert profile.street_address == "" and profile.course_ids == [] and profile.major == ""
    assert not store.is_complete(profile.model_dump())


def test_profiles_are_saved_per_account_and_survive_a_restart(database):
    ada = accounts.register("Ada Lovelace", "ada@example.edu", "password-123")
    with signed_in(ada["id"]):
        profile_core.update_profile(filled())
    restart()
    with signed_in(ada["id"]):
        mine = profile_core.private_profile()
    with signed_in("u_maya"):
        maya = profile_core.private_profile()
    assert mine.major == "Physics" and mine.street_address == "2425 Durant Ave"
    assert maya.major == "EECS" and maya.street_address == "2100 Channing Way"  # Untouched.


def test_matching_compares_against_other_accounts(database):
    ada = accounts.register("Ada Lovelace", "ada@example.edu", "password-123")
    new = accounts.register("Not Finished", "new@example.edu", "password-123")
    with signed_in(ada["id"]):
        profile_core.update_profile(filled())
    with signed_in("u_maya"):
        others = {person["id"] for person in store.others()}
        eligible = set(profile_core.eligible_student_ids())
    assert ada["id"] in others and ada["id"] in eligible
    assert new["id"] not in others  # An empty profile is not matched.
    with signed_in(ada["id"]):
        assert "u_maya" in {person["id"] for person in store.others()}
        assert ada["id"] not in {person["id"] for person in store.others()}


def test_private_fields_stay_private_across_accounts(database):
    ada = accounts.register("Ada Lovelace", "ada@example.edu", "password-123")
    with signed_in(ada["id"]):
        profile_core.update_profile(filled(gender_self="female"))
    with signed_in("u_maya"):
        public = profile_core.public_profile(ada["id"]).model_dump()
    assert "street_address" not in public and "email" not in public and "gender_self" not in public


def test_typed_courses_are_shared_and_survive_a_restart(database):
    ada = accounts.register("Ada Lovelace", "ada@example.edu", "password-123")
    with signed_in(ada["id"]):
        profile_core.update_profile(filled(courses="Physics 7A"))
    restart()
    assert store.course_by_id("physics7a")["code"] == "Physics 7A"


# Rooms


def test_rooms_are_stored_and_closing_one_removes_it(database, monkeypatch):
    monkeypatch.setattr(store, "_rooms", [])
    ada = {"id": "user_ada", "first_name": "Ada", "major": "Physics", "year": 2}
    room = store.create_room(ada, "math54", "Math 54", "2026-10-29T16:00", "2026-10-29T18:00", "library", "Doe Library", 3)
    store.join_room(room["id"], {"id": "u_maya", "first_name": "Maya", "major": "EECS", "year": 3})
    restart()
    store._rooms[:] = []  # The in-memory copy is gone after a restart.
    saved = next(item for item in store.rooms() if item["id"] == room["id"])
    assert [member["id"] for member in saved["members"]] == ["user_ada", "u_maya"]
    store.leave_room(room["id"], "u_maya")
    store.leave_room(room["id"], "user_ada")
    restart()
    assert room["id"] not in {item["id"] for item in store.rooms()}


def test_demo_rooms_are_seeded_once(database):
    seeded = {room["id"] for room in store.rooms()}
    assert "room_doe_thu" in seeded
    store.leave_room("room_doe_thu", "u_jordan")
    store.leave_room("room_doe_thu", "u_sam")
    restart()
    assert "room_doe_thu" not in {room["id"] for room in store.rooms()}  # Not re-seeded.


# Web


def test_new_account_sees_a_blank_profile_page_not_mayas(database):
    with TestClient(app) as client:
        client.post("/register", data={"name": "Ada Lovelace", "email": "ada@example.edu", "password": "password-123"})
        body = client.get("/profile").text
    assert "Finish your profile" in body
    assert "2100 Channing Way" not in body and "maya@example.edu" not in body


def test_two_browsers_keep_their_own_profiles(database):
    with TestClient(app) as ada, TestClient(app) as maya:
        ada.post("/register", data={"name": "Ada Lovelace", "email": "ada@example.edu", "password": "password-123"})
        maya.post("/login", data={"email": "maya@example.edu", "password": "studymate"})
        assert ada.post("/profile", data=filled(), follow_redirects=False).headers["location"] == "/profile?saved=1"
        assert ada.get("/api/profile/me").json()["major"] == "Physics"
        assert maya.get("/api/profile/me").json()["major"] == "EECS"


# Postgres specifics


@pytest.mark.parametrize("given", ["postgres://u:p@db.example.com:5432/postgres", "postgresql://u:p@db.example.com:5432/postgres"])
def test_cloud_urls_use_the_psycopg_driver(monkeypatch, given):
    monkeypatch.setenv("DATABASE_URL", given)
    assert db.cloud_url() == "postgresql+psycopg://u:p@db.example.com:5432/postgres"
    assert db.where() == "Postgres at db.example.com"  # Never the password.


def test_postgres_statements_compile():
    from sqlalchemy.dialects import postgresql
    from app.scheduling.core.storage import SQLiteScheduleRepository

    repository = SQLiteScheduleRepository("postgresql+psycopg://u:p@localhost:5432/postgres")  # Does not connect yet.
    assert not repository.is_sqlite
    from app.scheduling.core.storage import states
    sql = str(repository._insert_ignore(states, owner_id="x").compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT DO NOTHING" in sql
