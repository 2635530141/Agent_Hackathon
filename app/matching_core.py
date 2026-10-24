"""Matching and places track (David).

`build_proposal` is the stub. Replace it with scoring and Places.
It may only use PublicProfile, eligible ids, and busy intervals without titles.
It must not read the private profile or call Google Calendar.
"""

from app.contracts import Proposal
from app.profile_core import eligible_student_ids, public_profile
from app.schedule_core import list_busy_for_match
from app.store import invite_text, others, person_index, place_index, places


def build_proposal(course_code: str, starts_at: str, ends_at: str, place_type: str, note: str) -> Proposal:
    """Canned proposal so the session page has a card before scoring exists."""
    people = others()
    person = people[person_index() % len(people)]
    place_options = places()
    place = place_options[place_index() % len(place_options)]
    note_bit = f' Note accounted for: "{note.strip()}".' if note.strip() else ""
    reason = (
        f"Shared {course_code}, inside your radius. You asked for a {place_type}; "
        f"this suggestion is a {place['kind']}. Calendar titles were not used.{note_bit}"
    )
    return Proposal(
        people=[{"id": person["id"], "name": person["name"], "detail": person["detail"]}],
        starts_at=starts_at,
        ends_at=ends_at,
        place=place,
        reason=reason,
        invite=invite_text(),
    )


def match_context() -> dict:
    profile = public_profile()
    return {
        "profile": profile,
        "eligible_ids": eligible_student_ids(),
        "busy": list_busy_for_match(),
    }
