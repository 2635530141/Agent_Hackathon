"""Profile track (Stephen).

PublicProfile is the only profile object matching may read.
Street address, email, and gender stay in PrivateProfile.
"""

import re

from app import places
from app.contracts import PrivateProfile, PublicProfile
from app.store import CAMPUS_LAT, CAMPUS_LNG, add_course, courses, course_by_id, others, owner, replace_owner, student_by_id

MAX_COURSES = 8
STYLE_NOTE_LIMIT = 200
PLACE_NOTE_LIMIT = 100
GROUP_SIZES = range(2, 6)
COURSE_CODE = re.compile(r"^([A-Za-z&]{1,12}(?: [A-Za-z&]{2,12})?) ?([A-Za-z]?\d{1,3}[A-Za-z]{0,3})$")
CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{7,}\d")
STUDY_STYLES = ("quiet", "talk", "drill")
PLACE_TYPES = ("coffee", "library", "outdoor")
GENDERS = ("", "male", "female", "other")
# Form values that are not stored as a style or place kind.
NO_PREFERENCE = "any"
OTHER = "other"
STYLE_LABELS = {
    "quiet": "Quiet focus",
    "talk": "Discussion",
    "drill": "Practice questions",
    NO_PREFERENCE: "No preference",
}
PLACE_LABELS = {
    "coffee": "Cafe",
    "library": "Library",
    "outdoor": "Open space",
}
GENDER_LABELS = {"male": "Male", "female": "Female", "other": "Others"}

# Offline fallback (tests, or STUDYMATE_OFFLINE=1). Online, real addresses go to
# Google Maps or OpenStreetMap through app.places.
_KNOWN_ADDRESSES = {
    "2100 channing way": (37.8668, -122.2650),
    "2425 durant ave": (37.8678, -122.2600),
    "1800 euclid ave": (37.8760, -122.2604),
    "2600 bancroft way": (37.8688, -122.2551),
    "1920 oxford st": (37.8740, -122.2662),
    "500 grand ave": (37.8105, -122.2593),
    "39100 paseo padre pkwy": (37.5523, -121.9785),
    "uc berkeley": (CAMPUS_LAT, CAMPUS_LNG),
    "university of california berkeley": (CAMPUS_LAT, CAMPUS_LNG),
    "sather gate": (CAMPUS_LAT, CAMPUS_LNG),
}


def maps_url(lat: float, lng: float) -> str:
    return places.maps_url(lat, lng)


def geocode(address: str) -> tuple[float, float] | None:
    """Google Maps with a key, otherwise OpenStreetMap. Offline, the fallback table."""
    if places.offline():
        normalized = " ".join(address.lower().replace(",", " ").split())
        return next((point for prefix, point in _KNOWN_ADDRESSES.items() if normalized.startswith(prefix)), None)
    try:
        return places.geocode(address)
    except places.PlacesError as exc:
        raise ValueError(str(exc)) from exc


def _locate(street_address: str, current: dict) -> tuple[float, float]:
    """Radius center: the address the student gave. The address never leaves this module."""
    if not street_address:
        raise ValueError("Add an address. Your travel radius is centered on it.")
    if street_address == current["street_address"] and current.get("anchor_lat") is not None:
        return current["anchor_lat"], current["anchor_lng"]  # Unchanged address: no new lookup.
    point = geocode(street_address)
    if point is None:
        raise ValueError(f"{places.source()} could not find that address. Check the street and add the city.")
    return point


def _course_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def split_courses(value: str | list[str]) -> list[str]:
    """The form sends one text box; the API may send a list."""
    items = re.split(r"[,;\n]", value) if isinstance(value, str) else value
    return [" ".join(str(item).split()) for item in items if str(item).strip()]


