"""Matching and places track (David).

Filter -> Advise. The only hard constraint in code is time: a candidate must be
free inside the requested window. Everything else (course, style, distance,
exams, the note) goes to the model as facts, and Advise chooses among the ids the
Filter returned. It may only use PublicProfile, eligible ids, and busy intervals
without titles. It must not read the private profile or call Google Calendar.
"""

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from app import advise as advise_step
from app import matching_prompt
from app.contracts import Exam, MatchBusyInterval, Proposal, ProposalPerson, ProposalPlace, PublicProfile, RankedCandidate, StudyRequest
from app.format import pretty_range
from app.profile_core import PLACE_LABELS, STYLE_LABELS, eligible_student_ids, public_profile, public_profile_of
from app.schedule_core import BLOCKING_SOURCES, list_busy_for_match, list_exams, weekly_hours
from app.store import (
    cache_advice,
    cached_advice,
    clear_advice,
    courses,
    generated_drafts,
    invite_text,
    manual_pick,
    places,
    remember_draft,
    set_manual_pick,
    swaps_for,
)
from app.schedule_core import matching_free_time
from app.scheduling.time import parse_instant, local_input, local_iso, display_zone
from app.scheduling.fixture_math import free_time as fixture_free_time
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

Interval = tuple[datetime, datetime]

FILTER_LIMIT = 12
OTHER_FREE_LIMIT = 3
BIO_LIMIT = 300
NOTE_LIMIT = 300
MIN_OVERLAP = timedelta(minutes=60)
SEARCH_DAYS = 14
WALK_METERS_PER_MINUTE = 80
TOP_N = 5
PLACE_NOUNS = {"coffee": "a coffee shop", "library": "a library", "outdoor": "an outdoor spot"}
SESSION_STYLES = {"quiet": "quiet focus", "talk": "talk it through", "drill": "drill practice questions"}
MANUAL_MESSAGE = "The matching assistant could not produce a valid proposal. Pick someone from the list."


class NoMatch(Exception):
    """Nothing to propose. The message is safe to show the organizer."""


class StaleProposal(NoMatch):
    """Send re-check failed. The cached Advise answer is dropped and Advise runs again."""


class ManualPick(NoMatch):
    """Advise failed twice or is unavailable. The page shows the Filter list to pick from."""

    def __init__(self, message: str, rows: list[dict]):
        super().__init__(message)
        self.rows = rows


@dataclass
class Person:
    profile: PublicProfile
    weekly: dict[str, tuple[str, str]]
    busy: list[MatchBusyInterval]
    exams: list[Exam]
    schedule_reader: object | None = None

    @property
    def first_name(self) -> str:
        return self.profile.name.split()[0]


@dataclass
class Inputs:
    me: Person
    people: list[Person]
    eligible_ids: list[str]


@dataclass
class Candidate:
    """A Filter result: free time with the requester, plus facts for the model."""

    person: Person
    free: list[Interval]
    other_free: list[Interval]
    facts: dict


@dataclass
class NoteReading:
    place_type: str
    avoid: set[str] = field(default_factory=set)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    used: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    request: StudyRequest
    candidates: list[Candidate]
    funnel: list[tuple[str, int]]
    later: bool = False
    proposal: Proposal | None = None
    message: str = ""
    advice: dict | None = None
    manual: bool = False
    model_ranking: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _parse(value: str) -> datetime:
    return parse_instant(value)


def _iso(value: datetime) -> str:
    return local_input(value)


# Intervals


