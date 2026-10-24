from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import schedule_core, store
from app.format import pretty_range
from app.web import render

router = APIRouter()


def _page(request: Request, error: str = "", saved: str = "",
          starts_at: str = "2026-10-29T16:00", ends_at: str = "2026-10-29T21:00"):
    available = []
    try:
        available = [{**slot, "when": pretty_range(slot["starts_at"], slot["ends_at"])}
                     for slot in schedule_core.availability(starts_at, ends_at)]
    except ValueError as exc:
        error = error or str(exc)
    blocks = []
    for block in schedule_core.list_busy():
        item = block.model_dump()
        item["when"] = pretty_range(block.starts_at, block.ends_at)
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
        available=available,
        preview_start=starts_at,
        preview_end=ends_at,
    )


@router.get("/schedule")
def schedule_page(request: Request, error: str = "", saved: str = "",
                  starts_at: str = "2026-10-29T16:00", ends_at: str = "2026-10-29T21:00"):
    return _page(request, error, saved, starts_at, ends_at)


@router.post("/schedule/hours")
async def save_hours_form(request: Request):
    form = await request.form()
    hours = {}
    for day in schedule_core.DAYS:
        hours[day] = (str(form.get(f"{day}_start", "")), str(form.get(f"{day}_end", "")))
    try:
        schedule_core.save_weekly(hours)
    except ValueError as exc:
        return RedirectResponse(f"/schedule?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/schedule?saved=hours", status_code=303)


@router.post("/schedule/exams")
def add_exam(
    course_id: str = Form(...),
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    priority: str | None = Form(None),
):
    try:
        schedule_core.create_exam(course_id, starts_at, ends_at, priority == "yes")
    except ValueError as exc:
        return RedirectResponse(f"/schedule?error={quote(str(exc))}", status_code=303)
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
        return RedirectResponse(f"/schedule?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/schedule?saved=slot", status_code=303)


@router.post("/schedule/exceptions/{exception_id}/delete")
def remove_exception(exception_id: str):
    schedule_core.remove_exception(exception_id)
    return RedirectResponse("/schedule?saved=slot", status_code=303)


@router.post("/schedule/calendar")
def connect_calendar():
    try:
        schedule_core.connect_calendar()
    except ValueError as exc:
        return RedirectResponse(f"/schedule?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/schedule", status_code=303)


@router.get("/api/schedule/busy")
def api_owner_busy():
    return schedule_core.list_busy()


@router.get("/api/schedule/busy/match")
def api_match_busy():
    return schedule_core.list_busy_for_match()


@router.get("/api/schedule/availability")
def api_availability(starts_at: str, ends_at: str):
    try:
        return {"synthetic": True, "timezone": schedule_core.ZONE.key,
                "available": schedule_core.availability(starts_at, ends_at)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
