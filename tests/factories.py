"""Builders for matching inputs, so each test states only what it is about."""

from datetime import timedelta

from app import matching_core as m
from app.contracts import Exam, MatchBusyInterval, PublicProfile, StudyRequest

CAMPUS = (37.8719, -122.2585)
EVENINGS = {day: ("17:00", "22:00") for day in ("mon", "tue", "wed", "thu", "fri")}


def person(pid, name, courses=("cs61a",), weekly=None, busy=(), exams=(), **fields):
    """busy items are (source, starts_at, ends_at) or (source, starts_at, ends_at, kind)."""
    profile = {
        "id": pid,
        "name": name,
        "study_style": "quiet",
        "radius_meters": 1500,
        "anchor": "campus",
        "anchor_lat": CAMPUS[0],
        "anchor_lng": CAMPUS[1],
        "group_size": 2,
        "place_types": ["coffee", "library"],
        "major": "EECS",
        "year": 3,
        "course_ids": list(courses),
        **fields,
    }
    blocks = []
    for i, block in enumerate(busy):
        source, starts_at, ends_at, *kind = block
        blocks.append(
            MatchBusyInterval(id=f"b{i}", source=source, starts_at=starts_at, ends_at=ends_at, kind=kind[0] if kind else None)
        )
    return m.Person(
        PublicProfile.model_validate(profile),
        dict(EVENINGS if weekly is None else weekly),
        blocks,
        [Exam(**exam) for exam in exams],
    )


def inputs(me, *people, eligible=None):
    ids = [p.profile.id for p in people] if eligible is None else list(eligible)
    return m.Inputs(me=me, people=list(people), eligible_ids=ids)


def request(**fields):
    base = {
        "course_id": "cs61a",
        "starts_at": "2026-10-27T19:00",
        "ends_at": "2026-10-27T21:00",
        "place_type": "library",
    }
    return StudyRequest(**{**base, **fields})


def at(value: str):
    return m._parse(value)


ME = person("u_me", "Maya Chen", courses=("cs61a", "math54"))


# Model stand-ins. Tests never call Omnara.


class ScriptedModel:
    """Returns the given outputs in order and records every call Advise made."""

    def __init__(self, *outputs, repeat=False):
        self.outputs = list(outputs)
        self.repeat = repeat
        self.calls = []

    def complete(self, system, user, schema, temperature):
        self.calls.append({"system": system, "user": user, "schema": schema, "temperature": temperature})
        output = self.outputs[0] if self.repeat else self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def _common(windows_a, windows_b):
    out = []
    for a in windows_a:
        for b in windows_b:
            start = max(m._parse(a["starts_at"]), m._parse(b["starts_at"]))
            end = min(m._parse(a["ends_at"]), m._parse(b["ends_at"]))
            if start < end:
                out.append({"starts_at": m.local_iso(start), "ends_at": m.local_iso(end)})
    return out


def first_free_advice(request, requester, candidates, client=None):
    """Default test policy in place of the model: the Filter order is the ranking.

    It is not a scorer; it lets tests that are not about Advise run the full pipeline."""
    from app.advise import Advice

    minimum = timedelta(minutes=request["min_minutes"])
    ranking = [{"id": cand["id"], "why": "free in your window"} for cand in candidates]
    base = {"ranking": ranking, "near_tie": False, "flags": [], "reason": "Picked by the test policy."}
    if not any(cand["free_overlap"] for cand in candidates):
        first = next(cand for cand in candidates if cand["other_free"])
        window = first["other_free"][0]
        return Advice({**base, "no_match": True, "chosen_ids": [], "slot": None, "runner_ups": [],
                       "alternative": {"ids": [first["id"]], **window}})
    chosen, common = [], None
    for cand in candidates:
        trial = cand["free_overlap"] if common is None else _common(common, cand["free_overlap"])
        trial = [w for w in trial if m._parse(w["ends_at"]) - m._parse(w["starts_at"]) >= minimum]
        if trial:
            chosen.append(cand["id"])
            common = trial
            if len(chosen) == request["group_size"] - 1:
                break
    slot = max(common, key=lambda w: m._parse(w["ends_at"]) - m._parse(w["starts_at"]))
    others = [cand["id"] for cand in candidates if cand["id"] not in chosen]
    return Advice({**base, "no_match": False, "chosen_ids": chosen, "slot": slot, "runner_ups": others[:1],
                   "alternative": None})
