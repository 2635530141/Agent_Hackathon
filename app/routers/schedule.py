from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import schedule_core, store
from app.format import pretty_range
from app.web import render
from app.scheduling.demo import FIXTURE_HORIZON
from app.scheduling.live_host import owner_access
from app.scheduling.context import owner_id, live_context
from app.scheduling.runtime import get_runtime
from app.scheduling.time import display_zone, local_input, horizon
from app.scheduling.core.schemas import Actor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

router = APIRouter(dependencies=[Depends(owner_access)])


def _page(request: Request, error: str = "", saved: str = "", conflict_result=None):
    from secrets import token_urlsafe
    from app.scheduling.week_view import week_payload
    if not request.session.get("calendar_editor_csrf"):
        request.session["calendar_editor_csrf"] = token_urlsafe(32)
    default_day = datetime.now(ZoneInfo(display_zone())).date().isoformat() if live_context() else "2026-10-29"
    selected = request.query_params.get("day", default_day)
    try:
        day = date.fromisoformat(selected)
    except ValueError:
        day = date(2026, 10, 29)
        error = "Choose a valid date."
    availability = schedule_core.effective_availability(owner_id(), day.isoformat()+"T00:00", (day+timedelta(days=1)).isoformat()+"T00:00")
    blocks = []
    for block in schedule_core.list_busy():
        item = block.model_dump()
        item["when"] = pretty_range(block.starts_at, block.ends_at)
        item["start_local"], item["end_local"] = local_input(block.starts_at), local_input(block.ends_at)
        blocks.append(item)
    match_blocks = []
    for block in schedule_core.list_busy_for_match():
        item = block.model_dump()
        item["when"] = pretty_range(block.starts_at, block.ends_at)
        match_blocks.append(item)
    exam_rows = []
    for exam in schedule_core.list_exams():
        course = store.course_by_id(exam.course_id)
        exam_rows.append(
            {
                **exam.model_dump(),
                "course_code": course["code"] if course else exam.course_id,
                "when": pretty_range(exam.starts_at, exam.ends_at),
                "priority_when": pretty_range(exam.priority_starts_at, exam.starts_at)
                if exam.priority_starts_at
                else "",
                "start_local":local_input(exam.starts_at), "end_local":local_input(exam.ends_at),
                "priority_local":local_input(exam.priority_starts_at) if exam.priority_starts_at else "",
            }
        )
    return render(
        request,
        "schedule.html",
        nav="schedule",
        days=schedule_core.DAYS,
        day_labels=schedule_core.DAY_LABELS,
        weekly=schedule_core.weekly_hours(),
        exams=exam_rows,
        blocks=blocks,
        match_blocks=match_blocks,
        courses=[] if live_context() else store.courses(),
        calendar=schedule_core.calendar_status(),
        error=error,
        saved=saved,
        timezone=display_zone(), availability=availability, selected_day=day.isoformat(),
        overnight={schedule_core.DAYS[r.weekday]:r.ends_next_day for r in get_runtime().repository.list_records(owner_id(),"weekly-rules")},
        free_rows=[pretty_range(local_input(i.start),local_input(i.end)) for i in availability.available],
        calendar_week=week_payload(get_runtime().service,owner_id(),day),
        editor_csrf=request.session["calendar_editor_csrf"],
        today=datetime.now(ZoneInfo(display_zone())).date().isoformat(),
        conflict_result=conflict_result,
    )


@router.get("/schedule")
def schedule_page(request: Request, error: str = "", saved: str = ""):
    return _page(request, error, saved)


@router.get("/schedule/google-setup")
def google_setup(request: Request):
    return render(request,"calendar_setup.html",nav="schedule")


@router.post("/schedule/hours")
async def save_hours_form(request: Request):
    form = await request.form()
    hours = {}
    for day in schedule_core.DAYS:
        hours[day] = (str(form.get(f"{day}_start", "")), str(form.get(f"{day}_end", "")))
    try:
        overnight = {day: form.get(f"{day}_overnight") == "yes" for day in schedule_core.DAYS}
        schedule_core.save_weekly(hours, timezone_name=str(form.get("timezone", display_zone())), overnight=overnight)
    except ValueError as exc:
        return RedirectResponse("/schedule?error=Invalid+weekly+hours+or+timezone", status_code=303)
    return RedirectResponse("/schedule?saved=hours", status_code=303)


@router.post("/schedule/exams")
def add_exam(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    priority: str | None = Form(None),
    priority_starts_at: str = Form(""),
):
    try:
        schedule_core.create_exam(course_id, starts_at, ends_at, priority == "yes", priority_starts_at or None)
    except ValueError as exc:
        return RedirectResponse("/schedule?error=Invalid+exam+or+priority+time", status_code=303)
    return RedirectResponse("/schedule?saved=exam", status_code=303)


@router.post("/schedule/exams/{exam_id}/delete")
def remove_exam(exam_id: str):
    schedule_core.remove_exam(exam_id)
    return RedirectResponse("/schedule?saved=exam", status_code=303)


@router.post("/schedule/exceptions")
def add_exception(
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    kind: str = Form(...),
):
    try:
        schedule_core.create_exception(starts_at, ends_at, kind)
    except ValueError as exc:
        return RedirectResponse("/schedule?error=Invalid+slot+or+DST+time", status_code=303)
    return RedirectResponse("/schedule?saved=slot", status_code=303)


