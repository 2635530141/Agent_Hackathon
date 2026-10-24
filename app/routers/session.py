import re
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app import matching_core, room_matching, schedule_core, store
from app.format import clock, pretty_range
from app.omnara_client import OmnaraError, interpret_request
from app.profile_core import PLACE_LABELS, public_profile
from app.web import render
from app.scheduling.session_hooks import refresh_before_request, export_card, check_room_time

router = APIRouter()
_VAGUE_TIME = re.compile(r"\bany\s*time\b|\bwhenever\b|\bany day\b|from \w+day to \w+day", re.IGNORECASE)
_BROAD_SUBJECT = re.compile(
    r"\b(?:an?y\s*thing|anthing|any(?!\s*(?:time|day)\b)|something|whatever|related|guys|subjects?|students?|people|folks|classes)\b",
    re.IGNORECASE,
)
_SPECIFIC_COURSE = re.compile(
    r"\b(?:computer science|cs|math(?:ematics)?|eecs?|stat|ee|physics)\s*\d",
    re.IGNORECASE,
)
_SUBJECTS = (
    ("cs", re.compile(r"\b(?:cs|computer science)\b", re.IGNORECASE)),
    ("math", re.compile(r"\b(?:math|mathematics)\b", re.IGNORECASE)),
)
_SUBJECT_LABELS = {"cs": "Any CS course", "math": "Any Math course"}
_DAYS = {
    "sunday": 25,
    "monday": 26,
    "tuesday": 27,
    "wednesday": 28,
    "thursday": 29,
    "friday": 30,
    "saturday": 31,
}
_PARTS = {
    "morning": ("09:00", "12:00"),
    "afternoon": ("13:00", "17:00"),
    "evening": ("18:00", "21:00"),
    "night": ("19:00", "22:00"),
}

DEFAULT_START = "2026-10-29T18:00"
DEFAULT_END = "2026-10-29T21:00"


def _query(
    course_id: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: bool, sentence: str
) -> str:
    return urlencode(
        {
            "sentence": sentence,
            "ready": "1",
            "course_id": course_id,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "place_type": place_type,
            "note": note,
            "study_anyway": "1" if study_anyway else "0",
        }
    )


def _profile_courses() -> list[dict]:
    owned = set(public_profile().course_ids)
    return [course for course in store.courses() if course["id"] in owned]


