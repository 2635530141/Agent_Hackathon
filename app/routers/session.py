from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app import matching_core, schedule_core, store
from app.format import pretty_range
from app.profile_core import PLACE_LABELS
from app.web import render

router = APIRouter()

DEFAULT_START = "2026-10-29T18:00"
DEFAULT_END = "2026-10-29T21:00"


def _query(course_id: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: bool) -> str:
    return urlencode(
        {
            "course_id": course_id,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "place_type": place_type,
            "note": note,
            "study_anyway": "1" if study_anyway else "0",
        }
    )


def _session_context(course_id: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: bool):
    course = store.course_by_id(course_id) or store.courses()[0]
    error = ""
    conflict = None
    try:
        conflict = schedule_core.own_conflict(starts_at, ends_at, study_anyway)
    except ValueError as exc:
        error = str(exc)
    proposal = None
    when = ""
    if conflict is None and not error:
        proposal = matching_core.build_proposal(course["code"], starts_at, ends_at, place_type, note)
        when = pretty_range(proposal.starts_at, proposal.ends_at)
    invitation = store.invitation()
    return {
        "course": course,
        "courses": store.courses(),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "place_type": place_type,
        "place_labels": PLACE_LABELS,
        "note": note,
        "study_anyway": study_anyway,
        "error": error,
        "conflict": conflict,
        "conflict_when": pretty_range(starts_at, ends_at) if conflict else "",
        "alternative_label": pretty_range(conflict["alternative_start"], conflict["alternative_end"])
        if conflict
        else "",
        "alternative_href": "/session?"
        + _query(
            course["id"],
            conflict["alternative_start"],
            conflict["alternative_end"],
            place_type,
            note,
            False,
        )
        if conflict
        else "",
        "anyway_href": "/session?" + _query(course["id"], starts_at, ends_at, place_type, note, True),
        "proposal": proposal,
        "when": when,
        "invitation": invitation,
        "keep": _query(course["id"], starts_at, ends_at, place_type, note, study_anyway),
    }


@router.get("/session")
def session_page(
    request: Request,
    course_id: str = "cs61a",
    starts_at: str = DEFAULT_START,
    ends_at: str = DEFAULT_END,
    place_type: str = "coffee",
    note: str = "",
    study_anyway: str = "0",
    notice: str = "",
    discarded: str = "0",
):
    context = _session_context(course_id, starts_at, ends_at, place_type, note, study_anyway == "1")
    if discarded == "1":
        context["proposal"] = None
        context["conflict"] = None
        notice = notice or "Request discarded. Nothing was sent."
    return render(request, "session.html", nav="session", notice=notice, **context)


def _redirect_session(
    course_id: str,
    starts_at: str,
    ends_at: str,
    place_type: str,
    note: str,
    study_anyway: str,
    notice: str = "",
) -> RedirectResponse:
    query = _query(course_id, starts_at, ends_at, place_type, note, study_anyway == "1")
    if notice:
        query += "&notice=" + quote(notice)
    return RedirectResponse(f"/session?{query}", status_code=303)


@router.post("/session/draft")
def save_draft(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
):
    store.set_invite_text(invite)
    return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway, "Draft saved.")


@router.post("/session/swap-person")
def swap_person(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
):
    store.set_invite_text(invite)
    store.cycle_person()
    return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway)


@router.post("/session/swap-place")
def swap_place(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
):
    store.set_invite_text(invite)
    store.cycle_place()
    return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway)


@router.post("/session/confirm")
def confirm_invite(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
):
    store.set_invite_text(invite)
    course = store.course_by_id(course_id) or store.courses()[0]
    proposal = matching_core.build_proposal(course["code"], starts_at, ends_at, place_type, note)
    store.set_invitation_details(course["id"], starts_at, ends_at, proposal.place.name)
    store.set_invitation_status("pending")
    return _redirect_session(
        course_id,
        starts_at,
        ends_at,
        place_type,
        note,
        study_anyway,
        "Invitation sent for this demo. Nothing is emailed until an email provider is connected.",
    )


@router.post("/session/calendar")
def add_to_calendar(
    course_id: str = Form("cs61a"),
    starts_at: str = Form(DEFAULT_START),
    ends_at: str = Form(DEFAULT_END),
    place_type: str = Form("coffee"),
    note: str = Form(""),
    study_anyway: str = Form("1"),
):
    if store.invitation()["status"] == "accepted":
        store.write_calendar_event()
        notice = "Saved to the organizer's calendar in this demo. Google write is not connected."
    else:
        notice = "Both people have to accept before the session goes on the calendar."
    return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway, notice)


@router.get("/invite/{invitation_id}")
def invite_page(request: Request, invitation_id: str):
    invitation = store.invitation()
    course = store.course_by_id(invitation["course_id"]) or store.courses()[0]
    return render(
        request,
        "invite.html",
        nav="invite",
        invitation_id=invitation_id,
        invitation=invitation,
        course=course,
        place_name=invitation["place_name"],
        when=pretty_range(invitation["starts_at"], invitation["ends_at"]),
        known=invitation_id == invitation["id"],
    )


@router.post("/invite/{invitation_id}/accept")
def accept_invite(invitation_id: str):
    if invitation_id == store.invitation()["id"] and store.invitation()["status"] == "pending":
        store.set_invitation_status("accepted")
    return RedirectResponse(f"/invite/{invitation_id}", status_code=303)


@router.post("/invite/{invitation_id}/decline")
def decline_invite(invitation_id: str):
    if invitation_id == store.invitation()["id"] and store.invitation()["status"] == "pending":
        store.set_invitation_status("declined")
    return RedirectResponse(f"/invite/{invitation_id}", status_code=303)
