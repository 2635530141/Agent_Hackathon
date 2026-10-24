"""Matching and places track (David).

Deterministic matching. No model picks the person: hard filters and a weighted
score do, and the reason and invite are built from those results.
It may only use PublicProfile, eligible ids, and busy intervals without titles.
It must not read the private profile or call Google Calendar.
"""

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from app.contracts import Exam, MatchBusyInterval, Proposal, ProposalPerson, ProposalPlace, PublicProfile, StudyRequest
from app.format import pretty_range
from app.profile_core import PLACE_LABELS, STYLE_LABELS, eligible_student_ids, public_profile, public_profile_of
from app.schedule_core import BLOCKING_SOURCES, DAYS, list_busy_for_match, list_exams, weekly_hours
from app.store import courses, generated_drafts, invite_text, places, remember_draft, swaps_for
from app.schedule_core import matching_free_time
from app.scheduling.time import parse_instant, local_input, display_zone
from app.scheduling.fixture_math import free_time as fixture_free_time
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

Interval = tuple[datetime, datetime]

TOP_N = 5
NOTE_LIMIT = 300
NEAR_TIE = 5.0
MIN_OVERLAP = timedelta(minutes=60)
SEARCH_DAYS = 14
WALK_METERS_PER_MINUTE = 80
WEIGHTS = {
    "overlap": 40.0,
    "exam": 15.0,
    "priority": 10.0,
    "style": 10.0,
    "major": 5.0,
    "year": 5.0,
    "place": 5.0,
    "distance": 5.0,
    "group": 5.0,
}
PLACE_NOUNS = {"coffee": "a coffee shop", "library": "a library", "outdoor": "an outdoor spot"}
TIEBREAK_LABELS = {
    "overlap": "more free time in your window",
    "exam": "the same upcoming exam",
    "priority": "the exam priority window",
    "style": "the same study style",
    "major": "the same major",
    "year": "a closer year",
    "place": "liking this kind of place",
    "distance": "a shorter trip",
    "group": "wanting the same group size",
}


class NoMatch(Exception):
    """Nothing to propose. The message is safe to show the organizer."""


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
    person: Person
    score: float
    parts: dict[str, float]
    free: list[Interval]
    distance_m: float
    shared_exam: Exam | None = None


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
        second = _clock(h2, m2, mer2)
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
        log.warning("note contains an instruction or address; only place and time were read")
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


# Scoring


def _upcoming(exams: list[Exam], course_id: str, after: datetime) -> list[Exam]:
    return [exam for exam in exams if exam.course_id == course_id and _parse(exam.starts_at) > after]


def _in_priority(exams: list[Exam], start: datetime) -> bool:
    return any(
        exam.priority_starts_at and _parse(exam.priority_starts_at) <= start < _parse(exam.starts_at)
        for exam in exams
    )


def score(me: Person, other: Person, request: StudyRequest, free: list[Interval]) -> Candidate:
    start, end = _parse(request.starts_at), _parse(request.ends_at)
    mine = _upcoming(me.exams, request.course_id, start)
    theirs = _upcoming(other.exams, request.course_id, start)
    shared = next(
        (exam for exam in theirs if any(_parse(exam.starts_at).date() == _parse(own.starts_at).date() for own in mine)),
        None,
    )
    distance = _between(me.profile, other.profile)
    reach = me.profile.radius_meters + other.profile.radius_meters
    year_gap = abs(me.profile.year - other.profile.year)
    group_gap = abs(me.profile.group_size - other.profile.group_size)
    parts = {
        "overlap": WEIGHTS["overlap"] * min(1.0, _total(free) / (end - start)),
        "exam": WEIGHTS["exam"] if shared else 0.0,
        "priority": WEIGHTS["priority"] if shared and _in_priority(mine + theirs, start) else 0.0,
        "style": WEIGHTS["style"] if me.profile.study_style == other.profile.study_style else 0.0,
        "major": WEIGHTS["major"] if me.profile.major.lower() == other.profile.major.lower() else 0.0,
        "year": WEIGHTS["year"] * {0: 1.0, 1: 0.5}.get(year_gap, 0.0),
        "place": WEIGHTS["place"] if request.place_type in other.profile.place_types else 0.0,
        "distance": WEIGHTS["distance"] * max(0.0, 1 - distance / reach) if reach else 0.0,
        "group": WEIGHTS["group"] * {0: 1.0, 1: 0.5}.get(group_gap, 0.0),
    }
    return Candidate(
        person=other,
        score=round(sum(parts.values()), 1),
        parts={key: round(value, 1) for key, value in parts.items()},
        free=free,
        distance_m=distance,
        shared_exam=shared,
    )


def _course_code(course_id: str) -> str:
    return next((course["code"] for course in courses() if course["id"] == course_id), course_id)


