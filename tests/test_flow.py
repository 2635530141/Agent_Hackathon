"""Matching through the session routes and the seeded store.

Seed: Maya (owner, EECS 3, quiet, campus) is free Tue 6–9 PM and Thu 4–9 PM.
Tests that care who is chosen script the model's answer (seed_model); the rest run on
the Filter-order test policy. Jordan has a lab Tue 6–7 PM.
"""

import html
import re

import pytest

from app import matching_core as m
from app import store
from factories import ScriptedModel
from app.advise import advise as REAL_ADVISE

TUE = {
    "course_id": "cs61a",
    "starts_at": "2026-10-27T19:00",
    "ends_at": "2026-10-27T21:00",
    "place_type": "library",
    "note": "",
    "study_anyway": "0",
    "ready": "1",
}


SEED_ANSWER = {
    "no_match": False,
    "chosen_ids": ["u_jordan"],
    "slot": {"starts_at": "2026-10-27T19:00-07:00", "ends_at": "2026-10-27T21:00-07:00"},
    "reason": "Jordan shares CS 61A, your year, major, and quiet style, and is free 7-9 PM.",
    "ranking": [
        {"id": "u_jordan", "why": "same course, year, major, style"},
        {"id": "u_priya", "why": "same course, year, major, style"},
        {"id": "u_sam", "why": "same course, talks it through"},
        {"id": "u_ken", "why": "free, Math 54 only"},
    ],
    "runner_ups": ["u_priya"],
    "near_tie": True,
    "flags": [],
    "alternative": None,
}
ANA_ANSWER = {
    "no_match": False,
    "chosen_ids": ["u_ana"],
    "slot": {"starts_at": "2026-11-05T16:00-08:00", "ends_at": "2026-11-05T18:00-08:00"},
    "reason": "Ana has the same Math 54 exam and is free 4-6 PM.",
    "ranking": [{"id": "u_ana", "why": "same exam"}],
    "runner_ups": [],
    "near_tie": False,
    "flags": [],
    "alternative": None,
}


@pytest.fixture
def seed_model(monkeypatch):
    """The real Advise loop with a scripted answer per request, in call order."""
    from app import advise

    monkeypatch.setattr(advise, "advise", REAL_ADVISE)
    model = ScriptedModel(SEED_ANSWER, ANA_ANSWER)
    monkeypatch.setattr(m, "advise_client", lambda: model)
    return model


def approved(response):
    """The hidden fields the card posts with Approve: who and when was approved."""
    text = html.unescape(response.text)
    return {name: re.search(rf'name="{name}" value="([^"]*)"', text).group(1)
            for name in ("approved_ids", "approved_starts_at", "approved_ends_at")}


def card(response):
    text = html.unescape(response.text)
    people = re.findall(r'<div class="person">\s*<strong>([^<]*)</strong>', text)
    invite = re.findall(r'<textarea name="invite">([^<]*)</textarea>', text)
    warning = re.findall(r'callout warn"><p>([^<]*)', text)
    return people, invite[0] if invite else "", warning


def test_seed_tie_shows_the_models_pick_and_ranking(client, seed_model):
    # Changed: the "Close call with Priya…" sentence was template text from scores. The card
    # now shows the model's reason and its ranking, marked as a close call.
    response = client.get("/session", params=TUE)
    people, invite, warning = card(response)
    assert (people, warning) == (["Jordan"], [])
    assert invite.startswith("Hi Jordan!") and "Doe Library" in invite
    text = html.unescape(response.text)
    assert SEED_ANSWER["reason"] in text
    assert "Ranking · close call" in text


def test_own_conflict_stops_matching_before_a_proposal(client):
    thu = {**TUE, "starts_at": "2026-10-29T18:00", "ends_at": "2026-10-29T21:00"}
    response = client.get("/session", params=thu)
    assert card(response)[0] == []
    assert "CS 61A lecture" in response.text


def test_swap_person_cycles_the_ranking(client, seed_model):
    seen = []
    for _ in range(4):
        people, invite, _ = card(client.get("/session", params=TUE))
        seen.append(people[0])
        client.post("/session/swap-person", data={**TUE, "invite": invite})
    assert seen == ["Jordan", "Priya", "Sam", "Ken"]


def test_unedited_invite_follows_the_swap_and_survives_refresh(client, seed_model):
    _, invite, _ = card(client.get("/session", params=TUE))
    client.post("/session/swap-person", data={**TUE, "invite": invite})
    first = card(client.get("/session", params=TUE))
    again = card(client.get("/session", params=TUE))
    assert first[0] == again[0] == ["Priya"]
    assert first[1].startswith("Hi Priya!") and again[1] == first[1]


def test_edited_invite_is_kept_across_swaps(client, seed_model):
    client.get("/session", params=TUE)
    client.post("/session/swap-person", data={**TUE, "invite": "My own words"})
    people, invite, _ = card(client.get("/session", params=TUE))
    assert (people, invite) == (["Priya"], "My own words")


def test_new_request_resets_swaps_and_drafts(client, seed_model):
    client.get("/session", params=TUE)
    client.post("/session/swap-person", data={**TUE, "invite": "My own words"})
    math = {**TUE, "course_id": "math54", "starts_at": "2026-11-05T16:00", "ends_at": "2026-11-05T18:00"}
    people, invite, _ = card(client.get("/session", params=math))
    assert people == ["Ana"]
    assert invite.startswith("Hi Ana!")