def _clock(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _course_record(course_id: str) -> dict:
    if course_id.startswith("subject:"):
        label = _SUBJECT_LABELS.get(course_id.split(":", 1)[1], "Any course")
        return {"id": course_id, "code": label, "name": label}
    found = store.course_by_id(course_id)
    if found:
        return found
    label = course_id.strip() or "Course"
    return {"id": label, "code": label, "name": label}


def _match_code(course: dict) -> str:
    if str(course["id"]).startswith("subject:"):
        return course["id"]
    return course["code"]


def _subject_request(text: str) -> str | None:
    """A department is enough. 'CS 61A' stays one course; 'anything CS' does not."""
    if _SPECIFIC_COURSE.search(text) and not _BROAD_SUBJECT.search(text):
        return None
    for key, pattern in _SUBJECTS:
        if pattern.search(text):
            return f"subject:{key}"
    return None


def _study_hour(hour: int, minute: int, meridiem: str | None) -> tuple[int, int] | None:
    """A bare 1–7 is the afternoon. 'at 4' is 4:00 PM."""
    if hour > 23 or minute > 59:
        return None
    if meridiem:
        flag = meridiem.lower()
        if flag == "pm" and hour < 12:
            hour += 12
        elif flag == "am" and hour == 12:
            hour = 0
    elif 1 <= hour <= 7:
        hour += 12
    return hour, minute


_ANYTIME = re.compile(r"\bany\s*time\b|\bwhenever\b|\bdon'?t care\b|\bdo not care\b", re.IGNORECASE)
_GROUP_SIZE = re.compile(
    r"\b(?:at least|group of|with|for)\s+(\d+)\b|\b(\d+)\s*(?:people|persons|students)\b",
    re.IGNORECASE,
)
_WEEK_START = "2026-10-26T09:00"
_WEEK_END = "2026-10-31T21:00"


def _is_flexible_day(text: str) -> bool:
    """'Any time on Wednesday' is that day. 'Any time' with no day, or several days, is not."""
    days = [name for name in _DAYS if re.search(rf"\b{name}\b", text, re.IGNORECASE)]
    return len(days) == 1 and bool(re.search(r"\bany\s*time\b|\bwhenever\b", text, re.IGNORECASE))


def _is_open_time(turns: list[dict]) -> bool:
    """'Any time' or 'I don't care', with no day and clock, means every open room."""
    user_turns = [turn["text"] for turn in turns if turn.get("role") == "user"]
    if not user_turns:
        return False
    latest = user_turns[-1]
    if _local_window(latest) and not _is_flexible_day(latest):
        return False
    if _is_flexible_day(latest):
        return False
    return bool(_ANYTIME.search(" ".join(user_turns)))


def _spoken_group_size(text: str) -> int | None:
    """'at least 4 people' is a room of 4. The profile size is the fallback."""
    found = [int(match.group(1) or match.group(2)) for match in _GROUP_SIZE.finditer(text)]
    if not found:
        return None
    return max(2, min(5, found[-1]))


def _is_anytime_span(starts_at: str, ends_at: str) -> bool:
    start, end = _clock(starts_at), _clock(ends_at)
    return bool(start and end and end - start > timedelta(hours=24))


_SPAN_BETWEEN = re.compile(
    r"\bbetween\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s+and\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE
)
_SPAN = re.compile(
    r"\b(?:from\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|until|till|-|–|—)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
    re.IGNORECASE,
)


def _local_window(text: str) -> tuple[str, str] | None:
    day = next((number for name, number in _DAYS.items() if re.search(rf"\b{name}\b", text, re.IGNORECASE)), None)
    if day is None:
        return None
    span = _SPAN_BETWEEN.search(text) or _SPAN.search(text)
    point = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.IGNORECASE)
    clock = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.IGNORECASE)
    start = end = None
    if span:
        start = _study_hour(int(span.group(1)), int(span.group(2) or 0), span.group(3) or span.group(6))
        end = _study_hour(int(span.group(4)), int(span.group(5) or 0), span.group(6) or span.group(3))
        if start and end and end <= start and not span.group(6) and end[0] + 12 <= 23:
            end = (end[0] + 12, end[1])  # "7-9" with no am/pm is 7 to 9 in the evening.
        if not (start and end and end > start):
            start = end = None
    if start is None and point:
        start = _study_hour(int(point.group(1)), int(point.group(2) or 0), point.group(3))
    elif start is None and clock:
        start = _study_hour(int(clock.group(1)), int(clock.group(2) or 0), clock.group(3))
    if start and end and (end[0], end[1]) > start:
        begin, finish = start, end
    elif start:
        begin = start
        finish = (start[0] + 1, start[1]) if start[0] < 23 else (23, 59)
    else:
        part = next((clocks for name, clocks in _PARTS.items() if re.search(rf"\b{name}\b", text, re.IGNORECASE)), None)
        if part is not None:
            return f"2026-10-{day:02d}T{part[0]}", f"2026-10-{day:02d}T{part[1]}"
        if _is_flexible_day(text):
            return f"2026-10-{day:02d}T09:00", f"2026-10-{day:02d}T21:00"
        return None
    return (
        f"2026-10-{day:02d}T{begin[0]:02d}:{begin[1]:02d}",
        f"2026-10-{day:02d}T{finish[0]:02d}:{finish[1]:02d}",
    )


def _window_from_turns(turns: list[dict]) -> tuple[str, str] | None:
    """The latest reply that names a day and a time wins over an earlier 'any time'."""
    user_turns = [turn["text"] for turn in turns if turn.get("role") == "user"]
    for text in reversed(user_turns):
        window = _local_window(text)
        if window:
            return window
    return None


def _course_from_text(text: str, courses: list[dict]) -> str | None:
    for course in courses:
        code = course["code"]
        compact = code.replace(" ", "")
        if re.search(rf"\b{re.escape(code)}\b", text, re.IGNORECASE) or re.search(
            rf"\b{re.escape(compact)}\b", text, re.IGNORECASE
        ):
            return course["id"]
    return _subject_request(text)