def find_matches(request: StudyRequest, inputs: Inputs, duration_minutes: int | None = None) -> tuple[list[Candidate], list[tuple[str, int]], bool, str]:
    """Hard filters, then the soft ranking.

    Returns the ranked candidates, the filter funnel, whether the candidates are
    for a later slot because nobody fits the requested one, and a message when
    there is nobody at all.
    """
    me = inputs.me
    code = _course_code(request.course_id)
    start, end = _parse(request.starts_at), _parse(request.ends_at)
    # Explicit agent duration is independent of the search window. Existing callers
    # retain the legacy policy and scoring weights.
    min_len = timedelta(minutes=duration_minutes) if duration_minutes is not None else min(MIN_OVERLAP, end - start)
    if min_len <= timedelta(0) or min_len > end-start:
        raise ValueError("Duration must fit inside the study window.")

    eligible = set(inputs.eligible_ids)
    pool = [person for person in inputs.people if person.profile.id in eligible and person.profile.id != me.profile.id]
    course = [person for person in pool if request.course_id in person.profile.course_ids]
    reach = [
        person
        for person in course
        if _between(me.profile, person.profile) <= me.profile.radius_meters + person.profile.radius_meters
    ]

    mine = free_time(me, start, end)
    ranked = []
    for person in reach:
        free = _intersect(mine, free_time(person, start, end))
        longest = _longest(free)
        if longest and longest[1] - longest[0] >= min_len:
            ranked.append(score(me, person, request, free))
    funnel = [
        ("eligible", len(pool)),
        (f"take {code}", len(course)),
        ("within both radii", len(reach)),
        ("free in your window", len(ranked)),
    ]
    if ranked:
        ranked.sort(key=lambda cand: (-cand.score, cand.person.profile.id))
        return ranked[:TOP_N], funnel, False, ""

    if not course:
        return [], funnel, False, f"Nobody else has listed {code} yet."
    if not reach:
        return [], funnel, False, f"Nobody taking {code} is within reach of your travel radius."

    horizon = start + timedelta(days=SEARCH_DAYS)
    mine_later = free_time(me, start, horizon)
    later = []
    for person in reach:
        free = [
            (s, e)
            for s, e in _intersect(mine_later, free_time(person, start, horizon))
            if e - s >= min_len and not (s < end and e > start)
        ]
        if free:
            cand = score(me, person, request, free[:1])
            cand.free = free
            later.append(cand)
    if not later:
        return [], funnel, False, f"Nobody taking {code} is free then, or in the next two weeks."
    later.sort(key=lambda cand: (cand.free[0][0], -cand.score, cand.person.profile.id))
    return later[:TOP_N], funnel, True, ""


def pick_group(ranked: list[Candidate], needed: int, min_len: timedelta, earliest: bool) -> tuple[list[Candidate], Interval | None]:
    """Greedy: add the next candidate while everyone still shares a long enough window."""
    group: list[Candidate] = []
    common: list[Interval] | None = None
    for cand in ranked:
        trial = cand.free if common is None else _intersect(common, cand.free)
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


def _tiebreak(winner: Candidate, runner_up: Candidate) -> str:
    edges = sorted(
        ((winner.parts[key] - runner_up.parts[key], key) for key in winner.parts),
        reverse=True,
    )
    reasons = [TIEBREAK_LABELS[key] for gap, key in edges if gap > 0][:2]
    if not reasons:
        return f"Close call with {runner_up.person.first_name}: they tie on every factor. Swap to see them."
    return f"Close call with {runner_up.person.first_name}; {winner.person.first_name} wins on {' and '.join(reasons)}."


def _reason(
    request: StudyRequest,
    group: list[Candidate],
    window: Interval,
    place: dict,
    later: bool,
    ranked: list[Candidate],
    notes: list[str],
) -> str:
    code = _course_code(request.course_id)
    people = [cand.person for cand in group]
    when = pretty_range(_iso(window[0]), _iso(window[1]))
    plural = len(people) > 1
    lines = []
    if later:
        asked = pretty_range(request.starts_at, request.ends_at)
        lines.append(
            f"Nobody taking {code} is free {asked}. {_names(people)} {'share' if plural else 'shares'} {code} "
            f"and {'are' if plural else 'is'} free {when}, the next time you are both open."
        )
    else:
        lines.append(f"{_names(people)} {'share' if plural else 'shares'} {code} and {'are' if plural else 'is'} free {when}.")
    exams = [cand.shared_exam for cand in group if cand.shared_exam]
    if exams:
        exam_day = _parse(exams[0].starts_at)
        line = f"You are both preparing for the {code} exam on {exam_day:%a} {exam_day.day} {exam_day:%b}"
        if any(cand.parts["priority"] for cand in group):
            line += ", and this is in its priority window"
        lines.append(line + ".")
    hours = place["hours"][:1].lower() + place["hours"][1:]
    minutes = place["walk_minutes"]
    lines.append(
        f"{place['name']} is {PLACE_NOUNS[place['kind']]}, {hours}, rating {place['rating']}, "
        f"within {minutes} minute{'' if minutes == 1 else 's'} of {'all' if plural else 'both'} of you."
    )
    if place["kind"] != request.place_type:
        lines.append(f"No {PLACE_NOUNS[request.place_type].split(' ', 1)[1]} fits, so this is the closest match.")
    if not place["inside"]:
        lines.append("Nothing fits inside every travel radius, so this is the nearest option.")
    if not place["open"]:
        lines.append("Nothing on the list is open for the whole session. Check the hours.")
    others = [cand for cand in ranked if cand not in group]
    if others and not later and group[0] is ranked[0] and group[0].score - others[0].score <= NEAR_TIE:
        lines.append(_tiebreak(group[0], others[0]))
    return " ".join(lines + notes)


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


