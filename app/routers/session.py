from datetime import datetime
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app import matching_core, schedule_core, store
from app.format import pretty_range
from app.omnara_client import OmnaraError, interpret_request
from app.profile_core import PLACE_LABELS, public_profile
from app.web import render
from app.scheduling.session_hooks import refresh_before_request, export_card

router = APIRouter()

DEFAULT_START = "2026-10-29T18:00"
DEFAULT_END = "2026-10-29T21:00"


def _query(
    course_id: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: bool, sentence: str
) -> str:
    return urlencode(
        {
            "sentence": sentence,
            "ready": "1",
            "course_id": course_id,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "place_type": place_type,
            "note": note,
            "study_anyway": "1" if study_anyway else "0",
        }
    )


def _profile_courses() -> list[dict]:
    owned = set(public_profile().course_ids)
    return [course for course in store.courses() if course["id"] in owned]


def _clock(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _course_record(course_id: str) -> dict:
    found = store.course_by_id(course_id)
    if found:
        return found
    label = course_id.strip() or "Course"
    return {"id": label, "code": label, "name": label}


def _parsed(raw: dict, courses: list[dict]) -> dict:
    by_id = {course["id"]: course for course in courses}
    by_code = {}
    for course in courses:
        by_code[course["code"].lower()] = course
        by_code[course["code"].lower().replace(" ", "")] = course
    named = str(raw.get("course_id") or raw.get("course") or "").strip()
    if not named:
        raise ValueError("That sentence did not name a course.")
    compact = named.lower().replace(" ", "")
    course = by_id.get(named) or by_code.get(named.lower()) or by_code.get(compact)
    place_type = str(raw.get("place_type") or "coffee")
    if place_type not in PLACE_LABELS:
        place_type = "coffee"
    starts_at = _clock(str(raw.get("starts_at") or ""))
    ends_at = _clock(str(raw.get("ends_at") or ""))
    if starts_at is None or ends_at is None or ends_at <= starts_at:
        raise ValueError("Omnara did not return a usable time.")
    return {
        "course_id": course["id"] if course else named,
        "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
        "ends_at": ends_at.strftime("%Y-%m-%dT%H:%M"),
        "place_type": place_type,
        "note": str(raw.get("note") or "")[:300],
    }


def _session_context(
    course_id: str, starts_at: str, ends_at: str, place_type: str, note: str, study_anyway: bool, sentence: str
):
    course = _course_record(course_id)
    error = ""
    conflict = None
    try:
        conflict = schedule_core.own_conflict(starts_at, ends_at, study_anyway)
    except ValueError as exc:
        error = str(exc)
    proposal = None
    when = ""
    try:
        asked_when = pretty_range(starts_at, ends_at)
    except ValueError:
        asked_when = ""
    if conflict is None and not error:
        try:
            proposal = matching_core.build_proposal(course["code"], starts_at, ends_at, place_type, note, study_anyway)
            when = pretty_range(proposal.starts_at, proposal.ends_at)
        except matching_core.NoMatch as exc:
            error = str(exc)
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
        if conflict and conflict.get("alternative_start")
        else "",
        "alternative_href": "/session?"
        + _query(
            course["id"],
            conflict["alternative_start"],
            conflict["alternative_end"],
            place_type,
            note,
            False,
            sentence,
        )
        if conflict and conflict.get("alternative_start")
        else "",
        "anyway_href": "/session?" + _query(course["id"], starts_at, ends_at, place_type, note, True, sentence),
        "proposal": proposal,
        "when": when,
        "asked_when": asked_when,
        "invites": store.latest_invitations(),
        "all_accepted": store.batch_all_accepted(),
        "declined_names": store.declined_names(),
        "calendar_written": store.calendar_event_written(),
        "sentence": sentence,
        "ready": True,
        "keep": _query(course["id"], starts_at, ends_at, place_type, note, study_anyway, sentence),
    }


def _form_only(request: Request, sentence: str, error: str = "", notice: str = ""):
    from app.routers.agent import csrf
    csrf(request)
    catalog = _profile_courses() or store.courses()
    return render(
        request,
        "session.html",
        nav="session",
        sentence=sentence,
        ready=False,
        course=catalog[0],
        courses=catalog,
        starts_at="",
        ends_at="",
        place_type="coffee",
        place_labels=PLACE_LABELS,
        note="",
        study_anyway=False,
        error=error,
        notice=notice,
        calendar_export=export_card(request),
        conflict=None,
        proposal=None,
        when="",
        invites=store.latest_invitations(),
        all_accepted=store.batch_all_accepted(),
        declined_names=store.declined_names(),
        calendar_written=store.calendar_event_written(),
    )