def _mentioned_place(text: str) -> str:
    if re.search(r"\b(?:not|no|avoid|never|without)\b(?:\W+\w+){0,3}?\W+(?:librar|lib\b)", text, re.IGNORECASE):
        return ""
    if re.search(r"\bdoe\b|librar|\blib\b", text, re.IGNORECASE):
        return "library"
    if re.search(r"coffeeshop|coffee\s*shop|\b(?:coffee|cafe|café)\b", text, re.IGNORECASE):
        return "coffee"
    if re.search(r"\b(?:outdoor|outside|glade)\b", text, re.IGNORECASE):
        return "outdoor"
    return ""


def _parsed(raw: dict, courses: list[dict]) -> dict:
    by_id = {course["id"]: course for course in courses}
    by_code = {}
    for course in courses:
        by_code[course["code"].lower()] = course
        by_code[course["code"].lower().replace(" ", "")] = course
    named = str(raw.get("course_id") or raw.get("course") or "").strip()
    if not named:
        raise ValueError("That sentence did not name a course.")
    compact = named.lower().replace(" ", "")
    course = by_id.get(named) or by_code.get(named.lower()) or by_code.get(compact)
    place_type = str(raw.get("place_type") or "coffee")
    if place_type not in PLACE_LABELS:
        place_type = "coffee"
    starts_at = _clock(str(raw.get("starts_at") or ""))
    ends_at = _clock(str(raw.get("ends_at") or ""))
    if starts_at is None or ends_at is None or ends_at <= starts_at:
        raise ValueError("Omnara did not return a usable time.")
    return {
        "course_id": course["id"] if course else named,
        "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": ends_at.strftime("%Y-%m-%dT%H:%M"),
        "place_type": place_type,
        "note": str(raw.get("note") or "")[:300],
    }


def _clarify(raw: dict, courses: list[dict], turns: list[dict]) -> tuple[dict | None, str]:
    """A missing course or a vague time becomes one question. The earlier messages stay."""
    question = str(raw.get("question") or "").strip()
    user_text = " ".join(turn["text"] for turn in turns if turn.get("role") == "user")
    raw = dict(raw)
    spoken_course = _course_from_text(user_text, courses)
    if spoken_course and (not str(raw.get("course_id") or raw.get("course") or "").strip() or raw.get("question")):
        raw["course_id"] = spoken_course
        if "course" in question.lower():
            question = ""
            raw.pop("question", None)
    open_time = _is_open_time(turns)
    window = None if open_time else _window_from_turns(turns)
    if open_time:
        raw["starts_at"], raw["ends_at"] = _WEEK_START, _WEEK_END
        if "time" in question.lower():
            question = ""
            raw.pop("question", None)
    elif window:
        raw["starts_at"], raw["ends_at"] = window
        if "time" in question.lower():
            question = ""
            raw.pop("question", None)
    place = _mentioned_place(user_text)
    if place:
        raw["place_type"] = place
    named = str(raw.get("course_id") or raw.get("course") or "").strip()
    user_turns = [turn["text"] for turn in turns if turn.get("role") == "user"]
    latest = user_turns[-1] if user_turns else ""
    starts_at = _clock(str(raw.get("starts_at") or ""))
    ends_at = _clock(str(raw.get("ends_at") or ""))
    wide = bool(
        starts_at
        and ends_at
        and not open_time
        and not _is_flexible_day(latest)
        and (ends_at - starts_at > timedelta(hours=8) or starts_at.date() != ends_at.date())
    )
    vague = bool(_VAGUE_TIME.search(latest)) and _local_window(latest) is None and not open_time
    missing_time = starts_at is None or ends_at is None or ends_at <= starts_at or wide or vague
    if not named:
        return None, question or "You didn't specify a course. Which course do you want to study?"
    if missing_time:
        return None, question or "You didn't specify a time. What time do you want to meet?"
    try:
        return _parsed(raw, courses), ""
    except ValueError:
        return None, question or "You didn't specify a time. What time do you want to meet?"


