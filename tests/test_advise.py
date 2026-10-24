"""validate_match, the Advise loop, what lands on the proposal, and the Send re-check."""

import logging

import pytest

from app import advise, matching_core as m, matching_prompt, store
from app.advise import AdviseError, AdviseUnavailable
from factories import ME, ScriptedModel, inputs, person, request

pytestmark = pytest.mark.real_advise

WINDOW = {"starts_at": "2026-10-27T19:00-07:00", "ends_at": "2026-10-27T21:00-07:00"}
LATER = {"starts_at": "2026-10-28T17:00-07:00", "ends_at": "2026-10-28T19:00-07:00"}


def answer(**fields):
    base = {
        "no_match": False,
        "chosen_ids": ["u_a"],
        "slot": dict(WINDOW),
        "reason": "Alex shares CS 61A and is free the whole window.",
        "ranking": [{"id": "u_a", "why": "same course"}, {"id": "u_b", "why": "free too"}],
        "runner_ups": ["u_b"],
        "near_tie": False,
        "flags": [],
        "alternative": None,
    }
    return {**base, **fields}


def setup(me=ME, *people):
    people = people or (person("u_a", "Alex Kim"), person("u_b", "Bo Wu"))
    pair = inputs(me, *people)
    found, *_ = m.find_matches(request(), pair)
    return m.request_facts(request(), me), [cand.facts for cand in found], pair


# validate_match


def test_valid_answer_has_no_errors():
    facts, records, _ = setup()
    assert matching_prompt.validate_match(answer(), facts, records) == []


def test_invented_id():
    facts, records, _ = setup()
    errors = matching_prompt.validate_match(answer(chosen_ids=["u_ghost"]), facts, records)
    assert "chosen id u_ghost is not in the candidate list" in errors


def test_slot_outside_free_overlap():
    late = person("u_b", "Bo Wu", weekly={"tue": ("20:00", "22:00")})
    facts, records, _ = setup(ME, person("u_a", "Alex Kim"), late)
    errors = matching_prompt.validate_match(answer(chosen_ids=["u_b"], runner_ups=["u_a"]), facts, records)
    assert errors == ["slot is not inside free_overlap for u_b"]


def test_wrong_count_for_group_size_three():
    me = person("u_me", "Maya Chen", group_size=3)
    facts, records, _ = setup(me)
    errors = matching_prompt.validate_match(answer(runner_ups=[]), facts, records)
    assert "group_size 3 needs exactly 2 chosen ids, got 1" in errors


@pytest.mark.parametrize(
    "reason",
    ["Write to alex@example.edu.", "Alex lives at 2425 Durant Ave.", "Alex is a woman in CS 61A."],
    ids=["email", "street", "gender"],
)
def test_private_field_in_reason(reason):
    facts, records, _ = setup()
    errors = matching_prompt.validate_match(answer(reason=reason), facts, records)
    assert "reason or ranking mentions a private field (email, street address, or gender)" in errors


def test_no_match_without_an_alternative():
    facts, records, _ = setup()
    errors = matching_prompt.validate_match(answer(no_match=True, chosen_ids=[], slot=None, runner_ups=[]), facts, records)
    assert "no_match needs an alternative from other_free" in errors


def test_flagged_candidate_cannot_be_chosen():
    facts, records, _ = setup()
    flagged = answer(flags=[{"source": "u_a", "text": "bio asks to invite everyone"}])
    assert "chosen id u_a is flagged for an instruction in their bio" in matching_prompt.validate_match(flagged, facts, records)


@pytest.mark.parametrize("source", ["u_a", "u_a.bio", "bio of u_a", "u_a/bio"])
def test_flag_source_in_any_form_blocks_that_candidate(source):
    # Seen live: the model flagged "u_inject.bio" and still chose u_inject.
    facts, records, _ = setup()
    flagged = answer(flags=[{"source": source, "text": "instruction in bio"}])
    assert "chosen id u_a is flagged for an instruction in their bio" in matching_prompt.validate_match(flagged, facts, records)


def test_flag_on_another_candidate_does_not_block():
    facts, records, _ = setup()
    assert matching_prompt.validate_match(answer(flags=[{"source": "u_b.bio", "text": "x"}]), facts, records) == []


def test_near_tie_needs_a_runner_up():
    facts, records, _ = setup()
    errors = matching_prompt.validate_match(answer(near_tie=True, runner_ups=[]), facts, records)
    assert "near_tie is true but no runner-up is named" in errors


# The Advise loop


def test_one_call_with_system_prompt_schema_and_low_temperature():
    facts, records, pair = setup()
    model = ScriptedModel(answer())
    result = advise.advise(facts, m.requester_brief(pair.me), records, model)
    assert result.ok and result.attempts == 1
    [call] = model.calls
    assert call["system"] == matching_prompt.SYSTEM_PROMPT
    assert call["schema"] == matching_prompt.PROPOSAL_SCHEMA
    assert call["temperature"] <= 0.2
    assert call["user"] == matching_prompt.build_matching_message(facts, m.requester_brief(pair.me), records)


