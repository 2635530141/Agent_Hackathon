"""Profile track (Stephen).

PublicProfile is the only profile object matching may read.
Street address, email, and gender stay in PrivateProfile.
"""

import os
import re
from pathlib import Path

import httpx

from app.contracts import PrivateProfile, PublicProfile
from app.store import CAMPUS_LAT, CAMPUS_LNG, add_course, courses, course_by_id, others, owner, replace_owner, student_by_id

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
# Bias Google results toward the campus area so "2425 Durant Ave" means Berkeley.
GEOCODE_BOUNDS = "37.80,-122.33|37.93,-122.18"
MAX_COURSES = 8
STYLE_NOTE_LIMIT = 200
COURSE_CODE = re.compile(r"^([A-Za-z&]{1,12}(?: [A-Za-z&]{2,12})?) ?([A-Za-z]?\d{1,3}[A-Za-z]{0,3})$")
CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s().-]{7,}\d")
STUDY_STYLES = ("quiet", "talk", "drill")
ANCHORS = ("home", "campus")
PLACE_TYPES = ("coffee", "library", "outdoor")
GENDERS = ("", "woman", "man", "nonbinary")
STYLE_LABELS = {
    "quiet": "Quiet focus",
    "talk": "Talking problems through",
    "drill": "Drilling practice questions",
}
PLACE_LABELS = {
    "coffee": "Coffee shop",
    "library": "Library",
    "outdoor": "Outdoor",
}

# Fallback when no Google Maps key is set, so the demo and tests run offline.
# Keys are lowercase street prefixes, matched against the start of the address.
_KNOWN_ADDRESSES = {
    "2100 channing way": (37.8668, -122.2650),
    "2425 durant ave": (37.8678, -122.2600),
    "1800 euclid ave": (37.8760, -122.2604),
    "2600 bancroft way": (37.8688, -122.2551),
    "1920 oxford st": (37.8740, -122.2662),
    "500 grand ave": (37.8105, -122.2593),
    "39100 paseo padre pkwy": (37.5523, -121.9785),
}


def _maps_key() -> str:
    if os.environ.get("GOOGLE_MAPS_API_KEY"):
        return os.environ["GOOGLE_MAPS_API_KEY"]
    dotenv = Path(__file__).resolve().parent.parent / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "GOOGLE_MAPS_API_KEY":
                return value.strip().strip('"').strip("'")
    return ""


def maps_connected() -> bool:
    return bool(_maps_key())


def maps_url(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


def _google_geocode(address: str, key: str) -> tuple[float, float] | None:
    try:
        response = httpx.get(
            GEOCODE_URL,
            params={"address": address, "key": key, "region": "us", "bounds": GEOCODE_BOUNDS},
            timeout=5,
        )
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError("Google Maps could not be reached. Try again or center on campus.") from exc
    status = data.get("status")
    if status == "ZERO_RESULTS":
        return None
    if status != "OK":
        raise ValueError(f"Google Maps could not look up the address ({status}). Try again or center on campus.")
    location = data["results"][0]["geometry"]["location"]
    return location["lat"], location["lng"]


def geocode(address: str) -> tuple[float, float] | None:
    """Google Geocoding when GOOGLE_MAPS_API_KEY is set, otherwise the fallback table."""
    key = _maps_key()
    if key:
        return _google_geocode(address, key)
    normalized = " ".join(address.lower().replace(",", " ").split())
    for prefix, point in _KNOWN_ADDRESSES.items():
        if normalized.startswith(prefix):
            return point
    return None


def _anchor_point(anchor: str, street_address: str) -> tuple[float, float]:
    """Radius center. The address itself never leaves this module."""
    if anchor == "campus":
        return CAMPUS_LAT, CAMPUS_LNG
    if not street_address:
        raise ValueError("Add a street address to center the radius on home.")
    point = geocode(street_address)
    if point is None:
        raise ValueError("Google Maps could not find that address. Check it or center on campus.")
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


def _accepts(preference: str, gender: str) -> bool:
    return not preference or preference == gender


def eligible_student_ids() -> list[str]:
    """Gender filtering belongs here so the matcher never sees gender.

    Both sides must accept each other. A student who sets a preference is
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


def update_profile(data: dict) -> PrivateProfile:
    current = owner()
    study_style = data.get("study_style", current["study_style"])
    if study_style not in STUDY_STYLES:
        raise ValueError("Choose a study style.")
    anchor = data.get("anchor", current["anchor"])
    if anchor not in ANCHORS:
        raise ValueError("Choose home or campus for the radius.")
    try:
        radius = int(data.get("radius_meters", current["radius_meters"]))
        group_size = int(data.get("group_size", current["group_size"]))
        year = int(data.get("year", current["year"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Radius, group size, and year need to be numbers.") from exc
    if not 200 <= radius <= 20000:
        raise ValueError("Radius needs to be between 200 and 20000 meters.")
    if group_size not in (2, 3, 4):
        raise ValueError("Group size is 2, 3, or 4.")
    if year not in range(1, 6):
        raise ValueError("Year is 1 through 5.")

    place_types = [item for item in data.get("place_types") or [] if item in PLACE_TYPES]
    if not place_types:
        place_types = list(current["place_types"])
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
    gender_preference = data.get("gender_preference", current["gender_preference"])
    if gender_self not in GENDERS or gender_preference not in GENDERS:
        raise ValueError("Gender preference was not recognized.")

    major = str(data.get("major", current["major"])).strip()
    if not major:
        raise ValueError("Major is required.")

    street_address = str(data.get("street_address", current["street_address"])).strip()
    anchor_lat, anchor_lng = _anchor_point(anchor, street_address)

    updated = {
        **current,
        "study_style": study_style,
        "study_style_note": clean_style_note(data.get("study_style_note", current["study_style_note"])),
        "street_address": street_address,
        "radius_meters": radius,
        "anchor": anchor,
        "anchor_lat": anchor_lat,
        "anchor_lng": anchor_lng,
        "group_size": group_size,
        "place_types": place_types,
        "major": major[:80],
        "year": year,
        "course_ids": course_ids,
        "gender_self": gender_self,
        "gender_preference": gender_preference,
        "under_18": _flag(data.get("under_18", current["under_18"])),
    }
    PrivateProfile.model_validate(updated)
    replace_owner(updated)
    return PrivateProfile.model_validate(updated)