def _session_context(
    course_id: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: bool, sentence: str
):
    course = _course_record(course_id)
    error = ""
    conflict = None
    anytime = _is_anytime_span(starts_at, ends_at)
    if not anytime:
        try:
            conflict = schedule_core.own_conflict(starts_at, ends_at, study_anyway)
        except ValueError as exc:
            error = str(exc)
    try:
        asked_when = "Any time" if anytime else pretty_range(starts_at, ends_at)
    except ValueError:
        asked_when = "Any time" if anytime else ""
    start, end = _clock(starts_at), _clock(ends_at)
    if (
        start
        and end
        and start.date() == end.date()
        and (start.hour, start.minute, end.hour, end.minute) == (9, 0, 21, 0)
    ):
        asked_when = f"{start:%a} {start.day} {start:%b}, any time"
    return {
        "course": course,
        "courses": store.courses(),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "place_type": place_type,
        "place_labels": PLACE_LABELS,
        "note": note,
        "study_anyway": study_anyway,
        "error": error,
        "conflict": conflict,
        "conflict_when": pretty_range(starts_at, ends_at) if conflict else "",
        "alternative_label": pretty_range(conflict["alternative_start"], conflict["alternative_end"])
        if conflict and conflict.get("alternative_start")
        else "",
        "alternative_href": "/session?"
        + _query(
            course["id"],
            conflict["alternative_start"],
            conflict["alternative_end"],
            place_type,
            note,
            False,
            sentence,
        )
        if conflict and conflict.get("alternative_start")
        else "",
        "anyway_href": "/session?" + _query(course["id"], starts_at, ends_at, place_type, note, True, sentence),
        "proposal": None,
        "when": asked_when,
        "asked_when": asked_when,
        "invites": store.latest_invitations(),
        "all_accepted": store.batch_all_accepted(),
        "declined_names": store.declined_names(),
        "calendar_written": store.calendar_event_written(),
        "sentence": sentence,
        "ready": True,
        "anytime": anytime,
        "keep": _query(course["id"], starts_at, ends_at, place_type, note, study_anyway, sentence),
        "group_size": _spoken_group_size(sentence) or public_profile().group_size,
        "new_place": _place_name(place_type, note),
    }


def _family(course_id: str) -> str:
    if course_id.startswith("subject:"):
        return course_id.split(":", 1)[1]
    found = store.course_by_id(course_id)
    label = (found["code"] if found else course_id).lower().replace(" ", "")
    if label.startswith("cs") or course_id.lower().startswith("cs"):
        return "cs"
    if label.startswith("math") or course_id.lower().startswith("math"):
        return "math"
    return label or course_id.lower()


def _place_name(place_type: str, note: str) -> str:
    folded = note.lower()
    for place in store.places():
        if place["name"].lower() in folded:
            return place["name"]
    for place in store.places():
        if place["kind"] == place_type:
            return place["name"]
    return PLACE_LABELS.get(place_type, "Campus")


def _member(request: Request) -> dict:
    user = request.session.get("user") or {}
    profile = public_profile()
    name = user.get("name") or profile.name
    return {
        "id": user.get("id") or profile.id,
        "first_name": name.split()[0],
        "major": profile.major,
        "year": profile.year,
    }


_WEEK = (
    ("Mon", 26),
    ("Tue", 27),
    ("Wed", 28),
    ("Thu", 29),
    ("Fri", 30),
    ("Sat", 31),
)


def _decorate_room(room: dict, user_id: str) -> dict:
    start, end = _clock(room["starts_at"]), _clock(room["ends_at"])
    taken = len(room["members"])
    flexible = bool(
        start and end and start.date() == end.date() and (start.hour, start.minute, end.hour, end.minute) == (9, 0, 21, 0)
    )
    return {
        **room,
        "when": "Any time" if flexible else pretty_range(room["starts_at"], room["ends_at"]),
        "clock_label": "Any time" if flexible else (f"{clock(start)}–{clock(end)}" if start and end else ""),
        "names": ", ".join(person["first_name"] for person in room["members"]) or "Empty",
        "taken": taken,
        "open_seats": max(0, room["capacity"] - taken),
        "full": taken >= room["capacity"],
        "joined": any(person["id"] == user_id for person in room["members"]),
        "day": start.day if start else 0,
    }


def week_schedule(user_id: str) -> list[dict]:
    """Every room on the demo week, grouped by day."""
    decorated = [_decorate_room(room, user_id) for room in store.rooms()]
    days = []
    for label, number in _WEEK:
        days.append(
            {
                "label": f"{label} {number}",
                "rooms": sorted((room for room in decorated if room["day"] == number), key=lambda room: room["starts_at"]),
            }
        )
    return days


