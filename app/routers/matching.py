from fastapi import APIRouter, Request

from app import matching_core
from app.format import pretty_range
from app.profile_core import PLACE_LABELS, STYLE_LABELS
from app.web import render

router = APIRouter()


@router.get("/matching")
def matching_page(request: Request):
    context = matching_core.match_context()
    proposal = matching_core.build_proposal("CS 189", "2026-10-29T19:00", "2026-10-29T21:00", "coffee", "")
    busy_rows = [
        {"when": pretty_range(block.starts_at, block.ends_at), "source": block.source, "kind": block.kind or "busy"}
        for block in context["busy"]
    ]
    return render(
        request,
        "matching.html",
        nav="matching",
        profile=context["profile"],
        eligible_ids=context["eligible_ids"],
        busy_rows=busy_rows,
        proposal=proposal,
        when=pretty_range(proposal.starts_at, proposal.ends_at),
        style_labels=STYLE_LABELS,
        place_labels=PLACE_LABELS,
    )


@router.get("/api/match")
def api_match():
    context = matching_core.match_context()
    proposal = matching_core.build_proposal("CS 189", "2026-10-29T19:00", "2026-10-29T21:00", "coffee", "")
    return {
        "profile": context["profile"],
        "eligible_ids": context["eligible_ids"],
        "busy": context["busy"],
        "proposal": proposal,
    }
