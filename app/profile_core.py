"""Profile track (Stephen).

PublicProfile is the only profile object matching may read.
Street address, email, and gender stay in PrivateProfile.
"""

from app.contracts import PrivateProfile, PublicProfile
from app.store import courses, others, owner, replace_owner, student_by_id

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
    """
    me = owner()
    return [
        person["id"]
        for person in others()
        if _accepts(me["gender_preference"], person["gender_self"])
        and _accepts(person["gender_preference"], me["gender_self"])
    ]


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
    known_courses = {course["id"] for course in courses()}
    course_ids = [item for item in data.get("course_ids") or [] if item in known_courses]
    if not course_ids:
        course_ids = list(current["course_ids"])

    gender_self = data.get("gender_self", "")
    gender_preference = data.get("gender_preference", "")
    if gender_self not in GENDERS or gender_preference not in GENDERS:
        raise ValueError("Gender preference was not recognized.")

    major = str(data.get("major", current["major"])).strip()
    if not major:
        raise ValueError("Major is required.")

    updated = {
        **current,
        "study_style": study_style,
        "street_address": str(data.get("street_address", "")).strip(),
        "radius_meters": radius,
        "anchor": anchor,
        "group_size": group_size,
        "place_types": place_types,
        "major": major[:80],
        "year": year,
        "course_ids": course_ids,
        "gender_self": gender_self,
        "gender_preference": gender_preference,
    }
    PrivateProfile.model_validate(updated)
    replace_owner(updated)
    return PrivateProfile.model_validate(updated)