def _merge(intervals: list[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract(intervals: list[Interval], blocks: list[Interval]) -> list[Interval]:
    pieces = list(intervals)
    for block_start, block_end in blocks:
        kept = []
        for start, end in pieces:
            if block_end <= start or block_start >= end:
                kept.append((start, end))
                continue
            if start < block_start:
                kept.append((start, block_start))
            if block_end < end:
                kept.append((block_end, end))
        pieces = kept
    return _merge(pieces)


def _intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out = []
    i = j = 0
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if start < end:
            out.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _total(intervals: list[Interval]) -> timedelta:
    return sum((end - start for start, end in intervals), timedelta())


def _longest(intervals: list[Interval]) -> Interval | None:
    return max(intervals, key=lambda item: item[1] - item[0], default=None)


def _blocks(person: Person) -> list[Interval]:
    blocks = [
        (_parse(block.starts_at), _parse(block.ends_at))
        for block in person.busy
        if block.source in BLOCKING_SOURCES and block.kind != "free"
    ]
    blocks += [(_parse(exam.starts_at), _parse(exam.ends_at)) for exam in person.exams]
    return _merge(blocks)


def free_time(person: Person, start: datetime, end: datetime) -> list[Interval]:
    """Weekly hours plus free one-offs, minus exams, calendar events, and busy one-offs."""
    if person.schedule_reader is not None:
        return person.schedule_reader(start, end)
    return fixture_free_time(person.weekly, person.busy, person.exams, start, end)


# Distance


def _meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lng2 - lng1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def _between(a: PublicProfile, b: PublicProfile) -> float:
    return _meters(a.anchor_lat, a.anchor_lng, b.anchor_lat, b.anchor_lng)


def injection_alert(source: str, detail) -> None:
    """The team's injection alert: one WARNING line per detection, picked up by monitoring."""
    log.warning("INJECTION_ALERT source=%s detail=%s", source, detail)


# Note


PLACE_WORDS = {
    "library": r"librar(?:y|ies)",
    "coffee": r"coffee(?: shops?)?|caf[eé]s?",
    "outdoor": r"outdoors?|outside|parks?",
}
NEGATION = r"\b(?:not|no|avoid|never|without|anything but|don'?t want)\b(?:\W+\w+){0,2}?\W+"
TALK = re.compile(r"\b(?:talk|talking|chat|discuss|out loud)\b")
QUIET = re.compile(r"\b(?:quiet|silent|silence)\b")
BOOKING = re.compile(r"\b(?:book|reserve|reservation)\b")
INJECTION = re.compile(r"ignore (?:all |any |your |previous |prior )*(?:instructions|rules)|[\w.+-]+@[\w-]+\.\w+")
WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tues": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thurs": 3, "thur": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
WEEKDAY = re.compile(r"\b(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b")
TIME_RANGE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|–|to|until|till)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b"
)
TIME_CUE = re.compile(r"\b(?:at|from|between|instead|time|move)\b")
TIME_POINT = re.compile(r"\b(?:at|from|around)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b|\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b")


def _clock(hour: str, minute: str | None, meridiem: str | None) -> time | None:
    h, m = int(hour), int(minute or 0)
    if meridiem == "pm" and h < 12:
        h += 12
    elif meridiem == "am" and h == 12:
        h = 0
    elif meridiem is None and 1 <= h <= 7:
        h += 12
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return time(h, m)


def _note_time(text: str, start: datetime, end: datetime) -> tuple[datetime, datetime] | None:
    original_start, original_end = start, end
    start = start.astimezone(ZoneInfo(display_zone()))
    end = end.astimezone(ZoneInfo(display_zone()))
    day = start.date()
    weekday = WEEKDAY.search(text)
    if weekday:
        day += timedelta(days=(WEEKDAYS[weekday.group(1)] - start.weekday()) % 7)
    new_start, new_end = datetime.combine(day, start.time()), datetime.combine(day, end.time())
    found = TIME_RANGE.search(text)
    if found and not (found.group(2) or found.group(3) or found.group(5) or found.group(6) or weekday or TIME_CUE.search(text)):
        found = None
    if found:
        h1, m1, mer1, h2, m2, mer2 = found.groups()
        first = _clock(h1, m1, mer1 or mer2)
        second = _clock(h2, m2, mer2 or ("pm" if first and first.hour >= 12 and not mer1 else None))
        if first and second and first >= second and not mer1:
            first = _clock(h1, m1, "am")
        if first and second and first < second:
            new_start, new_end = datetime.combine(day, first), datetime.combine(day, second)
    else:
        point = TIME_POINT.search(text)
        if point:
            hour, minute, meridiem = point.groups()[:3] if point.group(1) else point.groups()[3:]
            at = _clock(hour, minute, meridiem)
            if at:
                new_start = datetime.combine(day, at)
                new_end = new_start + (original_end - original_start)
    new_start, new_end = parse_instant(new_start), parse_instant(new_end)
    if (new_start, new_end) == (original_start, original_end):
        return None
    return new_start, new_end