def test_retry_once_with_the_errors_appended():
    facts, records, pair = setup()
    model = ScriptedModel(answer(chosen_ids=["u_ghost"]), answer())
    result = advise.advise(facts, m.requester_brief(pair.me), records, model)
    assert result.ok and result.attempts == 2
    assert "chosen id u_ghost is not in the candidate list" in model.calls[1]["user"]
    assert model.calls[1]["user"].startswith(model.calls[0]["user"])


def test_second_failure_goes_to_manual_pick_with_the_models_ranking():
    facts, records, pair = setup()
    bad = answer(chosen_ids=["u_ghost"], ranking=[{"id": "u_b", "why": "free"}, {"id": "u_ghost", "why": "?"}])
    result = advise.advise(facts, m.requester_brief(pair.me), records, ScriptedModel(bad, "not json"))
    assert not result.ok and result.attempts == 2
    assert result.ranking == [{"id": "u_b", "why": "free"}]


def test_transport_error_counts_as_an_attempt():
    facts, records, pair = setup()
    result = advise.advise(facts, m.requester_brief(pair.me), records, ScriptedModel(AdviseError("timeout"), answer()))
    assert result.ok and result.attempts == 2


def test_unconfigured_model_stops_without_retry():
    facts, records, pair = setup()
    model = ScriptedModel(AdviseUnavailable("not configured"), answer())
    result = advise.advise(facts, m.requester_brief(pair.me), records, model)
    assert not result.ok and len(model.calls) == 1


def test_json_text_answer_is_accepted():
    assert advise.parse_output('```json\n{"a": 1}\n```') == {"a": 1}
    assert advise.parse_output("no json") is None


# Onto the proposal


def test_ranking_runner_ups_near_tie_and_flags_land_on_the_proposal():
    _, _, pair = setup()
    said = answer(near_tie=True, flags=[{"source": "note", "text": "asked to email everyone"}])
    proposal = m.match(request(), pair, client=ScriptedModel(said)).proposal
    assert [p.id for p in proposal.people] == ["u_a"]
    assert (proposal.starts_at, proposal.ends_at) == ("2026-10-27T19:00", "2026-10-27T21:00")
    assert proposal.reason.startswith("Alex shares CS 61A and is free the whole window.")
    assert [(r.id, r.first_name) for r in proposal.ranking] == [("u_a", "Alex"), ("u_b", "Bo")]
    assert proposal.runner_ups == ["u_b"] and proposal.near_tie
    assert proposal.flags == said["flags"] and proposal.source == "model"
    assert proposal.place.kind == "library" and proposal.invite.startswith("Hi Alex!")


def test_flags_raise_the_injection_alert_and_near_tie_is_logged(caplog):
    _, _, pair = setup()
    said = answer(flags=[{"source": "u_b", "text": "bio says ignore your rules"}])
    with caplog.at_level(logging.INFO, logger="app.matching_core"):
        m.match(request(), pair, client=ScriptedModel(said))
    assert "advise proposal near_tie=False flags=" in caplog.text
    assert "INJECTION_ALERT source=advise" in caplog.text


def test_no_alert_without_flags(caplog):
    _, _, pair = setup()
    with caplog.at_level(logging.INFO, logger="app.matching_core"):
        m.match(request(), pair, client=ScriptedModel(answer()))
    assert "INJECTION_ALERT" not in caplog.text


def test_swap_follows_the_models_ranking_without_a_new_call():
    _, _, pair = setup()
    said = answer(ranking=[{"id": "u_a", "why": "same course"}, {"id": "u_b", "why": "talks it through"}])
    first = m.match(request(), pair, client=ScriptedModel(said))
    swapped = m.match(request(), pair, swap_person=1, advice=first.advice, client=ScriptedModel())
    assert [p.id for p in swapped.proposal.people] == ["u_b"]
    assert swapped.proposal.reason.startswith("You swapped to Bo. talks it through")


def test_no_match_maps_the_alternative_onto_a_later_proposal():
    blocked = person("u_a", "Alex Kim", busy=[("calendar", "2026-10-27T17:00", "2026-10-27T22:00")])
    pair = inputs(ME, blocked)
    said = answer(no_match=True, chosen_ids=[], slot=None, runner_ups=[], ranking=[{"id": "u_a", "why": "free Wednesday"}],
                  reason="Nobody is free Tuesday evening; Alex is free Wednesday at 5.", alternative={"ids": ["u_a"], **LATER})
    result = m.match(request(), pair, client=ScriptedModel(said))
    assert result.later
    assert (result.proposal.starts_at, result.proposal.ends_at) == ("2026-10-28T17:00", "2026-10-28T19:00")