@router.post("/schedule/exceptions/{exception_id}/delete")
def remove_exception(exception_id: str):
    schedule_core.remove_exception(exception_id)
    return RedirectResponse("/schedule?saved=slot", status_code=303)


@router.post("/schedule/calendar")
def connect_calendar(request: Request):
    # The existing entry point reaches M2 only after the host has independently
    # supplied verified auth and an isolated live composition. Default demo stays off.
    from app.scheduling.google_config import GoogleActor
    from app.scheduling.google_integration import GoogleCalendarIntegration
    from secrets import compare_digest
    actor = getattr(request.state,"calendar_actor",None)
    active = getattr(request.app.state,"google_calendar_integration",None)
    if isinstance(actor,GoogleActor) and isinstance(active,GoogleCalendarIntegration):
        if live_context():
            return RedirectResponse("/calendar/google/connect",status_code=303)
        csrf = request.headers.get("x-csrf-token","")
        if not compare_digest(csrf.encode(),actor.csrf_token.encode()):
            from fastapi import HTTPException
            raise HTTPException(403,"CSRF_REQUIRED")
        return RedirectResponse("/calendar/google/connect",status_code=303)
    return RedirectResponse("/schedule?error=Google+not+configured%3B+fixed+demo+identity+cannot+connect", status_code=303)


@router.post("/schedule/exams/{exam_id}/edit")
def edit_exam(exam_id: str, course_id: str = Form(...), starts_at: str = Form(...),
              ends_at: str = Form(...), priority_starts_at: str = Form("")):
    try:
        schedule_core.update_exam(exam_id, course_id, starts_at, ends_at, priority_starts_at or None)
    except ValueError:
        return RedirectResponse("/schedule?error=Invalid+exam+edit", status_code=303)
    return RedirectResponse("/schedule?saved=exam", status_code=303)


@router.post("/schedule/exceptions/{exception_id}/edit")
def edit_exception(exception_id: str, starts_at: str = Form(...), ends_at: str = Form(...), kind: str = Form(...)):
    try:
        schedule_core.update_exception(exception_id, starts_at, ends_at, kind)
    except ValueError:
        return RedirectResponse("/schedule?error=Invalid+slot+edit", status_code=303)
    return RedirectResponse("/schedule?saved=slot", status_code=303)


@router.post("/schedule/calendar/refresh")
def refresh_synthetic_calendar(day: str = Form("")):
    # Refresh the known synthetic pool together so a fresh owner snapshot does
    # not leave candidate fixtures stale. This never contacts a real provider.
    runtime = get_runtime()
    context = live_context()
    if context:
        from app.scheduling.google_storage import GoogleStateError
        try:
            context.host.active.refresh(context.actor)
        except GoogleStateError:
            return RedirectResponse("/schedule?error=Reconnect+Google+Calendar+and+retry",303)
    else:
        for user_id in [owner_id(), *(person["id"] for person in store.others())]:
            runtime.service.refresh_calendar(Actor(subject_id=user_id), FIXTURE_HORIZON)
    try:
        selected = date.fromisoformat(day).isoformat()
    except ValueError:
        selected = ""
    query = f"day={selected}&" if selected else ""
    saved = "google-refresh" if context else "synthetic-refresh"
    return RedirectResponse(f"/schedule?{query}saved={saved}", status_code=303)


@router.post("/schedule/calendar/disconnect")
def disconnect_calendar():
    context = live_context()
    if not context:
        from fastapi import HTTPException
        raise HTTPException(409,"GOOGLE_NOT_CONFIGURED")
    context.host.active.disconnect(context.actor)
    return RedirectResponse("/schedule?saved=disconnected",303)


@router.post("/schedule/timezone")
def save_timezone(timezone: str = Form(...)):
    context = live_context()
    if not context:
        from fastapi import HTTPException
        raise HTTPException(409,"PRIVATE_ACCOUNT_REQUIRED")
    try:
        context.host.accounts.set_timezone(owner_id(),timezone)
    except (ValueError,KeyError):
        return RedirectResponse("/schedule?error=Choose+a+valid+IANA+timezone",303)
    return RedirectResponse("/schedule?saved=timezone",303)


@router.post("/schedule/check")
def check_own_time(request: Request, starts_at: str = Form(...), ends_at: str = Form(...)):
    try:
        result = schedule_core.own_conflict(starts_at,ends_at,False)
    except ValueError:
        return _page(request,error="Choose a valid interval and resolve any DST ambiguity.")
    return _page(request,conflict_result=result or {"message":"No blocking conflict in the checked calendar/manual schedule."})


@router.get("/api/schedule/availability")
def api_availability(starts_at: str, ends_at: str, duration_minutes: int = 1):
    try:
        return schedule_core.effective_availability(owner_id(), starts_at, ends_at, duration_minutes)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(422, "INVALID_INTERVAL_OR_DURATION") from None


@router.get("/api/schedule/busy")
def api_owner_busy():
    return schedule_core.list_busy()


@router.get("/api/schedule/busy/match")
def api_match_busy():
    return schedule_core.list_busy_for_match()