def read_note(note: str, place_type: str, preferred: list[str], start: datetime, end: datetime) -> NoteReading:
    """Rules, not a model. Only place type and time come out of the note."""
    text = note[:NOTE_LIMIT].lower()
    reading = NoteReading(place_type=place_type)
    if not text.strip():
        return reading
    if INJECTION.search(text):
        injection_alert("note", "the note contains an instruction or an address; only place and time were read")
    wanted = []
    for kind, pattern in PLACE_WORDS.items():
        if re.search(NEGATION + rf"(?:{pattern})\b", text):
            reading.avoid.add(kind)
        elif re.search(rf"\b(?:{pattern})\b", text):
            wanted.append(kind)
    talk = bool(TALK.search(text))
    if talk:
        reading.avoid.add("library")
    elif QUIET.search(text) and "library" not in reading.avoid:
        wanted.append("library")
    wanted = [kind for kind in wanted if kind not in reading.avoid]
    if wanted:
        reading.place_type = wanted[0]
    elif place_type in reading.avoid:
        order = ["coffee", "outdoor", "library"] if talk else [*preferred, "coffee", "library", "outdoor"]
        reading.place_type = next(kind for kind in order if kind not in reading.avoid)
    if reading.place_type != place_type:
        label = PLACE_NOUNS[reading.place_type]
        if reading.avoid:
            ruled_out = " or ".join(PLACE_NOUNS[kind] for kind in sorted(reading.avoid))
            reading.used.append(f"Your note ruled out {ruled_out}, so this looks for {label}.")
        else:
            reading.used.append(f"Your note asked for {label}, so the form's place type was replaced.")
    moved = _note_time(text, start, end)
    if moved:
        reading.starts_at, reading.ends_at = moved
        reading.used.append(f"Your note moved the time to {pretty_range(_iso(moved[0]), _iso(moved[1]))}.")
    if BOOKING.search(text):
        reading.used.append("Booking is not available yet, so nothing is reserved.")
    return reading


# Filter


def _course_code(course_id: str) -> str:
    return next((course["code"] for course in courses() if course["id"] == course_id), course_id)


def _window_rows(intervals: list[Interval]) -> list[dict]:
    return [{"starts_at": local_iso(start), "ends_at": local_iso(end)} for start, end in intervals]


def _minutes(meters: float) -> int:
    return math.ceil(meters / WALK_METERS_PER_MINUTE)


def _exam_rows(me: Person, other: Person, start: datetime) -> list[dict]:
    zone = ZoneInfo(display_zone())
    mine = {(exam.course_id, _parse(exam.starts_at).astimezone(zone).date()) for exam in me.exams}
    rows = []
    for exam in other.exams:
        when = _parse(exam.starts_at)
        if when <= start:
            continue
        window = exam.priority_starts_at and _parse(exam.priority_starts_at) <= start < when
        rows.append({
            "course": _course_code(exam.course_id),
            "date": when.astimezone(zone).date().isoformat(),
            "in_priority_window": bool(window),
            "same_exam_as_requester": (exam.course_id, when.astimezone(zone).date()) in mine,
        })
    return rows


def candidate_facts(me: Person, other: Person, start: datetime, free: list[Interval], other_free: list[Interval]) -> dict:
    """Exactly the fields build_matching_message() documents. Nothing private."""
    distance = _between(me.profile, other.profile)
    minutes = _minutes(distance)
    mine = set(me.profile.course_ids)
    return {
        "id": other.profile.id,
        "first_name": other.first_name,
        "major": other.profile.major,
        "year": other.profile.year,
        "courses": [_course_code(course) for course in other.profile.course_ids],
        "shared_courses": [_course_code(course) for course in other.profile.course_ids if course in mine],
        "style": SESSION_STYLES[other.profile.study_style],
        "exams": _exam_rows(me, other, start),
        "free_overlap": _window_rows(free),
        "other_free": _window_rows(other_free),
        "within_radius": {
            "requester": distance <= me.profile.radius_meters,
            "candidate": distance <= other.profile.radius_meters,
        },
        "minutes_away": {"requester": minutes, "candidate": minutes},
        "bio": other.profile.bio[:BIO_LIMIT],
    }


def _own_window(me: Person, request: StudyRequest, start: datetime, end: datetime) -> list[Interval]:
    mine = free_time(me, start, end)
    if request.study_anyway:
        own = [
            (_parse(block.starts_at), _parse(block.ends_at))
            for block in me.busy
            if block.source in ("calendar", "exception") and block.kind != "free"
        ]
        mine = _merge(mine + _intersect(_merge(own), [(start, end)]))
    return mine


