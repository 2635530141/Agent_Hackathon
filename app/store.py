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
    {
        "id": "u_jordan",
        "name": "Jordan Lee",
        "first_name": "Jordan",
        "year": 3,
        "major": "EECS",
        "study_style": "quiet",
        "radius_meters": 1200,
        "anchor": "campus",
        "anchor_lat": 37.8719,
        "anchor_lng": -122.2585,
        "group_size": 2,
        "place_types": ["coffee", "library"],
        "course_ids": ["cs61a", "math54"],
    },
    {
        "id": "u_sam",
        "name": "Sam Ortiz",
        "first_name": "Sam",
        "year": 2,
        "major": "Mathematics",
        "study_style": "talk",
        "radius_meters": 2000,
        "anchor": "home",
        "anchor_lat": 37.8665,
        "anchor_lng": -122.2600,
        "group_size": 2,
        "place_types": ["coffee", "outdoor"],
        "course_ids": ["cs61a", "math54"],
    },
    {
        "id": "u_priya",
        "name": "Priya Nair",
        "first_name": "Priya",
        "year": 3,
        "major": "EECS",
        "study_style": "quiet",
        "radius_meters": 1500,
        "anchor": "campus",
        "anchor_lat": 37.8719,
        "anchor_lng": -122.2585,
        "group_size": 2,
        "place_types": ["library"],
        "course_ids": ["cs61a"],
    },
    {
        "id": "u_leo",
        "name": "Leo Park",
        "first_name": "Leo",
        "year": 1,
        "major": "Data Science",
        "study_style": "drill",
        "radius_meters": 1000,
        "anchor": "home",
        "anchor_lat": 37.8044,
        "anchor_lng": -122.2712,
        "group_size": 2,
        "place_types": ["coffee"],
        "course_ids": ["cs61a"],
    },
    {
        "id": "u_ana",
        "name": "Ana Silva",
        "first_name": "Ana",
        "year": 3,
        "major": "Mathematics",
        "study_style": "quiet",
        "radius_meters": 2000,
        "anchor": "campus",
        "anchor_lat": 37.8719,
        "anchor_lng": -122.2585,
        "group_size": 2,
        "place_types": ["library", "outdoor"],
        "course_ids": ["math54"],
    },
    {
        "id": "u_ken",
        "name": "Ken Ito",
        "first_name": "Ken",
        "year": 2,
        "major": "Physics",
        "study_style": "drill",
        "radius_meters": 1500,
        "anchor": "home",
        "anchor_lat": 37.8770,
        "anchor_lng": -122.2690,
        "group_size": 3,
        "place_types": ["coffee"],
        "course_ids": ["math54"],
    },
]