@router.get("/session")
def session_page(
    request: Request,
    sentence: str = "",
    ready: str = "0",
    course_id: str = "cs61a",
    starts_at: str = DEFAULT_START,
    ends_at: str = DEFAULT_END,
    place_type: str = "coffee",
    note: str = "",
    study_anyway: str = "0",
    notice: str = "",
    discarded: str = "0",
    agent_request_id: str = "",
):
    if agent_request_id:
        from app.routers.agent import page, load
        from app.scheduling.demo import demo_owner
        from app.study_agent.service import AgentService
        actor = demo_owner(request)
        return page(request,load(AgentService(),agent_request_id,actor.subject_id))
    if ready != "1":
        return _form_only(request, sentence, notice=notice)
    context = _session_context(course_id, starts_at, ends_at, place_type, note, study_anyway == "1", sentence)
    if discarded == "1":
        context["proposal"] = None
        context["conflict"] = None
        notice = notice or "Request discarded. Nothing was sent."
    return render(request, "session.html", nav="session", notice=notice, calendar_export=export_card(request), **context)


@router.post("/session/interpret")
def interpret(request: Request, sentence: str = Form(""), mode: str = Form("legacy"), csrf_token: str = Form("")):
    from app.scheduling.demo import demo_owner
    demo_owner(request)
    text = sentence.strip()
    if not text:
        return _form_only(request, "", error="Write what you want to study.")
    if mode != "legacy":
        from app.routers.agent import verify
        from app.scheduling.demo import demo_owner
        from app.study_agent.service import AgentService
        verify(request,csrf_token)
        actor = demo_owner(request)
        refresh_before_request(request)
        try:
            run = AgentService().create(actor.subject_id,text,mode)
        except ValueError:
            return _form_only(request,text,error="Choose a valid mode and explicit study requirements.")
        return RedirectResponse(f"/session?agent_request_id={run['id']}",status_code=303)
    courses = _profile_courses() or store.courses()
    try:
        refresh_before_request(request)
        parsed = _parsed(interpret_request(text, courses), courses)
    except (OmnaraError, ValueError) as exc:
        return _form_only(request, text, error=str(exc))
    return _redirect_session(
        parsed["course_id"],
        parsed["starts_at"],
        parsed["ends_at"],
        parsed["place_type"],
        parsed["note"],
        "0",
        sentence=text,
    )


def _redirect_session(
    course_id: str,
    starts_at: str,
    ends_at: str,
    place_type: str,
    note: str,
    study_anyway: str,
    notice: str = "",
    sentence: str = "",
) -> RedirectResponse:
    query = _query(course_id, starts_at, ends_at, place_type, note, study_anyway == "1", sentence)
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
    sentence: str = Form(""),
):
    store.set_invite_text(invite)
    return _redirect_session(
        course_id, starts_at, ends_at, place_type, note, study_anyway, "Draft saved.", sentence=sentence
    )


@router.post("/session/swap-person")
def swap_person(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
    sentence: str = Form(""),
):
    store.set_invite_text(invite)
    store.cycle_person()
    return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway, sentence=sentence)


@router.post("/session/swap-place")
def swap_place(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    place_type: str = Form(...),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
    sentence: str = Form(""),
):
    store.set_invite_text(invite)
    store.cycle_place()
    return _redirect_session(course_id, starts_at, ends_at, place_type, note, study_anyway, sentence=sentence)