def find_matches(request: StudyRequest, inputs: Inputs, duration_minutes: int | None = None) -> tuple[list[Candidate], list[tuple[str, int]], bool, str]:
    """The time Filter. The only hard constraint: free together inside the requested window.

    Weekly hours, one-off blocks, exams, and calendar intervals all count, and the
    other person's calendar has no override. Course, radius, style, and year are
    facts on each record, never filters. Gender preference and age were applied
    by the profile service when it produced eligible_ids.
    Returns up to FILTER_LIMIT candidates ordered by overlap length, the funnel,
    whether they only have later windows (nobody is free in the requested one),
    and a message when there is nobody.
    """
    me = inputs.me
    start, end = _parse(request.starts_at), _parse(request.ends_at)
    min_len = timedelta(minutes=duration_minutes) if duration_minutes is not None else min(MIN_OVERLAP, end - start)
    if min_len <= timedelta(0) or min_len > end - start:
        raise ValueError("Duration must fit inside the study window.")

    eligible = set(inputs.eligible_ids)
    pool = [person for person in inputs.people if person.profile.id in eligible and person.profile.id != me.profile.id]
    mine = _own_window(me, request, start, end)
    horizon = start + timedelta(days=SEARCH_DAYS)
    mine_later = free_time(me, start, horizon)
    found = []
    for person in pool:
        free = [(s, e) for s, e in _intersect(mine, free_time(person, start, end)) if e - s >= min_len]
        later = [
            (s, e)
            for s, e in _intersect(mine_later, free_time(person, start, horizon))
            if e - s >= min_len and not (s < end and e > start)
        ][:OTHER_FREE_LIMIT]
        found.append(Candidate(person, free, later, candidate_facts(me, person, start, free, later)))

    free_now = [cand for cand in found if cand.free]
    funnel = [("eligible", len(pool)), ("free in your window", len(free_now))]
    if free_now:
        free_now.sort(key=lambda cand: (-_total(cand.free), cand.person.profile.id))
        return free_now[:FILTER_LIMIT], funnel, False, ""
    if not pool:
        return [], funnel, False, "Nobody else is eligible to match yet."
    later = [cand for cand in found if cand.other_free]
    if not later:
        return [], funnel, False, "Nobody is free then, or in the next two weeks."
    later.sort(key=lambda cand: (cand.other_free[0][0], cand.person.profile.id))
    return later[:FILTER_LIMIT], funnel, True, ""


