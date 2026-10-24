from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import schedule_core, store
from app.format import pretty_range
from app.web import render
from app.scheduling.demo import demo_owner, FIXTURE_HORIZON
from app.scheduling.runtime import get_runtime
from app.scheduling.time import display_zone, local_input, horizon
from app.scheduling.core.schemas import Actor
from datetime import date, timedelta

router = APIRouter(dependencies=[Depends(demo_owner)])


def _page(request: Request, error: str = "", saved: str = ""):
    selected = request.query_params.get("day", "2026-10-29")
    try:
        day = date.fromisoformat(selected)
    except ValueError:
        day = date(2026, 10, 29)
        error = "Choose a valid date."
    availability = schedule_core.effective_availability(store.OWNER_ID, day.isoformat()+"T00:00", (day+timedelta(days=1)).isoformat()+"T00:00")
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
        courses=store.courses(),
        calendar=schedule_core.calendar_status(),
        error=error,
        saved=saved,
        timezone=display_zone(), availability=availability, selected_day=day.isoformat(),
        overnight={schedule_core.DAYS[r.weekday]:r.ends_next_day for r in get_runtime().repository.list_records(store.OWNER_ID,"weekly-rules")},
        free_rows=[pretty_range(local_input(i.start),local_input(i.end)) for i in availability.available],
    )


@router.get("/schedule")
def schedule_page(request: Request, error: str = "", saved: str = ""):
    return _page(request, error, saved)


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
def refresh_synthetic_calendar():
    # Refresh the known synthetic pool together so a fresh owner snapshot does
    # not leave candidate fixtures stale. This never contacts a real provider.
    runtime = get_runtime()
    for user_id in [store.OWNER_ID, *(person["id"] for person in store.others())]:
        runtime.service.refresh_calendar(Actor(subject_id=user_id), FIXTURE_HORIZON)
    return RedirectResponse("/schedule?saved=synthetic-refresh", status_code=303)


@router.get("/api/schedule/availability")
def api_availability(starts_at: str, ends_at: str, duration_minutes: int = 1):
    try:
        return schedule_core.effective_availability(store.OWNER_ID, starts_at, ends_at, duration_minutes)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(422, "INVALID_INTERVAL_OR_DURATION") from None


@router.get("/api/schedule/busy")
def api_owner_busy():
    return schedule_core.list_busy()


@router.get("/api/schedule/busy/match")
def api_match_busy():
    return schedule_core.list_busy_for_match()
