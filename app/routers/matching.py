from fastapi import APIRouter

from app import matching_core
from app.contracts import StudyRequest
from app.profile_core import PLACE_LABELS

router = APIRouter()

DEFAULT_START = "2026-10-27T18:00"
DEFAULT_END = "2026-10-27T21:00"


def _run(course_id: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: str):
    if place_type not in PLACE_LABELS:
        place_type = "coffee"
    request = StudyRequest(
        course_id=course_id,
        starts_at=starts_at,
        ends_at=ends_at,
        place_type=place_type,
        note=note,
        study_anyway=study_anyway == "1",
    )
    try:
        return matching_core.match(request), ""
    except matching_core.NoMatch as exc:
        return None, str(exc)


@router.get("/api/match")
def api_match(
    course_id: str = "cs61a",
    starts_at: str = DEFAULT_START,
    ends_at: str = DEFAULT_END,
    place_type: str = "coffee",
    note: str = "",
    study_anyway: str = "0",
):
    result, error = _run(course_id, starts_at, ends_at, place_type, note, study_anyway)
    if result is None:
        return {"request": None, "funnel": [], "candidates": [], "proposal": None, "message": error}
    return {
        "request": result.request,
        "funnel": result.funnel,
        "later": result.later,
        "candidates": matching_core.candidate_rows(result),
        "proposal": result.proposal,
        "message": result.message,
    }
