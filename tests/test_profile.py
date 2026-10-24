"""Profile track checks: privacy of public views, eligibility, and updates."""

import pytest
from fastapi.testclient import TestClient

from app import accounts, profile_core, store
from app.contracts import PrivateProfile
from app.main import app

PRIVATE_KEYS = {"email", "street_address", "gender_self", "gender_preference", "under_18"}


@pytest.fixture(autouse=True)
def restore_owner():
    saved = store.owner()
    yield
    store.replace_owner(saved)


@pytest.fixture
def client():
    client = TestClient(app)
    client.post("/login", data={"email": accounts.TEST_EMAIL, "password": accounts.TEST_PASSWORD})
    return client


def _set_owner(**fields):
    store.replace_owner({**store.owner(), **fields})


def _private_strings() -> list[str]:
    values = []
    for person in store.others():
        values.append(person["email"])
        if person["street_address"]:
            values.append(person["street_address"])
    return values


# Seed


def test_seed_students_are_full_private_profiles():
    for person in store.others():
        PrivateProfile.model_validate(person)


def test_seed_ids_are_unique_and_exclude_owner():
    ids = [person["id"] for person in store.others()]
    assert len(ids) == len(set(ids))
    assert store.OWNER_ID not in ids


# Privacy


def test_public_profiles_have_no_private_fields():
    ids = [person["id"] for person in store.others()]
    for profile in profile_core.public_profiles(ids):
        assert not PRIVATE_KEYS & profile.model_dump().keys()


def test_public_profiles_skip_unknown_ids():
    assert [p.id for p in profile_core.public_profiles(["u_jordan", "u_ghost"])] == ["u_jordan"]


def test_public_profile_unknown_id_raises():
    with pytest.raises(KeyError):
        profile_core.public_profile("u_ghost")


def test_email_for_reads_from_store():
    assert profile_core.email_for("u_jordan") == "jordan@example.edu"


@pytest.mark.parametrize("path", ["/api/profile/me", "/api/match", "/matching", "/session"])
def test_match_facing_pages_never_show_other_students_private_strings(client, path):
    body = client.get(path).text
    for value in _private_strings():
        assert value not in body


def test_owner_public_api_hides_owner_private_fields(client):
    data = client.get("/api/profile/me").json()
    assert not PRIVATE_KEYS & data.keys()


def test_proposal_people_carry_no_email():
    from app.matching_core import build_proposal

    proposal = build_proposal("CS 61A", "2026-10-29T19:00", "2026-10-29T21:00", "coffee", "")
    for person in proposal.people:
        assert "email" not in person.model_dump()


# Eligibility


def test_blank_owner_excludes_students_who_prefer_a_gender():
    ids = profile_core.eligible_student_ids()
    assert "u_aisha" not in ids
    assert "u_hana" not in ids
    assert "u_jordan" in ids


def test_mutual_gender_filter():
    _set_owner(gender_self="woman", gender_preference="woman")
    ids = set(profile_core.eligible_student_ids())
    women = {p["id"] for p in store.others() if p["gender_self"] == "woman" and not p["under_18"]}
    assert ids == women


def test_under_18_students_are_never_eligible():
    assert "u_kai" not in profile_core.eligible_student_ids()


def test_under_18_owner_gets_no_matches():
    _set_owner(under_18=True)
    assert profile_core.eligible_student_ids() == []


# Updates and radius center


def _form(**overrides):
    return {**profile_core.private_profile().model_dump(), **overrides}


def test_campus_anchor_uses_campus_point():
    profile = profile_core.update_profile(_form(anchor="campus"))
    assert (profile.anchor_lat, profile.anchor_lng) == (store.CAMPUS_LAT, store.CAMPUS_LNG)


def test_home_anchor_geocodes_the_address():
    profile = profile_core.update_profile(_form(anchor="home", street_address="2425 Durant Ave, Berkeley"))
    assert (profile.anchor_lat, profile.anchor_lng) == profile_core.geocode("2425 Durant Ave")
    assert (profile.anchor_lat, profile.anchor_lng) != (store.CAMPUS_LAT, store.CAMPUS_LNG)


def test_home_anchor_needs_an_address():
    with pytest.raises(ValueError):
        profile_core.update_profile(_form(anchor="home", street_address=""))


def test_home_anchor_with_unknown_address_is_rejected():
    with pytest.raises(ValueError):
        profile_core.update_profile(_form(anchor="home", street_address="1 Nowhere Rd"))


def test_partial_api_update_keeps_private_fields(client):
    _set_owner(street_address="2100 Channing Way", gender_self="woman", gender_preference="woman")
    response = client.put("/api/profile/me", json={"major": "Mathematics"})
    assert response.status_code == 200
    profile = profile_core.private_profile()
    assert profile.major == "Mathematics"
    assert profile.street_address == "2100 Channing Way"
    assert profile.gender_self == "woman"
    assert profile.gender_preference == "woman"


def test_api_validation_error_is_422(client):
    response = client.put("/api/profile/me", json={"radius_meters": 5})
    assert response.status_code == 422
    assert "Radius" in response.json()["detail"]


def test_form_post_saves_under_18(client):
    data = _form()
    data.pop("under_18")
    data["under_18"] = "1"
    response = client.post("/profile", data=data, follow_redirects=False)
    assert response.status_code == 303
    assert profile_core.private_profile().under_18 is True