def resolve_courses(names: list[str]) -> list[str]:
    """Exact course codes typed by the student, as catalog ids.

    "cs61a", "CS61A", and "CS 61A" are the same course. A code that is not in
    the catalog yet is added, so the session page and matching can use it.
    """
    if not names:
        raise ValueError("Add at least one course code, like CS 61A.")
    if len(names) > MAX_COURSES:
        raise ValueError(f"List at most {MAX_COURSES} courses.")
    ids: list[str] = []
    for name in names:
        key = _course_key(name)
        found = course_by_id(key) or next((c for c in courses() if _course_key(c["code"]) == key), None)
        if found is None:
            match = COURSE_CODE.match(name)
            if not match:
                raise ValueError(f'"{name[:40]}" is not a course code. Type codes like CS 61A or Math 54.')
            dept, number = match.groups()
            if dept.islower() or dept.isupper():
                dept = dept.upper() if len(dept) <= 4 else dept.title()
            code = f"{dept} {number.upper()}"
            found = {"id": key, "code": code, "name": code}
            add_course(found)
        if found["id"] not in ids:
            ids.append(found["id"])
    return ids


def course_codes(course_ids: list[str]) -> str:
    """The text box value: the student's courses as typed codes."""
    found = [course_by_id(course_id) for course_id in course_ids]
    return ", ".join(course["code"] for course in found if course)


def clean_style_note(text: str) -> str:
    """Free-text study style. Matching may read it, so contact details are removed."""
    text = CONTACT.sub("[removed]", " ".join(str(text).split()))
    return text[:STYLE_NOTE_LIMIT]


def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("", "0", "false", "no", "off"):
        return False
    raise ValueError("Under 18 needs a yes or no.")


def private_profile() -> PrivateProfile:
    return PrivateProfile.model_validate(owner())


def public_profile(student_id: str | None = None) -> PublicProfile:
    """The owner's profile by default, or any student's by id."""
    record = owner() if student_id is None else student_by_id(student_id)
    if record is None:
        raise KeyError(f"Unknown student {student_id}")
    return PublicProfile.model_validate(record)


def public_profiles(student_ids: list[str]) -> list[PublicProfile]:
    """Skips unknown ids so a stale id cannot break a match."""
    profiles = []
    for student_id in student_ids:
        record = student_by_id(student_id)
        if record is not None:
            profiles.append(PublicProfile.model_validate(record))
    return profiles


def email_for(student_id: str) -> str:
    """Executor only, at Send. Never pass the result to matching or the model."""
    record = student_by_id(student_id)
    if record is None:
        raise KeyError(f"Unknown student {student_id}")
    return record["email"]


def _accepts(preferences: list[str], gender: str) -> bool:
    """Nothing ticked means no preference."""
    return not preferences or gender in preferences


def eligible_student_ids() -> list[str]:
    """Gender filtering belongs here so the matcher never sees gender.

    Both sides must accept each other. A student who ticks a preference is
    not matched with someone who left their own gender blank.
    Students under 18 are not matched, in either direction.
    """
    me = owner()
    if me["under_18"]:
        return []
    return [
        person["id"]
        for person in others()
        if not person["under_18"]
        and _accepts(me["gender_preference"], person["gender_self"])
        and _accepts(person["gender_preference"], me["gender_self"])
    ]


def public_profile_of(student_id: str) -> PublicProfile | None:
    """Another student's public fields, for matching."""
    for person in others():
        if person["id"] == student_id:
            return PublicProfile.model_validate(person)
    return None


def _ticked(data: dict, key: str) -> list[str]:
    value = data.get(key) or []
    return [value] if isinstance(value, str) else list(value)


def _styles(data: dict, current: dict) -> tuple[list[str], str]:
    """Ticked study styles and the "Others" text. Nothing ticked means no preference."""
    if "study_styles" not in data and "study_style" not in data and "study_style_note" not in data:
        return list(current["study_styles"]), current["study_style_note"]
    if "study_styles" in data:
        ticked = _ticked(data, "study_styles")
    elif "study_style" in data:
        ticked = _ticked(data, "study_style")
    else:
        ticked = list(current["study_styles"])
    if set(ticked) - {*STUDY_STYLES, NO_PREFERENCE, OTHER}:
        raise ValueError("Study style was not recognized.")
    note = clean_style_note(data.get("study_style_note", current["study_style_note"]))
    if "study_styles" in data and OTHER not in ticked:
        note = ""  # The form's Others box was unticked, so its text is dropped.
    chosen = [style for style in STUDY_STYLES if style in ticked]
    if NO_PREFERENCE in ticked and (chosen or OTHER in ticked):
        raise ValueError("Tick No preference or specific study styles, not both.")
    if OTHER in ticked and not note:
        raise ValueError("Describe your other study style, or untick Others.")
    return chosen, note


