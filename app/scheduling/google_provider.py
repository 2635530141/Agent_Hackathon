"""Primary-only bounded Google reads; stages every page before any DB replacement."""
import hashlib
from datetime import datetime, time, timedelta, timezone
from time import sleep
from zoneinfo import ZoneInfo
import httpx
from .core.intervals import overlaps
from .core.recurrence import local_candidates
from .core.schemas import BusyBlock, Horizon, PrivateEvent, ProviderSnapshot
from .google_storage import GoogleStateError

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
FIELDS = "timeZone,nextPageToken,items(id,summary,status,transparency,eventType,start,end,attendees(self,responseStatus))"


def google_window(now, zone_name):
    zone = ZoneInfo(zone_name)
    day = now.astimezone(zone).date()
    start, end = datetime.combine(day,time.min), datetime.combine(day+timedelta(days=14),time.min)
    boundaries = [local_candidates(wall,zone) for wall in (start,end)]
    if any(len(c)!=1 for c in boundaries):
        raise GoogleStateError("COVERAGE_BOUNDARY_REQUIRES_RESOLUTION")
    return Horizon(start=boundaries[0][0],end=boundaries[1][0])


def _event_time(value, calendar_zone):
    if "dateTime" in value:
        parsed = datetime.fromisoformat(value["dateTime"])
    elif "date" in value:
        parsed = datetime.combine(datetime.fromisoformat(value["date"]).date(),time.min)
    else:
        raise ValueError("missing event time")
    if parsed.tzinfo is None:
        candidates = local_candidates(parsed,ZoneInfo(value.get("timeZone") or calendar_zone))
        if len(candidates)!=1:
            raise ValueError("ambiguous provider local time")
        return candidates[0]
    return parsed.astimezone(timezone.utc)


def normalize_event(owner_id, event, calendar_zone):
    if (event.get("status")=="cancelled" or event.get("transparency")=="transparent"
        or event.get("eventType")=="workingLocation"
        or any(a.get("self") is True and a.get("responseStatus")=="declined" for a in event.get("attendees",[]))):
        return None
    opaque = hashlib.sha256((owner_id+"\0"+event["id"]).encode()).hexdigest()
    return PrivateEvent(id=opaque,start=_event_time(event["start"],calendar_zone),
        end=_event_time(event["end"],calendar_zone),private_title=str(event.get("summary", ""))[:500])


class GoogleCalendarProvider:
    def __init__(self, token_source, timezone_for_owner, clock=None, transport=None,
                 generation_for_owner=None, pause=sleep):
        self.token_source, self.timezone_for_owner = token_source, timezone_for_owner
        self.clock = clock or (lambda:datetime.now(timezone.utc))
        self.transport, self.pause = transport, pause
        self.generation_for_owner = generation_for_owner

    def read_snapshot(self, owner_id, horizon):
        allowed = google_window(self.clock(),self.timezone_for_owner(owner_id))
        if horizon.start < allowed.start or horizon.end > allowed.end:
            raise GoogleStateError("OUTSIDE_GOOGLE_14_LOCAL_DAY_COVERAGE")
        generation = self.generation_for_owner(owner_id) if self.generation_for_owner else None
        token = self.token_source(owner_id)
        params = {"singleEvents":"true","timeMin":horizon.start.isoformat(),"timeMax":horizon.end.isoformat(),
                  "maxResults":2500,"fields":FIELDS}
        staged, seen_pages, page_token, calendar_zone = {}, set(), None, None
        try:
            with httpx.Client(transport=self.transport,timeout=10) as client:
                for _ in range(100):
                    if page_token:
                        params["pageToken"] = page_token
                    response = None
                    for attempt in range(3):
                        try:
                            response = client.get(EVENTS_URL,params=params,headers={"Authorization":"Bearer "+token})
                        except httpx.TransportError:
                            if attempt==2:
                                raise GoogleStateError("CALENDAR_READ_FAILED") from None
                            self.pause(0.05*(2**attempt))
                            continue
                        if response.status_code==401 and attempt==0:
                            token = self.token_source(owner_id,force=True)
                            continue
                        if response.status_code==429 or response.status_code>=500:
                            if attempt==2:
                                raise GoogleStateError("CALENDAR_READ_FAILED")
                            self.pause(0.05*(2**attempt))
                            continue
                        break
                    if response is None or response.status_code!=200:
                        raise GoogleStateError("CALENDAR_PERMISSION_OR_READ_FAILED")
                    body = response.json()
                    if calendar_zone is None:
                        calendar_zone = body["timeZone"]
                        ZoneInfo(calendar_zone)
                    elif body.get("timeZone",calendar_zone)!=calendar_zone:
                        raise GoogleStateError("CALENDAR_TIMEZONE_CHANGED_DURING_READ")
                    for event in body.get("items",[]):
                        normalized = normalize_event(owner_id,event,calendar_zone)
                        if normalized and overlaps(normalized,horizon):
                            staged[normalized.id] = normalized
                    page_token = body.get("nextPageToken")
                    if not page_token:
                        break
                    if page_token in seen_pages:
                        raise GoogleStateError("INVALID_PAGINATION")
                    seen_pages.add(page_token)
                else:
                    raise GoogleStateError("CALENDAR_PAGE_LIMIT_EXCEEDED")
            if self.generation_for_owner and self.generation_for_owner(owner_id)!=generation:
                raise GoogleStateError("CONNECTION_CHANGED")
            events = sorted(staged.values(),key=lambda e:(e.start,e.end,e.id))
            return ProviderSnapshot(provider_kind="google",coverage=horizon,
                busy=[BusyBlock(id=e.id,start=e.start,end=e.end) for e in events],private_events=events)
        except GoogleStateError:
            raise
        except Exception:
            raise GoogleStateError("CALENDAR_RESPONSE_INVALID") from None