@router.post("/session/confirm")
def confirm_invite(
    request: Request,
    course_id: str = Form(""),
    starts_at: str = Form(""),
    ends_at: str = Form(""),
    place_type: str = Form(""),
    note: str = Form(""),
    study_anyway: str = Form("0"),
    invite: str = Form(""),
    sentence: str = Form(""),
    agent_request_id: str = Form(""),
    proposal_revision: int = Form(-1),
    evidence_id: str = Form(""),
    csrf_token: str = Form(""),
):
    if agent_request_id:
        from app.routers.agent import verify
        from app.scheduling.demo import demo_owner
        from app.study_agent.service import AgentService
        from fastapi import HTTPException
        actor = demo_owner(request)
        verify(request,csrf_token)
        refresh_before_request(request)
        def synthetic_executor(p):
            store.create_invitations([{"person_id":person["id"],"first_name":person["first_name"],
                "course_id":store.course_by_id(AgentService().repository.get(agent_request_id,actor.subject_id)["goal"]["course_id"])["id"],
                "course_code":p["course"],"course_name":p["course"],"starts_at":p["starts_at"],"ends_at":p["ends_at"],
                "place_name":p["place"]["name"],"message":p["invite"]} for person in p["people"]])
        try:
            AgentService().before_send(agent_request_id,actor.subject_id,proposal_revision,evidence_id,synthetic_executor)
        except KeyError:
            raise HTTPException(404,"REQUEST_NOT_FOUND") from None
        except ValueError:
            raise HTTPException(409,"EXACT_PROPOSAL_APPROVAL_REQUIRED") from None
        return RedirectResponse(f"/session?agent_request_id={agent_request_id}",status_code=303)
    course = _course_record(course_id)
    try:
        refresh_before_request(request)
        conflict = schedule_core.own_conflict(starts_at, ends_at, study_anyway == "1")
    except ValueError:
        conflict = {"title": "That time"}
    if conflict:
        return _redirect_session(
            course["id"],
            starts_at,
            ends_at,
            place_type,
            note,
            study_anyway,
            "Pick a conflict-free time before sending.",
            sentence=sentence,
        )
    store.set_invite_text(invite)
    try:
        proposal = matching_core.build_proposal(
            course["code"], starts_at, ends_at, place_type, note, study_anyway == "1"
        )
    except matching_core.NoMatch as exc:
        return _redirect_session(
            course["id"], starts_at, ends_at, place_type, note, study_anyway, str(exc), sentence=sentence
        )
    message = invite.strip() or proposal.invite
    store.create_invitations(
        [
            {
                "person_id": person.id,
                "first_name": person.first_name,
                "course_id": course["id"],
                "course_code": course["code"],
                "course_name": course["name"],
                "starts_at": proposal.starts_at,
                "ends_at": proposal.ends_at,
                "place_name": proposal.place.name,
                "message": message,
            }
            for person in proposal.people
        ]
    )
    return _redirect_session(
        course_id,
        starts_at,
        ends_at,
        place_type,
        note,
        study_anyway,
        "Invitation sent for this demo. Nothing is emailed until an email provider is connected.",
        sentence=sentence,
    )


@router.post("/session/calendar")
def add_to_calendar(
    course_id: str = Form("cs61a"),
    starts_at: str = Form(DEFAULT_START),
    ends_at: str = Form(DEFAULT_END),
    place_type: str = Form("coffee"),
    note: str = Form(""),
    study_anyway: str = Form("1"),
    sentence: str = Form(""),
):
    notice = "Calendar export is disabled. Authoritative session revision, acceptances and separate organizer approval are required. No calendar was written."
    return _redirect_session(
        course_id, starts_at, ends_at, place_type, note, study_anyway, notice, sentence=sentence
    )


@router.get("/invite")
def invite_index(request: Request):
    return render(
        request,
        "invite.html",
        nav="invite",
        index=True,
        invites=store.latest_invitations(),
    )


@router.get("/invite/{invitation_id}")
def invite_page(request: Request, invitation_id: str):
    invitation = store.invitation_by_id(invitation_id)
    when = ""
    if invitation:
        when = pretty_range(invitation["starts_at"], invitation["ends_at"])
    return render(
        request,
        "invite.html",
        nav="invite",
        index=False,
        invitation_id=invitation_id,
        invitation=invitation,
        when=when,
    )


@router.post("/invite/{invitation_id}/accept")
def accept_invite(invitation_id: str):
    store.set_person_status(invitation_id, "accepted")
    return RedirectResponse(f"/invite/{invitation_id}", status_code=303)


@router.post("/invite/{invitation_id}/decline")
def decline_invite(invitation_id: str):
    store.set_person_status(invitation_id, "declined")
    return RedirectResponse(f"/invite/{invitation_id}", status_code=303)
