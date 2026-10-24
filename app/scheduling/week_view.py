"""Owner-only timetable projection. Matching never consumes labels or this payload."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from .core.schemas import Actor, Horizon
from .core.recurrence import expand
from .time import display_zone, local_input, parse_instant


def week_payload(service, owner_id, selected_day):
    monday = selected_day-timedelta(days=selected_day.weekday())
    zone = ZoneInfo(display_zone())
    def midnight(day):
        return parse_instant(day.isoformat()+"T00:00")
    span = Horizon(start=midnight(monday),end=midnight(monday+timedelta(days=7)))
    view = service.owner_schedule(Actor(subject_id=owner_id),span)
    raw = []
    for rule in view.weekly_rules:
        intervals,_ = expand([rule],span)
        for item in intervals:
            raw.append((item,"weekly",str(rule.weekday),"Study hours",{}))
    for exam in view.exams:
        from app import store
        course = store.course_by_id(exam.course_id)
        raw.append((exam,"exam",exam.id,(course or {}).get("code",exam.course_id)+" exam",
            {"course_id":exam.course_id,"priority_starts_at":local_input(exam.priority_start) if exam.priority_start else ""}))
    for item in view.exceptions:
        raw.append((item,"free" if item.kind=="available" else "busy",item.id,
            "Extra study time" if item.kind=="available" else "Unavailable",{}))
    for item in view.private_calendar_events:
        raw.append((item,"calendar",item.id,item.private_title or "Calendar busy",{}))
    for item in view.confirmed_reservations:
        raw.append((item,"session",item.session_id,"Confirmed study session",{}))
    days,entries = [],[]
    for index in range(7):
        day = monday+timedelta(days=index)
        start,end = midnight(day),midnight(day+timedelta(days=1))
        daily = service.effective_availability(Actor(subject_id=owner_id),owner_id,Horizon(start=start,end=end))
        days.append({"date":day.isoformat(),"weekday":day.strftime("%a"),"number":day.day,
            "verification":daily.calendar.verification_level,"selected":day==selected_day,
            "dst":(end-start)!=timedelta(days=1)})
        items = list(raw)+[(a,"available","","Effective free",{}) for a in daily.available]
        for item,kind,record_id,label,extra in items:
            left,right = max(item.start,start),min(item.end,end)
            if right<=left:
                continue
            local_start,local_end = left.astimezone(zone),right.astimezone(zone)
            minute = local_start.hour*60+local_start.minute
            finish = 1440 if right==end else local_end.hour*60+local_end.minute
            entries.append({"day":index,"id":record_id,"kind":kind,"label":label,
                "starts_at":local_input(item.start),"ends_at":local_input(item.end),
                "time_label":local_start.strftime("%H:%M")+"–"+local_end.strftime("%H:%M"),
                "top":minute/14.4,"height":max(15,finish-minute)/14.4,
                "editable":kind in {"weekly","exam","free","busy"},**extra})
    # Assign overlap lanes only to foreground blocks; background availability stays full width.
    for day in range(7):
        foreground = sorted([e for e in entries if e["day"]==day and e["kind"] in
            {"exam","free","busy","calendar","session"}],key=lambda e:(e["top"],e["id"]))
        groups,group,edge = [],[],0
        for entry in foreground:
            if group and entry["top"]>=edge:
                groups.append(group); group=[]
            group.append(entry); edge=max(edge if len(group)>1 else 0,entry["top"]+entry["height"])
        if group:
            groups.append(group)
        for group in groups:
            lane_ends=[]
            for entry in group:
                lane = next((i for i,end in enumerate(lane_ends) if end<=entry["top"]),len(lane_ends))
                if lane==len(lane_ends): lane_ends.append(0)
                lane_ends[lane]=entry["top"]+entry["height"]; entry["lane"]=lane
            for entry in group:
                entry["left"]=entry["lane"]*100/len(lane_ends)
                entry["width"]=100/len(lane_ends)
    return {"start":monday.isoformat(),"selected":selected_day.isoformat(),"timezone":display_zone(),
        "label":monday.strftime("%b %d")+" – "+(monday+timedelta(days=6)).strftime("%b %d, %Y"),
        "previous":(selected_day-timedelta(days=7)).isoformat(),
        "next":(selected_day+timedelta(days=7)).isoformat(),"days":days,"entries":entries}