def opened_room(room_id: str, user_id: str) -> dict | None:
    found = next((room for room in store.rooms() if room["id"] == room_id), None)
    return _decorate_room(found, user_id) if found else None


def _fits(room_start: datetime, room_end: datetime, start: datetime, end: datetime) -> bool:
    """The requested window is a hard boundary: the room sits inside it, or shares at least an hour."""
    inside = start <= room_start and room_end <= end
    shared = min(room_end, end) - max(room_start, start)
    return inside or shared >= timedelta(hours=1)


def eligible_rooms(course_id: str, starts_at: str, ends_at: str, user_id: str, open_time: bool = False) -> list[dict]:
    """Rooms for the same subject. A specific time must fit (see _fits); any time shows every open room."""
    start, end = _clock(starts_at), _clock(ends_at)
    if start is None or end is None or end <= start:
        return []
    family = _family(course_id)
    rows = []
    for room in store.rooms():
        if _family(room["course_id"]) != family:
            continue
        room_start, room_end = _clock(room["starts_at"]), _clock(room["ends_at"])
        if room_start is None or room_end is None:
            continue
        if not open_time and not _fits(room_start, room_end, start, end):
            continue
        rows.append(_decorate_room(room, user_id))
    rows.sort(key=lambda row: (row["full"], row["starts_at"], row["place_name"]))
    return rows


def rooms_you_can_join(
    user_id: str, course_id: str | None = None, starts_at: str = "", ends_at: str = ""
) -> list[dict]:
    """Open seats you can take. A described session keeps only rooms that overlap it."""
    if course_id and starts_at and ends_at:
        rows = eligible_rooms(course_id, starts_at, ends_at, user_id, _is_anytime_span(starts_at, ends_at))
    else:
        rows = [_decorate_room(room, user_id) for room in store.rooms()]
    return [room for room in rows if room["joined"] or not room["full"]]


def _schedule_view(
    request: Request,
    open_room: str = "",
    course_id: str | None = None,
    starts_at: str = "",
    ends_at: str = "",
    place_type: str = "",
    note: str = "",
) -> dict:
    user = request.session.get("user") or {}
    user_id = user.get("id", "")
    rooms = rooms_you_can_join(user_id, course_id, starts_at, ends_at)
    ranking = room_matching.RoomRanking()
    if course_id and starts_at and ends_at and place_type:
        ranking = room_matching.rank(rooms, user_id, course_id, starts_at, ends_at, place_type, note)
        rooms = room_matching.apply(rooms, ranking)
    selected = opened_room(open_room, user_id) if open_room else None
    if selected and selected["id"] not in {room["id"] for room in rooms}:
        selected = None
    return {"rooms": rooms, "selected": selected, "ranking": ranking}


def _form_only(
    request: Request,
    sentence: str,
    error: str = "",
    notice: str = "",
    thread: list | None = None,
    question: str = "",
    open_room: str = "",
):
    catalog = _profile_courses() or store.courses()
    return render(
        request,
        "session.html",
        nav="session",
        sentence=sentence,
        ready=False,
        course=catalog[0],
        courses=catalog,
        starts_at="",
        ends_at="",
        place_type="coffee",
        place_labels=PLACE_LABELS,
        note="",
        study_anyway=False,
        error=error,
        notice=notice,
        calendar_export=export_card(request),
        conflict=None,
        proposal=None,
        when="",
        invites=store.latest_invitations(),
        all_accepted=store.batch_all_accepted(),
        declined_names=store.declined_names(),
        calendar_written=store.calendar_event_written(),
        thread=thread or [],
        question=question,
        group_size=public_profile().group_size,
        new_place="",
        asked_when="",
        **_schedule_view(request, open_room),
    )