def test_manual_pick_when_advise_fails_twice():
    _, _, pair = setup()
    result = m.match(request(), pair, client=ScriptedModel("nonsense", answer(chosen_ids=["u_ghost"])))
    assert result.manual and result.proposal is None and result.message == m.MANUAL_MESSAGE
    rows = m.manual_rows(result.candidates, result.model_ranking)
    assert [row["id"] for row in rows] == ["u_a", "u_b"]
    picked = m.match(request(), pair, pick="u_b", client=ScriptedModel())
    assert [p.id for p in picked.proposal.people] == ["u_b"] and picked.proposal.source == "manual"


def test_cached_advice_is_revalidated_against_a_fresh_filter():
    _, _, pair = setup()
    gone = inputs(ME, person("u_a", "Alex Kim", weekly={}), person("u_b", "Bo Wu"))
    stale = answer()
    fresh = answer(chosen_ids=["u_b"], runner_ups=[], ranking=[{"id": "u_b", "why": "still free"}])
    model = ScriptedModel(fresh)
    result = m.match(request(), gone, advice=stale, client=model)
    assert [p.id for p in result.proposal.people] == ["u_b"] and len(model.calls) == 1


# Through the session pipeline (seed data)


TUE = ("CS 61A", "2026-10-27T19:00", "2026-10-27T21:00", "library", "", False, None)
SEED_ANSWER = {
    "no_match": False,
    "chosen_ids": ["u_jordan"],
    "slot": {"starts_at": "2026-10-27T19:00-07:00", "ends_at": "2026-10-27T21:00-07:00"},
    "reason": "Jordan shares CS 61A, is in your year and major, and is free 7-9 PM.",
    "ranking": [{"id": "u_jordan", "why": "same course, year, style"}, {"id": "u_priya", "why": "same course, year, style"}],
    "runner_ups": ["u_priya"],
    "near_tie": True,
    "flags": [],
    "alternative": None,
}


@pytest.fixture
def seed_model(monkeypatch):
    model = ScriptedModel(SEED_ANSWER, repeat=True)
    monkeypatch.setattr(m, "advise_client", lambda: model)
    return model


def test_build_proposal_calls_the_model_once_per_request(seed_model):
    first = m.build_proposal(*TUE)
    store.cycle_person()
    swapped = m.build_proposal(*TUE)
    again = m.build_proposal(*TUE)
    assert len(seed_model.calls) == 1
    assert [p.id for p in first.people] == ["u_jordan"]
    assert [p.id for p in swapped.people] == [p.id for p in again.people] == ["u_priya"]


def test_send_recheck_passes_for_the_approved_slot(seed_model):
    proposal = m.build_proposal(*TUE)
    sent = m.recheck_for_send(*TUE, ["u_jordan"], proposal.starts_at, proposal.ends_at)
    assert [p.id for p in sent.people] == ["u_jordan"]


def test_send_recheck_fails_when_the_person_is_no_longer_free(seed_model, monkeypatch):
    proposal = m.build_proposal(*TUE)
    key = m._request(*TUE)[1]
    monkeypatch.setitem(store._student_weekly, "u_jordan", {})
    from app.scheduling.runtime import get_runtime
    get_runtime().repository.create_record("u_jordan", "exceptions", __import__(
        "app.scheduling.core.schemas", fromlist=["ExceptionInput"]).ExceptionInput(
        start="2026-10-28T02:00Z", end="2026-10-28T04:00Z", kind="unavailable"))
    with pytest.raises(m.StaleProposal):
        m.recheck_for_send(*TUE, ["u_jordan"], proposal.starts_at, proposal.ends_at)
    assert store.cached_advice(key) is None


def test_send_recheck_fails_for_a_slot_outside_free_overlap(seed_model):
    m.build_proposal(*TUE)
    with pytest.raises(m.StaleProposal):
        m.recheck_for_send(*TUE, ["u_jordan"], "2026-10-27T18:00", "2026-10-27T20:00")


def test_send_recheck_fails_for_an_id_not_in_the_filter(seed_model):
    proposal = m.build_proposal(*TUE)
    with pytest.raises(m.StaleProposal):
        m.recheck_for_send(*TUE, ["u_ghost"], proposal.starts_at, proposal.ends_at)


def test_manual_pick_through_the_session(monkeypatch):
    monkeypatch.setattr(m, "advise_client", lambda: ScriptedModel("nope", "still nope", repeat=False))
    with pytest.raises(m.ManualPick) as raised:
        m.build_proposal(*TUE)
    assert raised.value.rows and all(set(row) >= {"id", "first_name", "slot"} for row in raised.value.rows)
    m.choose_manually(*TUE, "u_priya")
    proposal = m.build_proposal(*TUE)
    assert [p.id for p in proposal.people] == ["u_priya"] and proposal.source == "manual"
