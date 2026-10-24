"""Matching and places track (David).

`build_proposal` is the stub. Replace it with scoring and Places.
It may only use PublicProfile, eligible ids, and busy intervals without titles.
It must not read the private profile or call Google Calendar.
"""

from app.contracts import Proposal
from app.profile_core import PLACE_LABELS, eligible_student_ids, public_profile
from app.schedule_core import list_busy_for_match
from app.store import invite_text, others, person_index, place_index, places


def build_proposal(course_code: str, starts_at: str, ends_at: str, place_type: str, note: str) -> Proposal:
    """Canned proposal so the session page has a card before scoring exists.

    A group of 3 or 4 names the other members. The card shows first names only.
    """
    roster = others()
    group_size = public_profile().group_size
    partner_count = 1 if group_size == 2 else min(group_size - 1, len(roster))
    start = person_index() % len(roster)
    chosen = [roster[(start + offset) % len(roster)] for offset in range(partner_count)]
    place_options = places()
    place = place_options[place_index() % len(place_options)]
    note_bit = f' The note was used: "{note.strip()}".' if note.strip() else ""
    names = " and ".join(person["first_name"] for person in chosen)
    verb = "shares" if len(chosen) == 1 else "share"
    free = "is" if len(chosen) == 1 else "are"
    asked = PLACE_LABELS.get(place_type, place_type).lower()
    kind = PLACE_LABELS.get(place["kind"], place["kind"]).lower()
    reason = (
        f"{names} {verb} {course_code} and {free} free for this slot. "
        f"You asked for a {asked}. This suggestion is {place['name']}, a {kind}: "
        f"{place['hours']}, rating {place['rating']}.{note_bit}"
    )
    return Proposal(
        people=[
            {
                "id": person["id"],
                "first_name": person["first_name"],
                "year": person["year"],
                "major": person["major"],
                "course_code": course_code,
            }
            for person in chosen
        ],
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