def match(request: StudyRequest, inputs: Inputs | None = None, swap_person: int = 0, swap_place: int = 0) -> MatchResult:
    """Interpret the note, score, pick the group and the place, and draft the copy."""
    inputs = inputs or load_inputs()
    me = inputs.me
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

    ranked, funnel, later, message = find_matches(request, inputs)
    if not ranked:
        return MatchResult(request=request, candidates=[], funnel=funnel, message=message)

    shift = swap_person % len(ranked)
    order = ranked[shift:] + ranked[:shift]
    needed = me.profile.group_size - 1
    min_len = min(MIN_OVERLAP, end - start)
    group, window = pick_group(order, needed, min_len, earliest=later)
    if later:
        window = (window[0], min(window[1], window[0] + (end - start)))
    notes = list(note.used)
    if len(group) < needed:
        notes.append(f"Only {len(group)} other {'person fits' if len(group) == 1 else 'people fit'}, so this is a group of {len(group) + 1}.")

    profiles = [me.profile] + [cand.person.profile for cand in group]
    options = suggest_places(request.place_type, profiles, window[0], window[1], note.avoid, me.profile.place_types)
    # Ranking an infeasible venue last never makes it safe to propose. A different
    # type needs the explicit-permission agent flow; legacy/manual calls ask first.
    options = [p for p in options if p["inside"] and p["open"] and p.get("opens") and p.get("closes")
               and p["kind"] == request.place_type and p["kind"] not in note.avoid]
    if not options:
        return MatchResult(request=request,candidates=ranked,funnel=funnel,later=later,
            message="No feasible place is verified for everyone's radius and the full session. Change the place type or time explicitly.")
    place = options[swap_place % len(options)]

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
        reason=_reason(request, group, window, place, later, ranked, notes),
        invite=_invite(request, [cand.person for cand in group], window, place),
    )
    return MatchResult(request=request, candidates=ranked, funnel=funnel, later=later, proposal=proposal)


def build_proposal(
    course_code: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: bool = False
) -> Proposal:
    """Session page entry point. Raises NoMatch with a message for the page."""
    course_id = next((course["id"] for course in courses() if course["code"] == course_code), course_code)
    if place_type not in PLACE_LABELS:
        place_type = public_profile().place_types[0]
    request = StudyRequest(
        course_id=course_id,
        starts_at=starts_at,
        ends_at=ends_at,
        place_type=place_type,
        note=note,
        study_anyway=study_anyway,
    )
    key = "|".join([course_id, starts_at, ends_at, place_type, note, str(study_anyway)])
    swap_person, swap_place = swaps_for(key)
    result = match(request, swap_person=swap_person, swap_place=swap_place)
    if result.proposal is None:
        raise NoMatch(result.message)
    proposal = result.proposal
    saved = invite_text().strip()
    drafts = generated_drafts(key)
    if drafts and saved and saved not in drafts:
        return proposal.model_copy(update={"invite": saved})
    remember_draft(key, proposal.invite)
    return proposal


def match_context() -> dict:
    profile = public_profile()
    return {
        "profile": profile,
        "eligible_ids": eligible_student_ids(),
        "busy": list_busy_for_match(),
    }


def candidate_rows(result: MatchResult) -> list[dict]:
    """Debug view of the ranking. Ids, scores, and the shared slot only."""
    rows = []
    for cand in result.candidates:
        window = cand.free[0] if result.later else _longest(cand.free)
        rows.append(
            {
                "id": cand.person.profile.id,
                "first_name": cand.person.first_name,
                "year": cand.person.profile.year,
                "major": cand.person.profile.major,
                "style": STYLE_LABELS[cand.person.profile.study_style],
                "score": cand.score,
                "parts": cand.parts,
                "slot": pretty_range(_iso(window[0]), _iso(window[1])) if window else "",
            }
        )
    return rows