def request_facts(request: StudyRequest, me: Person) -> dict:
    start, end = _parse(request.starts_at), _parse(request.ends_at)
    return {
        "course_id": request.course_id,
        "course_code": _course_code(request.course_id),
        "window": {"starts_at": local_iso(start), "ends_at": local_iso(end)},
        "min_minutes": int(min(MIN_OVERLAP, end - start).total_seconds() // 60),
        "group_size": me.profile.group_size,
        "place_type": request.place_type,
        "session_style": request.session_style,
        "note": request.note[:NOTE_LIMIT],
    }


def requester_brief(me: Person) -> dict:
    """get_profile's snapshot: public fields only, no coordinates or private data."""
    return {
        "first_name": me.first_name,
        "major": me.profile.major,
        "year": me.profile.year,
        "courses": [_course_code(course) for course in me.profile.course_ids],
        "style": SESSION_STYLES[me.profile.study_style],
        "group_size": me.profile.group_size,
        "place_types": me.profile.place_types,
        "exams": [
            {"course": _course_code(exam.course_id), "date": _parse(exam.starts_at).astimezone(ZoneInfo(display_zone())).date().isoformat()}
            for exam in me.exams
        ],
    }


def pick_group(ordered: list[Candidate], needed: int, min_len: timedelta, earliest: bool) -> tuple[list[Candidate], Interval | None]:
    """Greedy over a given order: add the next person while everyone still shares a long enough window."""
    group: list[Candidate] = []
    common: list[Interval] | None = None
    for cand in ordered:
        windows = cand.other_free if earliest else cand.free
        trial = windows if common is None else _intersect(common, windows)
        trial = [(s, e) for s, e in trial if e - s >= min_len]
        if trial:
            group.append(cand)
            common = trial
            if len(group) == needed:
                break
    if not common:
        return group, None
    return group, common[0] if earliest else _longest(common)


# Places


def _is_open(place: dict, start: datetime, end: datetime) -> bool:
    start, end = start.astimezone(ZoneInfo(display_zone())), end.astimezone(ZoneInfo(display_zone()))
    if "opens" not in place:
        return True
    if start.date() != end.date():
        return False
    return time.fromisoformat(place["opens"]) <= start.time() and end.time() <= time.fromisoformat(place["closes"])


def suggest_places(
    place_type: str,
    people: list[PublicProfile],
    start: datetime,
    end: datetime,
    avoid: set[str] | None = None,
    preferred: list[str] | None = None,
) -> list[dict]:
    """Stub for Google Places: the seeded list, ranked on type, radius, hours, and rating."""
    avoid = avoid or set()
    preferred = preferred or []
    rows = []
    for place in places():
        trips = [_meters(p.anchor_lat, p.anchor_lng, place["lat"], place["lng"]) for p in people]
        inside = all(trip <= p.radius_meters for trip, p in zip(trips, people))
        rows.append(
            {
                **place,
                "inside": inside,
                "open": _is_open(place, start, end),
                "walk_minutes": math.ceil(max(trips) / WALK_METERS_PER_MINUTE),
            }
        )
    rows.sort(
        key=lambda row: (
            row["kind"] in avoid,
            not row["inside"],
            not row["open"],
            row["kind"] != place_type,
            row["kind"] not in preferred,
            -float(row["rating"]),
            row["walk_minutes"],
        )
    )
    return rows[:TOP_N]


# Copy


def _names(people: list[Person]) -> str:
    names = [person.first_name for person in people]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _place_lines(request: StudyRequest, place: dict, plural: bool) -> list[str]:
    hours = place["hours"][:1].lower() + place["hours"][1:]
    minutes = place["walk_minutes"]
    lines = [
        f"{place['name']} is {PLACE_NOUNS[place['kind']]}, {hours}, rating {place['rating']}, "
        f"within {minutes} minute{'' if minutes == 1 else 's'} of {'all' if plural else 'both'} of you."
    ]
    if place["kind"] != request.place_type:
        lines.append(f"No {PLACE_NOUNS[request.place_type].split(' ', 1)[1]} fits, so this is the closest match.")
    if not place["inside"]:
        lines.append("Nothing fits inside every travel radius, so this is the nearest option.")
    if not place["open"]:
        lines.append("Nothing on the list is open for the whole session. Check the hours.")
    return lines


def _invite(request: StudyRequest, people: list[Person], window: Interval, place: dict) -> str:
    code = _course_code(request.course_id)
    when = pretty_range(_iso(window[0]), _iso(window[1]))
    return (
        f"Hi {_names(people)}! I'm looking for someone to study {code} with. "
        f"Want to meet at {place['name']} ({place['address']}) on {when}? Let me know if that works."
    )


# Entry points


def load_inputs() -> Inputs:
    me = Person(public_profile(), weekly_hours(), list_busy_for_match(), list_exams(),
                lambda start, end: matching_free_time(public_profile().id, start, end))
    eligible = eligible_student_ids()
    people = []
    for student_id in eligible:
        profile = public_profile_of(student_id)
        if profile:
            people.append(
                Person(profile, weekly_hours(student_id), list_busy_for_match(student_id), list_exams(student_id),
                       lambda start, end, sid=student_id: matching_free_time(sid, start, end))
            )
    return Inputs(me=me, people=people, eligible_ids=eligible)


def advise_client() -> advise_step.ModelClient:
    """The model behind Advise. Tests replace this."""
    return advise_step.OmnaraAdviseClient()


def manual_rows(candidates: list[Candidate], model_ranking: list[dict]) -> list[dict]:
    """The Filter list for the manual pick, in the model's order when it parsed."""
    order = [row["id"] for row in model_ranking]
    why = {row["id"]: row.get("why", "") for row in model_ranking}
    ranked = sorted(candidates, key=lambda cand: (order.index(cand.person.profile.id) if cand.person.profile.id in order else len(order)))
    rows = []
    for cand in ranked:
        window = cand.other_free[0] if not cand.free and cand.other_free else _longest(cand.free)
        rows.append({
            "id": cand.person.profile.id,
            "first_name": cand.person.first_name,
            "major": cand.person.profile.major,
            "year": cand.person.profile.year,
            "shared_courses": cand.facts["shared_courses"],
            "slot": pretty_range(_iso(window[0]), _iso(window[1])) if window else "",
            "why": why.get(cand.person.profile.id, ""),
        })
    return rows


def _log_advice(output: dict) -> None:
    log.info("advise proposal near_tie=%s flags=%s", output.get("near_tie"), output.get("flags"))
    if output.get("flags"):
        injection_alert("advise", output["flags"])


def _prepare(request: StudyRequest, me: Person) -> tuple[StudyRequest, NoteReading, datetime, datetime]:
    """Check the times and apply the note's place and time, before the Filter runs."""
    try:
        start, end = _parse(request.starts_at), _parse(request.ends_at)
    except ValueError:
        raise NoMatch("Enter valid local times and resolve any DST ambiguity.") from None
    if end <= start:
        raise NoMatch("The session has to end after it starts.")
    try:
        note = read_note(request.note, request.place_type, me.profile.place_types, start, end)
    except ValueError:
        raise NoMatch("The note resolves to an invalid or ambiguous local time. Choose an explicit time.") from None
    request = request.model_copy(update={"place_type": note.place_type, "note": request.note[:NOTE_LIMIT]})
    if note.starts_at and note.ends_at:
        request = request.model_copy(update={"starts_at": _iso(note.starts_at), "ends_at": _iso(note.ends_at)})
        start, end = note.starts_at, note.ends_at
        if _intersect([(start, end)], _blocks(me)):
            raise NoMatch(
                f"Your note moves the session to {pretty_range(request.starts_at, request.ends_at)}, "
                "which overlaps your own busy time. Pick another time."
            )
    return request, note, start, end


def filter_preview(request: StudyRequest, inputs: Inputs | None = None) -> MatchResult:
    """The Filter alone, for the /matching debug page. No model call."""
    inputs = inputs or load_inputs()
    request, _, _, _ = _prepare(request, inputs.me)
    candidates, funnel, later, message = find_matches(request, inputs)
    return MatchResult(request=request, candidates=candidates, funnel=funnel, later=later, message=message)


def match(
    request: StudyRequest,
    inputs: Inputs | None = None,
    swap_person: int = 0,
    swap_place: int = 0,
    client: advise_step.ModelClient | None = None,
    advice: dict | None = None,
    pick: str | None = None,
) -> MatchResult:
    """Read the note, run the time Filter, Advise (or reuse `advice`), then place and invite."""
    if matching_prompt.IS_STAND_IN:
        log.warning("Advise is using the stand-in matching_prompt.py; add the team's file.")
    inputs = inputs or load_inputs()
    me = inputs.me
    request, note, start, end = _prepare(request, me)
    candidates, funnel, later, message = find_matches(request, inputs)
    if not candidates:
        return MatchResult(request=request, candidates=[], funnel=funnel, message=message)
    facts = request_facts(request, me)
    records = [cand.facts for cand in candidates]
    by_id = {cand.person.profile.id: cand for cand in candidates}
    needed = me.profile.group_size - 1
    min_len = min(MIN_OVERLAP, end - start)

    if pick:
        if pick not in by_id:
            raise StaleProposal("That student is no longer free then. Pick again.")
        order = [by_id[pick]] + [cand for cand in candidates if cand.person.profile.id != pick]
        group, window = pick_group(order, needed, min_len, earliest=later)
        output = {"no_match": later, "chosen_ids": [], "ranking": [], "runner_ups": [], "near_tie": False, "flags": [],
                  "reason": f"You picked {by_id[pick].person.first_name} from the list."}
        source = "manual"
    else:
        if advice is not None and matching_prompt.validate_match(advice, facts, records):
            advice = None
        if advice is None:
            result = advise_step.advise(facts, requester_brief(me), records, client or advise_client())
            if not result.ok:
                log.info("advise failed after %s attempt(s): %s", result.attempts, result.errors)
                return MatchResult(request=request, candidates=candidates, funnel=funnel, later=later,
                                   message=MANUAL_MESSAGE, manual=True, model_ranking=result.ranking, errors=result.errors)
            advice = result.output
            _log_advice(advice)
        output = advice
        source = "model"
        ranked_ids = [row["id"] for row in output["ranking"] if row["id"] in by_id]
        chosen = output["alternative"]["ids"] if output["no_match"] else output["chosen_ids"]
        order_ids = list(dict.fromkeys(chosen + ranked_ids + [cand.person.profile.id for cand in candidates]))
        shift = swap_person % len(order_ids)
        if shift == 0:
            group = [by_id[cand_id] for cand_id in chosen]
            slot = output["alternative"] if output["no_match"] else output["slot"]
            window = (_parse(slot["starts_at"]), _parse(slot["ends_at"]))
        else:
            rotated = order_ids[shift:] + order_ids[:shift]
            group, window = pick_group([by_id[cand_id] for cand_id in rotated], needed, min_len, earliest=output["no_match"])
        later = output["no_match"]

    if not group or window is None:
        return MatchResult(request=request, candidates=candidates, funnel=funnel, later=later, message=MANUAL_MESSAGE,
                           manual=True, model_ranking=output.get("ranking", []))
    if later:
        window = (window[0], min(window[1], window[0] + (end - start)))
    notes = list(note.used)
    if len(group) < needed:
        notes.append(f"Only {len(group)} other {'person fits' if len(group) == 1 else 'people fit'}, so this is a group of {len(group) + 1}.")

    profiles = [me.profile] + [cand.person.profile for cand in group]
    options = suggest_places(request.place_type, profiles, window[0], window[1], note.avoid, me.profile.place_types)
    place = options[swap_place % len(options)]

    lead = group[0].person.profile.id
    why = {row["id"]: row.get("why", "") for row in output["ranking"]}
    chosen_now = [cand.person.profile.id for cand in group]
    model_pick = source == "model" and chosen_now == (output["alternative"]["ids"] if output["no_match"] else output["chosen_ids"])
    if model_pick:
        reason = output["reason"]
    elif source == "model":
        reason = f"You swapped to {_names([cand.person for cand in group])}. {why.get(lead, '')}".strip()
    else:
        reason = output["reason"]
    when = pretty_range(_iso(window[0]), _iso(window[1]))
    lines = [reason, f"Free {when}."] if not model_pick else [reason]
    lines += _place_lines(request, place, len(group) > 1) + notes

    first = {cand.person.profile.id: cand.person.first_name for cand in candidates}
    proposal = Proposal(
        people=[
            ProposalPerson(
                id=cand.person.profile.id,
                first_name=cand.person.first_name,
                year=cand.person.profile.year,
                major=cand.person.profile.major,
                course_code=_course_code(request.course_id),
            )
            for cand in group
        ],
        starts_at=_iso(window[0]),
        ends_at=_iso(window[1]),
        place=ProposalPlace(
            name=place["name"],
            address=place["address"],
            kind=place["kind"],
            hours=place["hours"],
            rating=place["rating"],
            map_url=place["map_url"],
        ),
        reason=" ".join(line for line in lines if line),
        invite=_invite(request, [cand.person for cand in group], window, place),
        ranking=[RankedCandidate(id=row["id"], first_name=first[row["id"]], why=row.get("why", ""))
                 for row in output["ranking"] if row["id"] in first],
        runner_ups=output["runner_ups"],
        near_tie=output["near_tie"],
        flags=output["flags"],
        source=source,
    )
    return MatchResult(request=request, candidates=candidates, funnel=funnel, later=later, proposal=proposal,
                       advice=output if source == "model" else None)


def _request(course_code: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: bool,
             session_style: str | None) -> tuple[StudyRequest, str]:
    course_id = next((course["id"] for course in courses() if course["code"] == course_code), course_code)
    if place_type not in PLACE_LABELS:
        place_type = public_profile().place_types[0]
    if session_style not in SESSION_STYLES.values():
        session_style = None
    request = StudyRequest(
        course_id=course_id,
        starts_at=starts_at,
        ends_at=ends_at,
        place_type=place_type,
        note=note,
        study_anyway=study_anyway,
        session_style=session_style,
    )
    key = "|".join([course_id, starts_at, ends_at, place_type, note, str(study_anyway), session_style or ""])
    return request, key


def build_proposal(
    course_code: str,
    starts_at: str,
    ends_at: str,
    place_type: str,
    note: str,
    study_anyway: bool = False,
    session_style: str | None = None,
) -> Proposal:
    """Session page entry point. Raises NoMatch, or ManualPick with the list to pick from."""
    request, key = _request(course_code, starts_at, ends_at, place_type, note, study_anyway, session_style)
    swap_person, swap_place = swaps_for(key)
    result = match(request, swap_person=swap_person, swap_place=swap_place, advice=cached_advice(key), pick=manual_pick(key))
    if result.manual:
        raise ManualPick(result.message, manual_rows(result.candidates, result.model_ranking))
    if result.proposal is None:
        raise NoMatch(result.message)
    if result.advice is not None:
        cache_advice(key, result.advice)
    proposal = result.proposal
    saved = invite_text().strip()
    drafts = generated_drafts(key)
    if drafts and saved and saved not in drafts:
        return proposal.model_copy(update={"invite": saved})
    remember_draft(key, proposal.invite)
    return proposal


def choose_manually(
    course_code: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: bool,
    session_style: str | None, student_id: str,
) -> None:
    """Manual pick after Advise failed. Advise then only writes the card around this person."""
    _, key = _request(course_code, starts_at, ends_at, place_type, note, study_anyway, session_style)
    set_manual_pick(key, student_id)


def recheck_for_send(
    course_code: str,
    starts_at: str,
    ends_at: str,
    place_type: str,
    note: str,
    study_anyway: bool,
    session_style: str | None,
    approved_ids: list[str],
    approved_starts_at: str,
    approved_ends_at: str,
) -> Proposal:
    """Executor re-check before Send: re-run the time Filter, confirm every approved id
    is still in it, and that the approved slot still sits inside each one's free window.
    A failure drops the cached Advise answer, so the page runs Advise again."""
    request, key = _request(course_code, starts_at, ends_at, place_type, note, study_anyway, session_style)
    inputs = load_inputs()
    request, _, _, _ = _prepare(request, inputs.me)
    candidates, _, later, _ = find_matches(request, inputs)
    records = [cand.facts for cand in candidates]
    facts = request_facts(request, inputs.me)
    errors = [f"{cand_id} is no longer free" for cand_id in approved_ids if cand_id not in {r["id"] for r in records}]
    slot = {"starts_at": approved_starts_at, "ends_at": approved_ends_at}
    if not errors and not later:
        errors = matching_prompt.slot_errors(slot, approved_ids, facts, records)
    elif not errors:
        later_records = [{**r, "free_overlap": r["other_free"]} for r in records]
        errors = matching_prompt.slot_errors(slot, approved_ids, {**facts, "window": {
            "starts_at": approved_starts_at, "ends_at": approved_ends_at}}, later_records)
    if not approved_ids:
        errors.append("nothing was approved")
    if errors:
        log.info("send re-check failed: %s", errors)
        clear_advice(key)
        raise StaleProposal("Their availability changed before sending. A new proposal replaces the old one.")
    proposal = build_proposal(course_code, starts_at, ends_at, place_type, note, study_anyway, session_style)
    if [person.id for person in proposal.people] != approved_ids or (proposal.starts_at, proposal.ends_at) != (
        _iso(_parse(approved_starts_at)), _iso(_parse(approved_ends_at))
    ):
        clear_advice(key)
        raise StaleProposal("The proposal changed before sending. Review the new one.")
    return proposal


def match_context() -> dict:
    profile = public_profile()
    return {
        "profile": profile,
        "eligible_ids": eligible_student_ids(),
        "busy": list_busy_for_match(),
    }


def candidate_rows(result: MatchResult) -> list[dict]:
    """Debug view of the Filter: facts per candidate, no scores."""
    rows = []
    for cand in result.candidates:
        window = cand.other_free[0] if result.later and cand.other_free else _longest(cand.free)
        rows.append(
            {
                "id": cand.person.profile.id,
                "first_name": cand.person.first_name,
                "year": cand.person.profile.year,
                "major": cand.person.profile.major,
                "style": STYLE_LABELS[cand.person.profile.study_style],
                "shared_courses": cand.facts["shared_courses"],
                "within_radius": cand.facts["within_radius"],
                "minutes_away": cand.facts["minutes_away"]["requester"],
                "slot": pretty_range(_iso(window[0]), _iso(window[1])) if window else "",
            }
        )
    return rows
