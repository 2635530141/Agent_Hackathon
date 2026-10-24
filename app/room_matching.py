"""Rank open rooms for a study request (David).

The board decides which rooms are in the requester's window. Each of those rooms is a
candidate for Advise: the host's public profile (courses, styles, year, major, exams,
bio) plus the room itself (place, fixed slot, open seats, members). The model orders
the rooms and explains why; joining stays the student's choice, and a room never moves
outside the window. When no room is left, the student opens one, as before.
"""

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app import advise as advise_step
from app import matching_prompt, schedule_core, store
from app.profile_core import STYLE_LABELS, eligible_student_ids, public_profile, public_profile_of
from app.scheduling.time import local_iso, parse_instant

log = logging.getLogger(__name__)

ROOM_LIMIT = 12
BIO_LIMIT = 300
MIN_OVERLAP = timedelta(hours=1)
WALK_METERS_PER_MINUTE = 80


@dataclass
class RoomRanking:
    """The model's order for the board. Empty `order` means show the board as it was."""

    order: list[str] = field(default_factory=list)
    why: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    near_tie: bool = False
    flagged: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.order)


def _meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((phi2 - phi1) / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _place(room: dict) -> dict | None:
    return next((place for place in store.places() if place["name"] == room["place_name"]), None)


def _course_code(course_id: str) -> str:
    found = store.course_by_id(course_id)
    return found["code"] if found else course_id


def _styles(profile) -> list[str]:
    styles = [STYLE_LABELS[style].lower() for style in profile.study_styles] or ["no preference"]
    return styles + ([f"other: {profile.study_style_note}"] if profile.study_style_note else [])


def _exams(student_id: str, mine: set[tuple[str, str]], start: datetime) -> list[dict]:
    rows = []
    for exam in schedule_core.list_exams(student_id):
        when = parse_instant(exam.starts_at)
        if when <= start:
            continue
        day = local_iso(when)[:10]
        window = exam.priority_starts_at and parse_instant(exam.priority_starts_at) <= start < when
        rows.append({"course": _course_code(exam.course_id), "date": day, "in_priority_window": bool(window),
                     "same_exam_as_requester": (exam.course_id, day) in mine})
    return rows


def _requester_exams() -> set[tuple[str, str]]:
    return {(exam.course_id, local_iso(parse_instant(exam.starts_at))[:10]) for exam in schedule_core.list_exams()}


def room_facts(room: dict, start: datetime, end: datetime) -> dict | None:
    """One candidate: the host's public facts and the room. Nothing private."""
    host = public_profile_of(room["host_id"])
    if host is None:
        return None
    me = public_profile()
    room_start, room_end = parse_instant(room["starts_at"]), parse_instant(room["ends_at"])
    shared = (max(room_start, start), min(room_end, end))
    place = _place(room)
    trips = {}
    if place:
        trips = {
            "requester": _meters(me.anchor_lat, me.anchor_lng, place["lat"], place["lng"]),
            "candidate": _meters(host.anchor_lat, host.anchor_lng, place["lat"], place["lng"]),
        }
    return {
        "id": room["id"],
        "first_name": host.name.split()[0],
        "major": host.major,
        "year": host.year,
        "courses": [_course_code(course) for course in host.course_ids],
        "shared_courses": [_course_code(course) for course in host.course_ids if course in me.course_ids],
        "style": ", ".join(_styles(host)),
        "exams": _exams(host.id, _requester_exams(), start),
        "free_overlap": [{"starts_at": local_iso(shared[0]), "ends_at": local_iso(shared[1])}] if shared[0] < shared[1] else [],
        "other_free": [],
        "within_radius": {
            "requester": trips["requester"] <= me.radius_meters if trips else None,
            "candidate": trips["candidate"] <= host.radius_meters if trips else None,
        },
        "minutes_away": {key: math.ceil(value / WALK_METERS_PER_MINUTE) for key, value in trips.items()} or None,
        "bio": host.bio[:BIO_LIMIT],
        "room": {
            "course": room["course_code"],
            "place_name": room["place_name"],
            "place_type": room["place_type"],
            "starts_at": local_iso(room_start),
            "ends_at": local_iso(room_end),
            "seats_open": max(0, room["capacity"] - len(room["members"])),
            "members": [{"first_name": m["first_name"], "major": m["major"], "year": m["year"]} for m in room["members"]],
        },
    }


def candidates(rooms: list[dict], user_id: str, starts_at: str, ends_at: str) -> list[dict]:
    """Rooms the requester could join: not theirs, not full, an eligible host, time inside the window."""
    start, end = parse_instant(starts_at), parse_instant(ends_at)
    eligible = set(eligible_student_ids())
    rows = []
    for room in rooms:
        if room["host_id"] == user_id or room.get("joined") or room.get("full") or room["host_id"] not in eligible:
            continue
        facts = room_facts(room, start, end)
        if facts and facts["free_overlap"]:
            rows.append(facts)
    return rows[:ROOM_LIMIT]


def request_facts(course_id: str, starts_at: str, ends_at: str, place_type: str, note: str) -> dict:
    start, end = parse_instant(starts_at), parse_instant(ends_at)
    return {
        "task": "rank the open rooms; chosen_ids is the single best room",
        "course_id": course_id,
        "course_code": _course_code(course_id),
        "window": {"starts_at": local_iso(start), "ends_at": local_iso(end)},
        "min_minutes": int(min(MIN_OVERLAP, end - start).total_seconds() // 60),
        "group_size": 2,
        "place_type": place_type,
        "session_style": None,
        "note": note[:300],
    }


def requester_brief() -> dict:
    me = public_profile()
    return {
        "first_name": me.name.split()[0],
        "major": me.major,
        "year": me.year,
        "courses": [_course_code(course) for course in me.course_ids],
        "style": ", ".join(_styles(me)),
        "group_size": {"min": me.group_min, "max": me.group_max},
        "place_types": me.place_types,
        "place_note": me.place_note,
        "exams": [{"course": c, "date": d} for c, d in sorted(_requester_exams())],
    }


def client() -> advise_step.ModelClient:
    """The model behind room ranking. Tests replace this."""
    return advise_step.OmnaraAdviseClient()


def _key(request: dict, records: list[dict]) -> str:
    seats = [(r["id"], r["room"]["seats_open"]) for r in records]
    raw = json.dumps([request, seats, requester_brief()], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def rank(rooms: list[dict], user_id: str, course_id: str, starts_at: str, ends_at: str, place_type: str, note: str) -> RoomRanking:
    """Ask the model once per request and set of rooms; reuse the answer on refresh."""
    records = candidates(rooms, user_id, starts_at, ends_at)
    if not records:
        return RoomRanking()
    request = request_facts(course_id, starts_at, ends_at, place_type, note)
    key = _key(request, records)
    cached = store.cached_ranking(key)
    if cached is not None:
        return RoomRanking(**{**cached, "flagged": set(cached["flagged"])})
    # A failure is cached too, so a refresh shows the board without calling the model again.
    if matching_prompt.IS_STAND_IN:
        log.warning("Room ranking is using the stand-in matching_prompt.py; add the team's file.")
    result = advise_step.advise(request, requester_brief(), records, client())
    if not result.ok:
        log.info("room ranking failed after %s attempt(s): %s", result.attempts, result.errors)
        store.cache_ranking(key, {**RoomRanking(errors=result.errors).__dict__, "flagged": []})
        return RoomRanking(errors=result.errors)
    output = result.output
    ids = [r["id"] for r in records]
    flagged = {cand_id for cand_id in ids if any(cand_id in str(flag.get("source", "")) for flag in output["flags"])}
    ranked = [row["id"] for row in output["ranking"] if row["id"] in ids]
    order = list(dict.fromkeys(output["chosen_ids"] + ranked + ids))
    order = [i for i in order if i not in flagged] + [i for i in order if i in flagged]
    ranking = RoomRanking(
        order=order,
        why={row["id"]: row.get("why", "") for row in output["ranking"]},
        reason=output["reason"],
        near_tie=output["near_tie"],
        flagged=flagged,
    )
    log.info("room ranking near_tie=%s flags=%s", output["near_tie"], output["flags"])
    if output["flags"]:
        log.warning("INJECTION_ALERT source=room_ranking detail=%s", output["flags"])
    store.cache_ranking(key, {**ranking.__dict__, "flagged": sorted(flagged)})
    return ranking


def apply(rooms: list[dict], ranking: RoomRanking) -> list[dict]:
    """The board in the model's order. Rooms the model did not see (joined, yours) keep their place on top."""
    if not ranking.ok:
        return rooms
    position = {room_id: index for index, room_id in enumerate(ranking.order)}
    unseen = [room for room in rooms if room["id"] not in position]
    seen = sorted((room for room in rooms if room["id"] in position), key=lambda room: position[room["id"]])
    for index, room in enumerate(seen):
        room["match"] = {
            "best": index == 0 and room["id"] not in ranking.flagged,
            "why": ranking.why.get(room["id"], ""),
            "flagged": room["id"] in ranking.flagged,
        }
    return unseen + seen