def _places(data: dict, current: dict) -> tuple[list[str], str]:
    """Ticked place kinds and the "Others" text."""
    if "place_types" not in data and "place_note" not in data:
        return list(current["place_types"]), current["place_note"]
    ticked = _ticked(data, "place_types") if "place_types" in data else list(current["place_types"])
    if set(ticked) - {*PLACE_TYPES, OTHER}:
        raise ValueError("Place type was not recognized.")
    note = clean_style_note(data.get("place_note", current["place_note"]))[:PLACE_NOTE_LIMIT]
    if "place_types" in data and OTHER not in ticked:
        note = ""  # The form's Others box was unticked, so its text is dropped.
    if OTHER in ticked and not note:
        raise ValueError("Describe the other kind of place, or untick Others.")
    chosen = [place for place in PLACE_TYPES if place in ticked]
    if not chosen and not note:
        raise ValueError("Tick at least one kind of place.")
    return chosen, note


def _group_range(data: dict, current: dict) -> tuple[int, int]:
    if "group_size" in data and "group_min" not in data:
        data = {**data, "group_min": data["group_size"], "group_max": data["group_size"]}
    try:
        low = int(data.get("group_min", current["group_min"]))
        high = int(data.get("group_max", current["group_max"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Group size needs to be a number.") from exc
    if low not in GROUP_SIZES or high not in GROUP_SIZES:
        raise ValueError("Group size is 2 to 5 people.")
    if low > high:
        raise ValueError("The smallest group size cannot be larger than the largest.")
    return low, high


def update_profile(data: dict) -> PrivateProfile:
    current = owner()
    study_styles, study_style_note = _styles(data, current)
    place_types, place_note = _places(data, current)
    group_min, group_max = _group_range(data, current)
    try:
        radius = int(data.get("radius_meters", current["radius_meters"]))
        year = int(data.get("year", current["year"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Radius and year need to be numbers.") from exc
    if not 200 <= radius <= 20000:
        raise ValueError("Radius needs to be between 200 and 20000 meters.")
    if year not in range(1, 6):
        raise ValueError("Year is 1 through 5.")

    if "courses" in data:
        course_ids = resolve_courses(split_courses(data["courses"]))
    elif "course_ids" in data:
        known_courses = {course["id"] for course in courses()}
        course_ids = [item for item in data["course_ids"] or [] if item in known_courses]
        if not course_ids:
            raise ValueError("Add at least one course code, like CS 61A.")
    else:
        course_ids = list(current["course_ids"])

    gender_self = data.get("gender_self", current["gender_self"])
    if "gender_preference" in data:
        gender_preference = _ticked(data, "gender_preference")
    else:
        gender_preference = current["gender_preference"]
    if gender_self not in GENDERS or not set(gender_preference) <= set(GENDER_LABELS):
        raise ValueError("Gender or preference was not recognized.")

    major = str(data.get("major", current["major"])).strip()
    if not major:
        raise ValueError("Major is required.")

    street_address = str(data.get("street_address", current["street_address"])).strip()
    anchor_lat, anchor_lng = _locate(street_address, current)

    updated = {
        **current,
        "study_styles": study_styles,
        "study_style": study_styles[0] if study_styles else NO_PREFERENCE,
        "study_style_note": study_style_note,
        "street_address": street_address,
        "radius_meters": radius,
        "anchor": "home",
        "anchor_lat": anchor_lat,
        "anchor_lng": anchor_lng,
        "group_min": group_min,
        "group_max": group_max,
        "group_size": group_min,
        "place_types": place_types,
        "place_note": place_note,
        "major": major[:80],
        "year": year,
        "course_ids": course_ids,
        "gender_self": gender_self,
        "gender_preference": [choice for choice in GENDER_LABELS if choice in gender_preference],
        "under_18": _flag(data.get("under_18", current["under_18"])),
    }
    PrivateProfile.model_validate(updated)
    replace_owner(updated)
    return PrivateProfile.model_validate(updated)
