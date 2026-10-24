"""Run every evals/matching fixture. Offline it checks the recorded answer and the plumbing;
with STUDYMATE_LIVE_EVALS=1 the same expectations run against the real model."""

import json
import os
from pathlib import Path

import pytest

from app import advise, matching_core as m, matching_prompt
from factories import ScriptedModel, inputs, person, request

pytestmark = pytest.mark.real_advise

FIXTURES = sorted((Path(__file__).resolve().parent.parent / "evals" / "matching").glob("*.json"))
LIVE = os.environ.get("STUDYMATE_LIVE_EVALS") == "1"


def _person(spec: dict, default_id: str = "u_me", default_name: str = "Maya Chen"):
    spec = dict(spec)
    pid, name = spec.pop("id", default_id), spec.pop("name", default_name)
    courses = tuple(spec.pop("courses", ("cs61a",) if pid != "u_me" else ("cs61a", "math54")))
    weekly = spec.pop("weekly", None)
    busy = [tuple(row) for row in spec.pop("busy", [])]
    exams = spec.pop("exams", [])
    return person(pid, name, courses=courses, weekly={day: tuple(v) for day, v in weekly.items()} if weekly else None,
                  busy=busy, exams=exams, **spec)


def _load(path: Path):
    case = json.loads(path.read_text())
    me = _person(case["requester"])
    people = [_person(spec) for spec in case["people"]]
    return case, me, inputs(me, *people), request(**case["request"])


@pytest.mark.parametrize("path", FIXTURES, ids=[path.stem for path in FIXTURES])
def test_fixture(path, monkeypatch):
    case, me, pair, req = _load(path)
    expect = case["expect"]
    preview = m.filter_preview(req, pair)
    in_window = {cand.person.profile.id for cand in preview.candidates if cand.free}
    for cand_id in expect.get("filter_excludes", []):
        assert cand_id not in in_window, f"{case['id']}: {cand_id} should have no time in the window"
    for cand_id in expect.get("filter_includes", []):
        assert cand_id in in_window, f"{case['id']}: {cand_id} should be free in the window"
    for cand_id, windows in expect.get("free_overlap", {}).items():
        facts = next(c.facts for c in preview.candidates if c.person.profile.id == cand_id)
        assert facts["free_overlap"] == windows

    if LIVE:
        client = advise.OmnaraAdviseClient()
    else:
        records = [cand.facts for cand in preview.candidates]
        errors = matching_prompt.validate_match(case["recorded_output"], m.request_facts(preview.request, me), records)
        assert errors == [], f"{case['id']}: recorded answer is invalid: {errors}"
        client = ScriptedModel(case["recorded_output"])

    result = m.match(req, pair, client=client)
    assert not result.manual, f"{case['id']}: Advise failed: {result.errors}"
    advice = result.advice
    chosen = advice["alternative"]["ids"] if advice["no_match"] else advice["chosen_ids"]
    if "chosen" in expect:
        assert chosen == expect["chosen"]
    if "chosen_one_of" in expect:
        assert chosen and set(chosen) <= set(expect["chosen_one_of"])
    for cand_id in expect.get("not_chosen", []):
        assert cand_id not in chosen
    if "no_match" in expect:
        assert advice["no_match"] is expect["no_match"]
    if expect.get("alternative_in_other_free"):
        assert result.later and advice["alternative"]
    if "near_tie" in expect:
        assert advice["near_tie"] is expect["near_tie"]
    if expect.get("runner_up_set"):
        assert advice["runner_ups"]
    sources = " ".join(str(flag.get("source", "")) for flag in advice["flags"])
    for cand_id in expect.get("flagged", []):
        assert cand_id in sources, f"{case['id']}: {cand_id} should be flagged: {advice['flags']}"
    reason = advice["reason"].lower()
    for phrase in expect.get("reason_mentions", []):
        assert phrase.lower() in reason, f"{case['id']}: reason should mention {phrase!r}: {advice['reason']}"
    if "reason_mentions_any" in expect:
        assert any(phrase in reason for phrase in expect["reason_mentions_any"])
    if "place_kind" in expect:
        assert result.proposal.place.kind == expect["place_kind"]
    assert result.proposal.flags == advice["flags"]