@router.get("/session")
def session_page(
    request: Request,
    sentence: str = "",
    ready: str = "0",
    course_id: str = "cs61a",
    starts_at: str = DEFAULT_START,
    ends_at: str = DEFAULT_END,
    place_type: str = "coffee",
    note: str = "",
    study_anyway: str = "0",
    notice: str = "",
    discarded: str = "0",
    fresh: str = "0",
    open: str = "",
    error: str = "",
):
    if fresh == "1":
        request.session.pop("study_thread", None)
        return _form_only(request, "")
    if ready != "1":
        thread = list(request.session.get("study_thread") or [])
        question = thread[-1]["text"] if thread and thread[-1].get("role") == "assistant" else ""
        return _form_only(request, sentence, notice=notice, thread=thread, question=question, open_room=open)
    context = _session_context(course_id, starts_at, ends_at, place_type, note, study_anyway == "1", sentence)
    thread = list(request.session.get("study_thread") or [])
    if thread and thread[0].get("text") != sentence:
        thread = []
    spoken = _spoken_group_size(
        " ".join([sentence, *(turn.get("text", "") for turn in thread if turn.get("role") == "user")])
    )
    if spoken:
        context["group_size"] = spoken
        user = request.session.get("user") or {}
        store.resize_hosted(
            user.get("id", ""), context["course"]["id"], context["new_place"], starts_at, ends_at, spoken
        )
    context.update(_schedule_view(request, open, context["course"]["id"], starts_at, ends_at, place_type, note))
    if discarded == "1":
        context["proposal"] = None
        context["conflict"] = None
        notice = notice or "Request discarded. Nothing was sent."
    if error:
        context["error"] = error
    return render(
        request,
        "session.html",
        nav="session",
        notice=notice,
        thread=thread,
        question="",
        calendar_export=export_card(request),
        **context,
    )


@router.post("/session/interpret")
def interpret(
    request: Request,
    sentence: str = Form(""),
    reply: str = Form(""),
    followup: str = Form("0"),
):
    courses = _profile_courses() or store.courses()
    if followup == "1":
        thread = list(request.session.get("study_thread") or [])
        answer = reply.strip()
        if not thread or not answer:
            return _form_only(
                request,
                sentence,
                error="Answer the question so matching can continue.",
                thread=thread,
                question=thread[-1]["text"] if thread and thread[-1].get("role") == "assistant" else "",
            )
        thread.append({"role": "user", "text": answer})
    else:
        text = sentence.strip()
        if not text:
            return _form_only(request, "", error="Write what you want to study.")
        thread = [{"role": "user", "text": text}]
    parsed, question = _clarify({}, courses, thread)
    if parsed is None:
        try:
            refresh_before_request(request)
            parsed, question = _clarify(interpret_request(thread, courses), courses, thread)
        except OmnaraError:
            question = question or "You didn't specify a time. What time do you want to meet?"
            parsed = None
        except ValueError as exc:
            return _form_only(request, thread[0]["text"], error=str(exc), thread=thread)
    if question:
        thread.append({"role": "assistant", "text": question})
        request.session["study_thread"] = thread
        return _form_only(request, thread[0]["text"], thread=thread, question=question)
    request.session["study_thread"] = thread
    return _redirect_session(
        parsed["course_id"],
        parsed["starts_at"],
        parsed["ends_at"],
        parsed["place_type"],
        parsed["note"],
        "0",
        sentence=thread[0]["text"],
    )


def _picked_time(day: str, start: str, end: str) -> tuple[str, str] | None:
    if not day or not start or not end:
        return None
    starts_at, ends_at = f"{day}T{start[:5]}", f"{day}T{end[:5]}"
    start_at, end_at = _clock(starts_at), _clock(ends_at)
    if start_at is None or end_at is None or end_at <= start_at:
        return None
    return starts_at, ends_at


def _redirect_session(
    course_id: str,
    starts_at: str,
    ends_at: str,
    place_type: str,
    note: str,
    study_anyway: str,
    notice: str = "",
    sentence: str = "",
    open_room: str = "",
    error: str = "",
) -> RedirectResponse:
    query = _query(course_id, starts_at, ends_at, place_type, note, study_anyway == "1", sentence)
    if open_room:
        query += "&open=" + quote(open_room)
    if notice:
        query += "&notice=" + quote(notice)
    if error:
        query += "&error=" + quote(error)
    return RedirectResponse(f"/session?{query}", status_code=303)


@router.post("/session/draft")
def save_draft(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
    sentence: str = Form(""),
):
    store.set_invite_text(invite)
    return _redirect_session(
        course_id, starts_at, ends_at, place_type, note, study_anyway, "Draft saved.", sentence=sentence
    )


@router.post("/session/swap-person")
def swap_person(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
    sentence: str = Form(""),
):
    store.set_invite_text(invite)
    store.cycle_person()
    return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway, sentence=sentence)


