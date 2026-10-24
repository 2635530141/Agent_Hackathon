"""In-memory seed. Postgres replaces this module later.

Target tables: students, courses, student_courses, availability_slots, exams,
calendar_busy_intervals, requests, proposals, proposal_candidates, places_cache,
invitations, feedback.
"""

from copy import deepcopy

OWNER_ID = "u_maya"

CAMPUS_LAT = 37.8719
CAMPUS_LNG = -122.2585

COURSES = [
    {"id": "cs61a", "code": "CS 61A", "name": "Structure and Interpretation of Computer Programs"},
    {"id": "math54", "code": "Math 54", "name": "Linear Algebra and Differential Equations"},
    {"id": "cs61b", "code": "CS 61B", "name": "Data Structures"},
    {"id": "data8", "code": "Data 8", "name": "Foundations of Data Science"},
]

_owner = {
    "id": OWNER_ID,
    "name": "Maya Chen",
    "email": "maya@example.edu",
    "study_style": "quiet",
    "study_styles": ["quiet"],
    "study_style_note": "",
    "street_address": "2100 Channing Way",
    "radius_meters": 1500,
    "anchor": "home",
    "anchor_lat": 37.8668,
    "anchor_lng": -122.2650,
    "group_size": 2,
    "group_min": 2,
    "group_max": 2,
    "place_types": ["coffee", "library"],
    "place_note": "",
    "major": "EECS",
    "year": 3,
    "course_ids": ["cs61a", "math54"],
    "gender_self": "",
    "gender_preference": [],
    "under_18": False,
}


def _student(
    sid: str,
    name: str,
    *,
    major: str,
    year: int,
    courses: list[str],
    style: str = "quiet",
    places: list[str] | None = None,
    group_size: int = 2,
    anchor: str = "campus",
    lat: float = CAMPUS_LAT,
    lng: float = CAMPUS_LNG,
    radius: int = 1500,
    street: str = "",
    gender_self: str = "",
    gender_preference: list[str] | None = None,
    under_18: bool = False,
) -> dict:
    """Full PrivateProfile shape. `first_name` is kept for the proposal card."""
    return {
        "id": sid,
        "name": name,
        "first_name": name.split()[0],
        "email": f"{sid.removeprefix('u_')}@example.edu",
        "study_style": style,
        "study_styles": [style],
        "study_style_note": "",
        "street_address": street,
        "radius_meters": radius,
        "anchor": anchor,
        "anchor_lat": lat,
        "anchor_lng": lng,
        "group_size": group_size,
        "group_min": group_size,
        "group_max": group_size,
        "place_types": places or ["coffee", "library"],
        "place_note": "",
        "major": major,
        "year": year,
        "course_ids": courses,
        "gender_self": gender_self,
        "gender_preference": gender_preference or [],
        "under_18": under_18,
    }


