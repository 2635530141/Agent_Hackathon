"""Timetable editing uses the same facade/repository as the existing HTML forms."""
from datetime import date, timedelta
from secrets import compare_digest
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from typing import Literal
from app import schedule_core, store
from app.scheduling.demo import demo_owner
from app.scheduling.runtime import get_runtime
from app.scheduling.time import parse_instant, display_zone
from app.scheduling.week_view import week_payload

router = APIRouter(prefix="/api/schedule",dependencies=[Depends(demo_owner)])
form_router = APIRouter(dependencies=[Depends(demo_owner)])


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["weekly","exam","free","busy"]
    starts_at: str
    ends_at: str
    id: str | None = None
    course_id: str = ""
    priority_starts_at: str = ""


def editor_csrf(request, form_token=None):
    expected = request.session.get("calendar_editor_csrf","")
    origin = request.headers.get("origin")
    if (not expected or not compare_digest(expected.encode(),(form_token or request.headers.get("x-csrf-token","")).encode())
        or (origin and origin!=f"{request.url.scheme}://{request.url.netloc}")):
        raise HTTPException(403,"CSRF_REQUIRED")


@router.get("/week")
def week(day: date):
    try:
        return week_payload(get_runtime().service,store.OWNER_ID,day)
    except ValueError:
        raise HTTPException(422,"INVALID_WEEK") from None


@router.post("/entry")
def save_entry(body: Entry, request: Request):
    editor_csrf(request)
    return apply_entry(body)


def apply_entry(body):
    try:
        start,end = parse_instant(body.starts_at),parse_instant(body.ends_at)
        if end<=start:
            raise ValueError("invalid interval")
        if body.kind=="weekly":
            from zoneinfo import ZoneInfo
            a,b = start.astimezone(ZoneInfo(display_zone())),end.astimezone(ZoneInfo(display_zone()))
            if b.date()-a.date() not in {timedelta(0),timedelta(days=1)} or end-start>timedelta(hours=25):
                raise ValueError("weekly interval too long")
            day = schedule_core.DAYS[a.weekday()]
            if body.id is not None and body.id != str(a.weekday()):
                raise ValueError("weekly edit must keep its weekday")
            hours = schedule_core.weekly_hours()
            hours[day] = (a.strftime("%H:%M"),b.strftime("%H:%M"))
            rules = get_runtime().repository.list_records(store.OWNER_ID,"weekly-rules")
            overnight = {schedule_core.DAYS[r.weekday]:r.ends_next_day for r in rules}
            overnight[day] = b.date()!=a.date()
            schedule_core.save_weekly(hours,overnight=overnight)
        elif body.kind=="exam":
            if body.id:
                schedule_core.update_exam(body.id,body.course_id,body.starts_at,body.ends_at,body.priority_starts_at or None)
            else:
                schedule_core.create_exam(body.course_id,body.starts_at,body.ends_at,False,body.priority_starts_at or None)
        elif body.id:
            schedule_core.update_exception(body.id,body.starts_at,body.ends_at,body.kind)
        else:
            schedule_core.create_exception(body.starts_at,body.ends_at,body.kind)
    except ValueError:
        raise HTTPException(422,"Invalid time, course or record. Check the timezone, DST and end time.") from None
    return {"saved":True}


@router.delete("/entry/{kind}/{record_id}")
def delete_entry(kind: Literal["weekly","exam","free","busy"], record_id: str, request: Request):
    editor_csrf(request)
    return remove_entry(kind,record_id)


def remove_entry(kind, record_id):
    if kind=="weekly":
        if record_id not in {str(n) for n in range(7)}:
            raise HTTPException(422,"INVALID_WEEKDAY")
        hours = schedule_core.weekly_hours()
        hours[schedule_core.DAYS[int(record_id)]] = ("","")
        rules = get_runtime().repository.list_records(store.OWNER_ID,"weekly-rules")
        schedule_core.save_weekly(hours,overnight={schedule_core.DAYS[r.weekday]:r.ends_next_day for r in rules})
    else:
        if kind=="exam": schedule_core.remove_exam(record_id)
        else: schedule_core.remove_exception(record_id)
    return {"removed":True}


@form_router.post("/schedule/timetable")
async def timetable_form(request: Request):
    data = await request.form()
    editor_csrf(request,str(data.get("csrf_token","")))
    try:
        body = Entry(kind=str(data.get("entry_kind","free")),id=str(data.get("record_id","")) or None,
            starts_at=str(data.get("starts_at","")),ends_at=str(data.get("ends_at","")),
            course_id=str(data.get("course_id","")),priority_starts_at=str(data.get("priority_starts_at","")))
        if data.get("operation")=="delete" and body.id:
            remove_entry(body.kind,body.id)
        else:
            apply_entry(body)
        day = date.fromisoformat(body.starts_at[:10]).isoformat()
    except (ValueError,HTTPException):
        return RedirectResponse("/schedule?error=Invalid+time,+course+or+DST.+Please+check+your+entry.",303)
    return RedirectResponse("/schedule?day="+day+"&saved=time",303)