@router.post("/session/swap-place")
def swap_place(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
    sentence: str = Form(""),
):
    store.set_invite_text(invite)
    store.cycle_place()
    return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway, sentence=sentence)


_ROOM_NOTICES = {
    "joined": "You joined the room.",
    "switched": "You joined this room and left the other one at that time.",
    "already": "You are already in that room.",
    "full": "That room is full.",
    "missing": "That room is no longer open.",
    "left": "You left. That seat is open again.",
    "closed": "You left, and the empty room was removed.",
}


@router.post("/session/rooms")
def open_room(
    request: Request,
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    sentence: str = Form(""),
    group_size: str = Form(""),
    room_day: str = Form(""),
    room_start: str = Form(""),
    room_end: str = Form(""),
):
    course = _course_record(course_id)
    try:
        asked_size = int(group_size)
    except ValueError:
        asked_size = 0
    if asked_size not in (2, 3, 4, 5):
        asked_size = _spoken_group_size(sentence) or public_profile().group_size
    chosen = _picked_time(room_day, room_start, room_end)
    if room_day or room_start or room_end:
        if chosen is None:
            return _redirect_session(
                course["id"],
                starts_at,
                ends_at,
                place_type,
                note,
                study_anyway,
                sentence=sentence,
                error="Choose a day and a start time that is earlier than the end time.",
            )
    room_starts, room_ends = chosen if chosen else (starts_at, ends_at)
    place_name = _place_name(place_type, note)
    host = _member(request)
    existing = store.hosted_room(host["id"], course["id"], place_name, room_starts, room_ends)
    try:
        check_room_time(request, [p["id"] for p in existing["members"]] if existing else [],
                        existing["starts_at"] if existing else room_starts,
                        existing["ends_at"] if existing else room_ends)
    except ValueError as exc:
        return _redirect_session(course["id"], starts_at, ends_at, place_type, note, study_anyway,
                                 sentence=sentence, error=str(exc))
    already = existing is not None
    left_another = store.holds_other_seat(host["id"], room_starts, room_ends, course["id"], place_name)
    room = store.create_room(
        host,
        course["id"],
        course["code"],
        room_starts,
        room_ends,
        place_type,
        place_name,
        asked_size,
    )
    if already:
        notice = "You already opened this room."
    elif left_another:
        notice = "Room opened. You left the other room at that time."
    else:
        notice = "Room opened. You are the host."
    return _redirect_session(
        course["id"],
        starts_at,
        ends_at,
        place_type,
        note,
        study_anyway,
        notice,
        sentence=sentence,
        open_room=room["id"],
    )


@router.post("/session/rooms/{room_id}/time")
def change_room_time(
    request: Request,
    room_id: str,
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    sentence: str = Form(""),
    room_day: str = Form(""),
    room_start: str = Form(""),
    room_end: str = Form(""),
):
    chosen = _picked_time(room_day, room_start, room_end)
    if chosen is None:
        return _redirect_session(
            course_id,
            starts_at,
            ends_at,
            place_type,
            note,
            study_anyway,
            sentence=sentence,
            open_room=room_id,
            error="Choose a day and a start time that is earlier than the end time.",
        )
    user = request.session.get("user") or {}
    room = next((item for item in store.rooms() if item["id"] == room_id), None)
    if room and room["host_id"] == user.get("id"):
        try:
            check_room_time(request, [p["id"] for p in room["members"]], chosen[0], chosen[1])
        except ValueError as exc:
            return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway,
                                     sentence=sentence, open_room=room_id, error=str(exc))
    result = store.update_room_time(room_id, user.get("id", ""), chosen[0], chosen[1])
    notice = {
        "updated": "Time updated.",
        "host": "Only the host can change the time.",
        "missing": "That room is no longer open.",
    }.get(result, "That room is no longer open.")
    return _redirect_session(
        course_id,
        starts_at,
        ends_at,
        place_type,
        note,
        study_anyway,
        notice,
        sentence=sentence,
        open_room="" if result == "missing" else room_id,
    )


def _schedule_redirect(room_id: str, notice: str, keep_open: bool) -> RedirectResponse:
    query = {}
    if keep_open and room_id:
        query["open"] = room_id
    if notice:
        query["notice"] = notice
    target = "/session" + (("?" + urlencode(query)) if query else "")
    return RedirectResponse(target, status_code=303)


