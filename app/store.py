"""In-memory seed. Postgres replaces this module later.

Target tables: students, courses, student_courses, availability_slots, exams,
calendar_busy_intervals, requests, proposals, proposal_candidates, places_cache,
invitations, feedback.
"""

from copy import deepcopy

OWNER_ID = "u_maya"

COURSES = [
    {"id": "cs61a", "code": "CS 61A", "name": "Structure and Interpretation of Computer Programs"},
    {"id": "math54", "code": "Math 54", "name": "Linear Algebra and Differential Equations"},
]

_owner = {
    "id": OWNER_ID,
    "name": "Maya Chen",
    "email": "maya@example.edu",
    "study_style": "quiet",
    "street_address": "2100 Channing Way",
    "radius_meters": 1500,
    "anchor": "campus",
    "anchor_lat": 37.8719,
    "anchor_lng": -122.2585,
    "group_size": 2,
    "place_types": ["coffee", "library"],
    "major": "EECS",
    "year": 3,
    "course_ids": ["cs61a", "math54"],
    "gender_self": "",
    "gender_preference": "",
}

_others = [
    {"id": "u_jordan", "first_name": "Jordan", "year": 3, "major": "EECS"},
    {"id": "u_sam", "first_name": "Sam", "year": 2, "major": "Mathematics"},
]

_places = [
    {
        "name": "Yali's Cafe",
        "address": "1920 Oxford St",
        "kind": "coffee",
        "hours": "Open until 9:00 PM",
        "rating": "4.4",
        "map_url": "https://maps.google.com/?q=Yali%27s+Cafe+Berkeley",
    },
    {
        "name": "Doe Library",
        "address": "Campus",
        "kind": "library",
        "hours": "Open until 10:00 PM",
        "rating": "4.6",
        "map_url": "https://maps.google.com/?q=Doe+Library+Berkeley",
    },
    {
        "name": "Faculty Glade",
        "address": "Campus",
        "kind": "outdoor",
        "hours": "Daylight hours",
        "rating": "4.7",
        "map_url": "https://maps.google.com/?q=Faculty+Glade+Berkeley",
    },
]

_weekly = {
    "mon": ("", ""),
    "tue": ("18:00", "21:00"),
    "wed": ("", ""),
    "thu": ("16:00", "21:00"),
    "fri": ("15:00", "18:00"),
    "sat": ("", ""),
    "sun": ("", ""),
}

_exams = [
    {
        "id": "exam_1",
        "course_id": "math54",
        "starts_at": "2026-11-06T09:00",
        "ends_at": "2026-11-06T12:00",
        "priority": True,
    }
]

_busy = [
    {
        "id": "week_thu",
        "source": "weekly",
        "starts_at": "2026-10-29T16:00",
        "ends_at": "2026-10-29T21:00",
        "title": None,
    },
    {
        "id": "cal_1",
        "source": "calendar",
        "starts_at": "2026-10-29T18:00",
        "ends_at": "2026-10-29T19:00",
        "title": "CS 61A lecture",
    },
    {
        "id": "exam_1",
        "source": "exam",
        "starts_at": "2026-11-06T09:00",
        "ends_at": "2026-11-06T12:00",
        "title": "Math 54 exam",
    },
]

_exceptions: list[dict] = []
_person_index = 0
_place_index = 0
_invite = "Want to work through CS 61A problem sets at Yali's Cafe on Thursday at 7?"
_invitation = {
    "id": "demo",
    "status": "draft",
    "calendar_written": False,
    "course_id": "cs61a",
    "starts_at": "2026-10-29T19:00",
    "ends_at": "2026-10-29T21:00",
    "place_name": "Yali's Cafe",
}
_calendar_connected = False
_seq = 20


def next_id(prefix: str) -> str:
    global _seq
    _seq += 1
    return f"{prefix}_{_seq}"


def courses() -> list[dict]:
    return deepcopy(COURSES)


def course_by_id(course_id: str) -> dict | None:
    for course in COURSES:
        if course["id"] == course_id:
            return deepcopy(course)
    return None


def owner() -> dict:
    return deepcopy(_owner)


def replace_owner(profile: dict) -> None:
    _owner.clear()
    _owner.update(profile)


def others() -> list[dict]:
    return deepcopy(_others)


def places() -> list[dict]:
    return deepcopy(_places)


def person_index() -> int:
    return _person_index


def place_index() -> int:
    return _place_index


def cycle_person() -> None:
    global _person_index
    _person_index = (_person_index + 1) % len(_others)


def cycle_place() -> None:
    global _place_index
    _place_index = (_place_index + 1) % len(_places)


def weekly() -> dict[str, tuple[str, str]]:
    return deepcopy(_weekly)


def replace_weekly(hours: dict[str, tuple[str, str]]) -> None:
    _weekly.clear()
    _weekly.update(hours)


def exams() -> list[dict]:
    return deepcopy(_exams)


def add_exam(exam: dict, block: dict) -> None:
    _exams.append(exam)
    _busy.append(block)


def delete_exam(exam_id: str) -> None:
    _exams[:] = [exam for exam in _exams if exam["id"] != exam_id]
    _busy[:] = [block for block in _busy if block["id"] != exam_id]


def busy() -> list[dict]:
    return deepcopy(_busy)


def add_exception(block: dict) -> None:
    _exceptions.append(block)
    _busy.append(block)


def delete_exception(exception_id: str) -> None:
    _exceptions[:] = [block for block in _exceptions if block["id"] != exception_id]
    _busy[:] = [block for block in _busy if block["id"] != exception_id]


def exceptions() -> list[dict]:
    return deepcopy(_exceptions)


def invite_text() -> str:
    return _invite


def set_invite_text(text: str) -> None:
    global _invite
    _invite = text.strip()


def invitation() -> dict:
    return deepcopy(_invitation)


def set_invitation_status(status: str) -> None:
    _invitation["status"] = status
    if status != "accepted":
        _invitation["calendar_written"] = False


def set_invitation_details(course_id: str, starts_at: str, ends_at: str, place_name: str) -> None:
    _invitation["course_id"] = course_id
    _invitation["starts_at"] = starts_at
    _invitation["ends_at"] = ends_at
    _invitation["place_name"] = place_name


def write_calendar_event() -> None:
    _invitation["calendar_written"] = True


def calendar_connected() -> bool:
    return _calendar_connected


def set_calendar_connected(connected: bool) -> None:
    global _calendar_connected
    _calendar_connected = connected