def test_swap_place(client):
    _, invite, _ = card(client.get("/session", params=TUE))
    client.post("/session/swap-place", data={**TUE, "invite": invite})
    assert "Yali's Cafe" in card(client.get("/session", params=TUE))[1]


def test_approve_records_the_proposed_slot_not_the_requested_one(client, monkeypatch):
    monkeypatch.setattr(m, "eligible_student_ids", lambda: ["u_jordan"])
    evening = {**TUE, "starts_at": "2026-10-27T18:00", "ends_at": "2026-10-27T21:00"}
    page = client.get("/session", params=evening)
    _, invite, _ = card(page)
    client.post("/session/confirm", data={**evening, "invite": invite, **approved(page)})
    [invitation] = store.latest_invitations()
    assert (invitation["person_id"], invitation["status"]) == ("u_jordan", "pending")
    assert (invitation["starts_at"], invitation["ends_at"]) == ("2026-10-27T19:00", "2026-10-27T21:00")
    assert invitation["place_name"] == "Doe Library"
    assert invitation["message"] == invite


def test_group_approve_invites_everyone_in_the_proposal(client):
    store.replace_owner({**store.owner(), "group_size": 3})
    page = client.get("/session", params=TUE)
    people, invite, _ = card(page)
    client.post("/session/confirm", data={**TUE, "invite": invite, **approved(page)})
    invited = store.latest_invitations()
    assert [item["first_name"] for item in invited] == people
    assert len(invited) == 2
    assert len({(item["starts_at"], item["ends_at"], item["place_name"]) for item in invited}) == 1


def test_no_match_shows_a_message_and_approve_sends_nothing(client, monkeypatch):
    monkeypatch.setattr(store, "_others", [])
    people, _, warning = card(client.get("/session", params=TUE))
    assert people == []
    # Changed: the course no longer filters, so the message is about eligibility.
    assert warning == ["Nobody else is eligible to match yet."]
    client.post("/session/confirm", data={**TUE, "invite": "hi"})
    assert store.latest_invitations() == []


def test_approve_without_an_approved_slot_sends_nothing(client):
    _, invite, _ = card(client.get("/session", params=TUE))
    client.post("/session/confirm", data={**TUE, "invite": invite})
    assert store.latest_invitations() == []


def test_ineligible_students_are_never_proposed(monkeypatch):
    monkeypatch.setattr(m, "eligible_student_ids", lambda: ["u_sam"])
    proposal = m.build_proposal("CS 61A", TUE["starts_at"], TUE["ends_at"], "library", "")
    assert [p.first_name for p in proposal.people] == ["Sam"]


def test_under_18_and_gender_filters_come_from_the_profile_service():
    ranked = m.match(m.StudyRequest(course_id="cs61a", starts_at=TUE["starts_at"], ends_at=TUE["ends_at"], place_type="library"))
    ids = {cand.person.profile.id for cand in ranked.candidates}
    assert "u_kai" not in ids
    assert "u_aisha" not in ids


def test_unknown_place_type_falls_back_to_the_profile():
    proposal = m.build_proposal("CS 61A", TUE["starts_at"], TUE["ends_at"], "spa", "")
    assert proposal.place.kind == "coffee"


def test_profile_changes_reach_matching():
    # Changed: there is no style score to reorder people; the style reaches the model's input.
    store.replace_owner({**store.owner(), "study_style": "talk"})
    assert m.requester_brief(m.load_inputs().me)["style"] == "talk it through"


def test_api_match_shapes(client):
    body = client.get("/api/match", params=TUE).json()
    assert set(body) == {"request", "funnel", "later", "candidates", "proposal", "message"}
    # Changed: the Filter has no scores; everyone free 7-9 PM has two hours, so id order.
    assert body["candidates"][0]["first_name"] == "Jordan" and body["proposal"] is None
    assert set(body["candidates"][0]) == {"id", "first_name", "year", "major", "style", "shared_courses",
                                          "within_radius", "minutes_away", "slot"}
    bad = client.get("/api/match", params={**TUE, "ends_at": "2026-10-27T17:00"}).json()
    assert bad["proposal"] is None and bad["message"] == "The session has to end after it starts."


def test_matching_page_shows_ranking_and_keeps_the_form(client):
    response = client.get("/matching", params={**TUE, "note": "not a library", "study_anyway": "1"})
    text = html.unescape(response.text)
    assert response.status_code == 200
    assert "free in your window" in text
    assert "Jordan" in text and "Priya" in text
    assert ">not a library</textarea>" in text
    assert 'name="study_anyway" value="1" checked' in text


@pytest.mark.parametrize("path", ["/api/match", "/matching", "/session"])
def test_private_fields_never_leave_matching(client, path):
    store.replace_owner({**store.owner(), "gender_self": "nonbinary", "gender_preference": "woman"})
    text = client.get(path, params=TUE).text
    private = ("maya@example.edu", "jordan@example.edu", "2100 Channing Way", "2425 Durant Ave", "nonbinary", "Lab section", "Work shift")
    for value in private:
        assert value not in text