@router.post("/session/rooms/{room_id}/join")
def join_room(
    request: Request,
    room_id: str,
    course_id: str = Form(""),
    starts_at: str = Form(""),
    ends_at: str = Form(""),
    place_type: str = Form("coffee"),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    sentence: str = Form(""),
):
    room = next((item for item in store.rooms() if item["id"] == room_id), None)
    if room:
        try:
            check_room_time(request, [p["id"] for p in room["members"]], room["starts_at"], room["ends_at"])
        except ValueError as exc:
            if not starts_at:
                return _schedule_redirect(room_id, str(exc), True)
            return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway,
                                     sentence=sentence, open_room=room_id, error=str(exc))
    result = store.join_room(room_id, _member(request))
    notice = _ROOM_NOTICES.get(result, "That room is no longer open.")
    if not starts_at:
        return _schedule_redirect(room_id, notice, result != "missing")
    return _redirect_session(
        course_id,
        starts_at,
        ends_at,
        place_type,
        note,
        study_anyway,
        _ROOM_NOTICES.get(result, "That room is no longer open."),
        sentence=sentence,
        open_room=room_id,
    )


@router.post("/session/rooms/{room_id}/leave")
def leave_room(
    request: Request,
    room_id: str,
    course_id: str = Form(""),
    starts_at: str = Form(""),
    ends_at: str = Form(""),
    place_type: str = Form("coffee"),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    sentence: str = Form(""),
):
    user = request.session.get("user") or {}
    result = store.leave_room(room_id, user.get("id", ""))
    notice = _ROOM_NOTICES.get(result, "That room is no longer open.")
    if not starts_at:
        return _schedule_redirect("" if result == "closed" else room_id, notice, result != "closed")
    return _redirect_session(
        course_id,
        starts_at,
        ends_at,
        place_type,
        note,
        study_anyway,
        _ROOM_NOTICES.get(result, "That room is no longer open."),
        sentence=sentence,
        open_room="" if result == "closed" else room_id,
    )


@router.post("/session/confirm")
def confirm_invite(
    request: Request,
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
    sentence: str = Form(""),
):
    course = _course_record(course_id)
    try:
        refresh_before_request(request)
        conflict = schedule_core.own_conflict(starts_at, ends_at, study_anyway == "1")
    except ValueError:
        conflict = {"title": "That time"}
    if conflict:
        return _redirect_session(
            course["id"],
            starts_at,
            ends_at,
            place_type,
            note,
            study_anyway,
            "Pick a conflict-free time before sending.",
            sentence=sentence,
        )
    store.set_invite_text(invite)
    try:
        proposal = matching_core.build_proposal(
            _match_code(course), starts_at, ends_at, place_type, note, study_anyway == "1"
        )
    except matching_core.NoMatch as exc:
        return _redirect_session(
            course["id"], starts_at, ends_at, place_type, note, study_anyway, str(exc), sentence=sentence
        )
    message = invite.strip() or proposal.invite
    store.create_invitations(
        [
            {
                "person_id": person.id,
                "first_name": person.first_name,
                "course_id": course["id"],
                "course_code": course["code"],
                "course_name": course["name"],
                "starts_at": proposal.starts_at,
                "ends_at": proposal.ends_at,
                "place_name": proposal.place.name,
                "message": message,
            }
            for person in proposal.people
        ]
    )
    return _redirect_session(
        course_id,
        starts_at,
        ends_at,
        place_type,
        note,
        study_anyway,
        "Invitation sent for this demo. Nothing is emailed until an email provider is connected.",
        sentence=sentence,
    )


@router.post("/session/calendar")
def add_to_calendar(
    course_id: str = Form("cs61a"),
    starts_at: str = Form(DEFAULT_START),
    ends_at: str = Form(DEFAULT_END),
    place_type: str = Form("coffee"),
    note: str = Form(""),
    study_anyway: str = Form("1"),
    sentence: str = Form(""),
):
    notice = "Calendar export is disabled. Authoritative session revision, acceptances and separate organizer approval are required. No calendar was written."
    return _redirect_session(
        course_id, starts_at, ends_at, place_type, note, study_anyway, notice, sentence=sentence
    )