_places = [
    {
        "name": "Yali's Cafe",
        "address": "1920 Oxford St",
        "kind": "coffee",
        "hours": "Open until 9:00 PM",
        "rating": "4.4",
        "map_url": "https://maps.google.com/?q=Yali%27s+Cafe+Berkeley",
        "lat": 37.8735,
        "lng": -122.2662,
        "opens": "07:00",
        "closes": "21:00",
    },
    {
        "name": "Doe Library",
        "address": "Campus",
        "kind": "library",
        "hours": "Open until 10:00 PM",
        "rating": "4.6",
        "map_url": "https://maps.google.com/?q=Doe+Library+Berkeley",
        "lat": 37.8722,
        "lng": -122.2593,
        "opens": "08:00",
        "closes": "22:00",
    },
    {
        "name": "Faculty Glade",
        "address": "Campus",
        "kind": "outdoor",
        "hours": "Daylight hours",
        "rating": "4.7",
        "map_url": "https://maps.google.com/?q=Faculty+Glade+Berkeley",
        "lat": 37.8714,
        "lng": -122.2560,
        "opens": "07:00",
        "closes": "18:00",
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

_student_weekly = {
    "u_jordan": {"tue": ("18:00", "22:00"), "wed": ("19:00", "21:00"), "thu": ("18:00", "21:00")},
    "u_sam": {"tue": ("17:00", "21:00"), "wed": ("16:00", "20:00"), "thu": ("19:00", "22:00")},
    "u_priya": {"tue": ("19:00", "21:00"), "thu": ("17:00", "21:00")},
    "u_leo": {"tue": ("18:00", "21:00"), "thu": ("18:00", "21:00")},
    "u_ana": {"mon": ("17:00", "20:00"), "wed": ("17:00", "21:00"), "thu": ("16:00", "20:00")},
    "u_ken": {"tue": ("18:00", "21:00"), "wed": ("18:00", "21:00")},
}

_student_exams = {
    "u_sam": [
        {"id": "exam_sam", "course_id": "math54", "starts_at": "2026-11-06T09:00", "ends_at": "2026-11-06T12:00", "priority": True}
    ],
    "u_ana": [
        {"id": "exam_ana", "course_id": "math54", "starts_at": "2026-11-06T09:00", "ends_at": "2026-11-06T12:00", "priority": True}
    ],
}

_student_busy = {
    "u_jordan": [
        {"id": "cal_j1", "source": "calendar", "starts_at": "2026-10-27T18:00", "ends_at": "2026-10-27T19:00", "title": "Lab section"},
    ],
    "u_sam": [
        {"id": "exam_sam", "source": "exam", "starts_at": "2026-11-06T09:00", "ends_at": "2026-11-06T12:00", "title": "Math 54 exam"},
    ],
    "u_priya": [
        {"id": "cal_p1", "source": "calendar", "starts_at": "2026-10-29T19:00", "ends_at": "2026-10-29T21:00", "title": "Work shift"},
    ],
    "u_ana": [
        {"id": "exam_ana", "source": "exam", "starts_at": "2026-11-06T09:00", "ends_at": "2026-11-06T12:00", "title": "Math 54 exam"},
    ],
}
_person_index = 0
_place_index = 0
_invite = "Want to work through CS 61A problem sets at Yali's Cafe on Thursday at 7?"
_invitations: dict[str, dict] = {}
_latest_batch_ids: list[str] = []
_calendar_written = False
_calendar_connected = False
_draft = {"key": "", "texts": []}
_swap_key = ""
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


def swaps_for(key: str) -> tuple[int, int]:
    """Person and place swap counts, reset when the request changes."""
    global _swap_key, _person_index, _place_index
    if key != _swap_key:
        _swap_key = key
        _person_index = 0
        _place_index = 0
    return _person_index, _place_index


def cycle_person() -> None:
    global _person_index
    _person_index = (_person_index + 1) % len(_others)


def cycle_place() -> None:
    global _place_index
    _place_index = (_place_index + 1) % len(_places)


def weekly(student_id: str = OWNER_ID) -> dict[str, tuple[str, str]]:
    if student_id != OWNER_ID:
        return deepcopy(_student_weekly.get(student_id, {}))
    return deepcopy(_weekly)


def replace_weekly(hours: dict[str, tuple[str, str]]) -> None:
    _weekly.clear()
    _weekly.update(hours)


def exams(student_id: str = OWNER_ID) -> list[dict]:
    if student_id != OWNER_ID:
        return deepcopy(_student_exams.get(student_id, []))
    return deepcopy(_exams)


def add_exam(exam: dict, block: dict) -> None:
    _exams.append(exam)
    _busy.append(block)


def delete_exam(exam_id: str) -> None:
    _exams[:] = [exam for exam in _exams if exam["id"] != exam_id]
    _busy[:] = [block for block in _busy if block["id"] != exam_id]


def busy(student_id: str = OWNER_ID) -> list[dict]:
    if student_id != OWNER_ID:
        return deepcopy(_student_busy.get(student_id, []))
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


def generated_drafts(key: str) -> list[str]:
    """Invites matching generated for this request, so an untouched draft can be regenerated."""
    return list(_draft["texts"]) if _draft["key"] == key else []


def remember_draft(key: str, text: str) -> None:
    if _draft["key"] != key:
        _draft["key"] = key
        _draft["texts"] = []
    _draft["texts"].append(text)


def create_invitations(records: list[dict]) -> list[dict]:
    """Freeze one pending invite per person. Later edits do not change these rows."""
    global _calendar_written
    created = []
    ids = []
    for record in records:
        item = {"id": next_id("inv"), "status": "pending", **record}
        _invitations[item["id"]] = item
        ids.append(item["id"])
        created.append(deepcopy(item))
    _latest_batch_ids[:] = ids
    _calendar_written = False
    return created


def latest_invitations() -> list[dict]:
    return [deepcopy(_invitations[item_id]) for item_id in _latest_batch_ids]


def invitation_by_id(invitation_id: str) -> dict | None:
    item = _invitations.get(invitation_id)
    return deepcopy(item) if item else None


def set_person_status(invitation_id: str, status: str) -> None:
    item = _invitations.get(invitation_id)
    if item and item["status"] == "pending" and status in ("accepted", "declined"):
        item["status"] = status


def batch_all_accepted() -> bool:
    items = latest_invitations()
    return bool(items) and all(item["status"] == "accepted" for item in items)


def declined_names() -> list[str]:
    return [item["first_name"] for item in latest_invitations() if item["status"] == "declined"]


def calendar_event_written() -> bool:
    return _calendar_written


def write_calendar_event() -> bool:
    global _calendar_written
    if not batch_all_accepted():
        return False
    _calendar_written = True
    return True


def calendar_connected() -> bool:
    return _calendar_connected


def set_calendar_connected(connected: bool) -> None:
    global _calendar_connected
    _calendar_connected = connected