# Eval fixture notes (README §7):
#   T1  Jordan and Priya take CS 61A.
#   T5  Jordan and Priya tie: same course, style, major, and year.
#   A6  Jordan Lee and Jordan Li have near-identical names.
#   E3  Omar lives 40 km away in Fremont but centers his radius on campus.
#   E4  Only Lena and Marcus take Data 8.
#   Gender filter: Aisha prefers to study with females, so she is eligible
#   for Maya only when Maya sets her gender to female.
#   Kai is under 18 and is never eligible.
_others = [
    # Matching seed (Jinan). These six have weekly hours, exams, and busy blocks below.
    _student("u_jordan", "Jordan Lee", major="EECS", year=3, courses=["cs61a", "math54"], radius=1200),
    _student("u_sam", "Sam Ortiz", major="Mathematics", year=2, courses=["cs61a", "math54"], style="talk", radius=2000, anchor="home", lat=37.8665, lng=-122.2600, street="2425 Durant Ave", places=["coffee", "outdoor"]),
    _student("u_priya", "Priya Nair", major="EECS", year=3, courses=["cs61a"], places=["library"]),
    _student("u_leo", "Leo Park", major="Data Science", year=1, courses=["cs61a"], style="drill", radius=1000, anchor="home", lat=37.8044, lng=-122.2712, street="500 Grand Ave, Oakland", places=["coffee"]),
    _student("u_ana", "Ana Silva", major="Mathematics", year=3, courses=["math54"], radius=2000, places=["library", "outdoor"], gender_self="female"),
    _student("u_ken", "Ken Ito", major="Physics", year=2, courses=["math54"], style="drill", anchor="home", lat=37.8770, lng=-122.2690, street="1800 Euclid Ave", group_size=3, places=["coffee"], gender_self="male"),
    # Profile edge cases (Stephen). No hours seeded yet, so matching finds no free time for them.
    _student("u_jordanli", "Jordan Li", major="Cognitive Science", year=2, courses=["cs61a"], style="talk", places=["coffee"]),
    _student("u_aisha", "Aisha Bello", major="EECS", year=3, courses=["cs61a", "math54"], gender_self="female", gender_preference=["female"]),
    _student("u_lena", "Lena Fischer", major="Statistics", year=2, courses=["data8", "math54"], style="drill", group_size=4, places=["library", "outdoor"], gender_self="female"),
    _student("u_marcus", "Marcus Hall", major="Economics", year=1, courses=["data8"], style="talk", group_size=4, places=["coffee"], gender_self="male"),
    _student("u_omar", "Omar Haddad", major="EECS", year=4, courses=["cs61b", "math54"], street="39100 Paseo Padre Pkwy, Fremont", radius=2000),
    _student("u_kai", "Kai Wong", major="Undeclared", year=1, courses=["cs61a", "math54"], under_18=True),
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
_invite = ""
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


def add_course(course: dict) -> None:
    """Students type their own course codes; new ones join the shared catalog."""
    if course_by_id(course["id"]) is None:
        COURSES.append(dict(course))


def owner() -> dict:
    return deepcopy(_owner)


def replace_owner(profile: dict) -> None:
    _owner.clear()
    _owner.update(profile)


def others() -> list[dict]:
    return deepcopy(_others)


def student_by_id(student_id: str) -> dict | None:
    if student_id == OWNER_ID:
        return owner()
    for person in _others:
        if person["id"] == student_id:
            return deepcopy(person)
    return None


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


_rooms = [
    {
        "id": "room_doe_thu",
        "host_id": "u_jordan",
        "course_id": "subject:cs",
        "course_code": "Any CS course",
        "starts_at": "2026-10-29T19:00",
        "ends_at": "2026-10-29T21:00",
        "place_type": "library",
        "place_name": "Doe Library",
        "capacity": 4,
        "members": [
            {"id": "u_jordan", "first_name": "Jordan", "major": "EECS", "year": 3},
            {"id": "u_sam", "first_name": "Sam", "major": "Mathematics", "year": 2},
        ],
    },
    {
        "id": "room_yali_thu",
        "host_id": "u_leo",
        "course_id": "cs61a",
        "course_code": "CS 61A",
        "starts_at": "2026-10-29T18:00",
        "ends_at": "2026-10-29T20:00",
        "place_type": "coffee",
        "place_name": "Yali's Cafe",
        "capacity": 2,
        "members": [
            {"id": "u_leo", "first_name": "Leo", "major": "Data Science", "year": 1},
        ],
    },
    {
        "id": "room_doe_wed",
        "host_id": "u_priya",
        "course_id": "subject:cs",
        "course_code": "Any CS course",
        "starts_at": "2026-10-28T19:00",
        "ends_at": "2026-10-28T21:00",
        "place_type": "library",
        "place_name": "Doe Library",
        "capacity": 3,
        "members": [
            {"id": "u_priya", "first_name": "Priya", "major": "EECS", "year": 3},
        ],
    },
]


def rooms() -> list[dict]:
    return deepcopy(_rooms)


def create_room(
    host: dict,
    course_id: str,
    course_code: str,
    starts_at: str,
    ends_at: str,
    place_type: str,
    place_name: str,
    capacity: int,
) -> dict:
    """Open a room and put the host in the first seat. The same host and slot is reused."""
    for room in _rooms:
        if (
            room["host_id"] == host["id"]
            and room["course_id"] == course_id
            and room["starts_at"] == starts_at
            and room["ends_at"] == ends_at
            and room["place_name"] == place_name
        ):
            return deepcopy(room)
    room = {
        "id": next_id("room"),
        "host_id": host["id"],
        "course_id": course_id,
        "course_code": course_code,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "place_type": place_type,
        "place_name": place_name,
        "capacity": max(2, min(4, capacity)),
        "members": [host],
    }
    _rooms.append(room)
    return deepcopy(room)


def join_room(room_id: str, member: dict) -> str:
    room = next((item for item in _rooms if item["id"] == room_id), None)
    if room is None:
        return "missing"
    if any(person["id"] == member["id"] for person in room["members"]):
        return "already"
    if len(room["members"]) >= room["capacity"]:
        return "full"
    room["members"].append(member)
    return "joined"


def leave_room(room_id: str, user_id: str) -> str:
    room = next((item for item in _rooms if item["id"] == room_id), None)
    if room is None:
        return "missing"
    kept = [person for person in room["members"] if person["id"] != user_id]
    if len(kept) == len(room["members"]):
        return "missing"
    if not kept:
        _rooms.remove(room)
        return "closed"
    room["members"] = kept
    return "left"
