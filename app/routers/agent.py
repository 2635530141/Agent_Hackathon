"""Synthetic-owner surface for the bounded planning loop; CSRF and revision gates."""
from secrets import compare_digest, token_urlsafe
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import store
from app.scheduling.demo import demo_owner
from app.scheduling.time import display_zone, local_input
from app.study_agent.service import AgentService
from app.study_agent.state import StaleRun
from app.study_agent.tools import profile_brief
from app.web import render

router = APIRouter(prefix="/agent", tags=["study-agent"])


def csrf(request):
    if not request.session.get("agent_csrf"):
        request.session["agent_csrf"] = token_urlsafe(32)
    return request.session["agent_csrf"]


def verify(request, supplied):
    expected = request.session.get("agent_csrf", "")
    if not expected or not compare_digest(expected.encode(), supplied.encode()):
        raise HTTPException(403, "CSRF_REQUIRED")
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(403, "ORIGIN_MISMATCH")


def load(service, run_id, owner):
    try:
        return service.repository.get(run_id, owner)
    except KeyError:
        raise HTTPException(404, "REQUEST_NOT_FOUND") from None


def page(request, run=None, error=""):
    goal = dict(run["goal"]) if run else {}
    for field in ("window_start", "window_end"):
        if goal.get(field):
            goal[field] = local_input(goal[field])
    return render(request, "agent.html", nav="agent", run=run, goal=goal, courses=store.courses(),
                  preferences=profile_brief(), csrf=csrf(request), action_key=uuid4().hex,
                  timezone=display_zone(), error=error)


@router.get("")
def index(request: Request, actor=Depends(demo_owner)):
    return page(request)


@router.post("")
def create(request: Request, sentence: str = Form(...), mode: str = Form("demo"), csrf_token: str = Form(""), actor=Depends(demo_owner)):
    verify(request, csrf_token)
    try:
        run = AgentService().create(actor.subject_id, sentence, mode)
    except ValueError as exc:
        return page(request, error=str(exc))
    return RedirectResponse(f"/agent/{run['id']}", status_code=303)


@router.get("/{run_id}")
def show(request: Request, run_id: str, actor=Depends(demo_owner)):
    return page(request, load(AgentService(), run_id, actor.subject_id))


@router.get("/{run_id}/status")
def status(run_id: str, actor=Depends(demo_owner)):
    run = load(AgentService(), run_id, actor.subject_id)
    return {"state":run["state"], "version":run["version"],
            "events":[{"kind":e["kind"], "summary":e["summary"]} for e in run["events"][-12:] ]}


@router.post("/{run_id}/{action}")
def act(request: Request, run_id: str, action: str, csrf_token: str = Form(""), version: int = Form(...),
        action_key: str = Form(...), course_id: str = Form(""), window_start: str = Form(""),
        window_end: str = Form(""), duration_minutes: str = Form(""), place_type: str = Form(""),
        actor=Depends(demo_owner)):
    verify(request, csrf_token)
    service = AgentService()
    run = load(service, run_id, actor.subject_id)
    try:
        if action == "cancel":
            service.cancel(run_id, actor.subject_id)
        elif action == "refresh":
            if run["version"] != version or run["state"] == "planning":
                raise StaleRun()
            service.refresh_synthetic()
        else:
            patch = {k:v for k,v in {"course_id":course_id,"window_start":window_start,"window_end":window_end,
                     "duration_minutes":int(duration_minutes) if duration_minutes else None,"place_type":place_type}.items() if v}
            service.act(run_id, actor.subject_id, version, action_key[:128], action, patch)
    except StaleRun:
        return page(request, load(service, run_id, actor.subject_id), "This request changed in another tab. Review the current revision.")
    except ValueError:
        return page(request, load(service, run_id, actor.subject_id), "Check the course, increasing time window, duration and current action.")
    return RedirectResponse(f"/agent/{run_id}", status_code=303)
