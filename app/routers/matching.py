from fastapi import APIRouter, Request

from app import matching_core, store
from app.contracts import StudyRequest
from app.format import pretty_range
from app.profile_core import PLACE_LABELS, STYLE_LABELS
from app.web import render

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
        return matching_core.filter_preview(request), ""
    except matching_core.NoMatch as exc:
        return None, str(exc)


@router.get("/matching")
def matching_page(
    request: Request,
    course_id: str = "cs61a",
    starts_at: str = DEFAULT_START,
    ends_at: str = DEFAULT_END,
    place_type: str = "coffee",
    note: str = "",
    study_anyway: str = "0",
):
    context = matching_core.match_context()
    result, error = _run(course_id, starts_at, ends_at, place_type, note, study_anyway)
    busy_rows = [
        {"when": pretty_range(block.starts_at, block.ends_at), "source": block.source, "kind": block.kind or "busy"}
        for block in context["busy"]
    ]
    proposal = result.proposal if result else None
    return render(
        request,
        "matching.html",
        nav="matching",
        profile=context["profile"],
        eligible_ids=context["eligible_ids"],
        busy_rows=busy_rows,
        courses=store.courses(),
        form={
            "course_id": course_id,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "place_type": place_type,
            "note": note,
            "study_anyway": study_anyway == "1",
        },
        result=result,
        rows=matching_core.candidate_rows(result) if result else [],
        error=error or (result.message if result else ""),
        proposal=proposal,
        when=pretty_range(proposal.starts_at, proposal.ends_at) if proposal else "",
        style_labels=STYLE_LABELS,
        place_labels=PLACE_LABELS,
    )


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
