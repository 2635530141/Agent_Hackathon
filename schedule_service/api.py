"""Schedule router and organizer-only calendar executor, independent of the agent loop."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import logging
import time as monotonic_time
import secrets
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import update

from .domain import Interval
from .engine import compute_availability, intersect_availability, next_available, owner_conflicts, priority_windows
from .google_calendar import CalendarError, CalendarWriteUncertain, GoogleCalendarConnector, GoogleOAuthClient, calendar_event_id
from .oauth import CredentialVault
from .serialization import interval, interval_json, snapshot
from .storage import ConfirmedSession, ConflictError, Store

UTC = timezone.utc


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Window(Input):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def valid_interval(self):
        Interval(self.start, self.end)
        return self

    def interval(self) -> Interval:
        return Interval(self.start, self.end)


class Weekly(Input):
    weekday: int = Field(ge=0, le=6)
    start: time
    end: time

    @model_validator(mode="after")
    def local_times(self):
        if self.start.tzinfo or self.end.tzinfo or self.start == self.end:
            raise ValueError("Weekly times must be distinct local wall times without offsets")
        return self


class ExamInput(Window):
    id: str = Field(min_length=1, max_length=128)
    course_id: str = Field(min_length=1, max_length=80)
    title: str = Field(default="", max_length=200)
    priority_days: int = Field(default=7, ge=0, le=30)


class ScheduleInput(Input):
    expected_version: int = Field(ge=0)
    timezone: str = "America/Los_Angeles"
    weekly: list[Weekly] = Field(default_factory=list, max_length=100)
    additions: list[Window] = Field(default_factory=list, max_length=200)
    removals: list[Window] = Field(default_factory=list, max_length=200)
    exams: list[ExamInput] = Field(default_factory=list, max_length=100)


class AvailabilityInput(Window):
    duration_minutes: int = Field(default=60, ge=1, le=480)


class MatchingInput(AvailabilityInput):
    participant_ids: list[str] = Field(min_length=2, max_length=4)
    organizer_id: str | None = None
    request_id: str | None = None
    owner_override_token: str | None = None

    @model_validator(mode="after")
    def override_identity(self):
        if self.owner_override_token and (not self.organizer_id or not self.request_id
                                         or self.organizer_id not in self.participant_ids):
            raise ValueError("Owner override requires the organizing participant and request id")
        return self


class OwnerCheck(AvailabilityInput):
    request_id: str = Field(min_length=1, max_length=128)
    study_anyway: bool = False


class SessionProjection(Window):
    id: str = Field(min_length=1, max_length=128)
    organizer_id: str = Field(min_length=1, max_length=128)
    participant_ids: list[str] = Field(min_length=2, max_length=4)
    accepted_ids: list[str] = Field(min_length=2, max_length=4)
    subject: str = Field(min_length=1, max_length=120)
    place: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    owner_override_token: str | None = None

    @model_validator(mode="after")
    def accepted(self):
        ids = set(self.participant_ids)
        if len(ids) != len(self.participant_ids) or self.organizer_id not in ids:
            raise ValueError("Session participants must be unique and include the organizer")
        if len(set(self.accepted_ids)) != len(self.accepted_ids) or set(self.accepted_ids) != ids:
            raise ValueError("Calendar export requires acceptance from every participant")
        return self


class Confirmation(Input):
    confirmed: Literal[True]
    confirmation_token: str


class ConnectInput(Input):
    enable_calendar_write: bool = False


def authenticated_user(request: Request, authorization: str | None = Header(default=None)) -> str:
    """Replace this dependency with the team's real login. Never trust a body user_id."""
    token = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
    for known, user_id in request.app.state.auth_tokens.items():
        if token and secrets.compare_digest(token, known):
            return user_id
    raise HTTPException(401, "Authentication required")


def matching_service(request: Request, x_schedule_service_token: str | None = Header(default=None)):
    expected = request.app.state.service_token
    if not expected or not x_schedule_service_token or not secrets.compare_digest(expected, x_schedule_service_token):
        raise HTTPException(403, "Trusted session/matching service required")


def create_app(database_url: str | None = None, *, auth_tokens: dict[str, str] | None = None,
               service_token: str | None = None, calendar_connector=None, oauth_client=None,
               encryption_key: str | None = None, signing_key: str | None = None, clock=None) -> FastAPI:
    app = FastAPI(title="StudyMate Schedule and Calendar", version="0.1.0",
                  description="shulun's PRD module. Calendar credentials and owner event titles never enter model tools.")
    app.state.store = store = Store(database_url or os.getenv("SCHEDULE_DATABASE_URL", "sqlite:///./schedule-demo.db"))
    app.state.auth_tokens = auth_tokens if auth_tokens is not None else json.loads(os.getenv("SCHEDULE_AUTH_TOKENS", "{}"))
    app.state.service_token = service_token or os.getenv("SCHEDULE_SERVICE_TOKEN")
    app.state.clock = clock or (lambda: datetime.now(UTC))
    app.state.signing_key = (signing_key or os.getenv("SCHEDULE_SIGNING_KEY") or secrets.token_hex(32)).encode()
    app.state.writes_enabled = True
    app.state.http = httpx.Client(timeout=8.0, follow_redirects=False)
    if oauth_client is None and os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
        oauth_client = GoogleOAuthClient(app.state.http, os.environ["GOOGLE_CLIENT_ID"],
                                        os.environ["GOOGLE_CLIENT_SECRET"], os.environ["GOOGLE_REDIRECT_URI"])
    key = encryption_key or os.getenv("CALENDAR_TOKEN_ENCRYPTION_KEY")
    app.state.vault = CredentialVault(store, key, oauth_client, app.state.clock) if key and oauth_client else None
    app.state.calendar = calendar_connector or (GoogleCalendarConnector(app.state.http, app.state.vault.token)
                                              if app.state.vault else None)

    @app.middleware("http")
    async def trace_request(request: Request, call_next):
        trace_id, started = secrets.token_hex(8), monotonic_time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = trace_id
        route = request.scope.get("route")
        logging.getLogger("studymate.schedule").info(
            "request=%s method=%s route=%s status=%s duration_ms=%.1f",
            trace_id, request.method, getattr(route, "path", "unmatched"), response.status_code,
            (monotonic_time.monotonic() - started) * 1000,
        )
        return response

    def now():
        return app.state.clock().astimezone(UTC)

    def require_writes():
        if not app.state.writes_enabled or os.getenv("SCHEDULE_WRITES_ENABLED", "true").lower() != "true":
            raise HTTPException(503, "Schedule writes are disabled")

    def load(user_id):
        try:
            payload, version = store.schedule(user_id)
            return payload, version, snapshot(user_id, payload, version)
        except KeyError:
            raise HTTPException(404, "Save a schedule first") from None

    def check_horizon(window: Interval):
        if window.start < now() or window.end > now() + timedelta(days=14):
            raise HTTPException(422, "Use a future interval inside the next 14 days")

    def fresh(user_id, force=False, required_window=None):
        payload, version, snap = load(user_id)
        if not snap.calendar_connected:
            return snap
        covered = bool(payload.get("calendar_window"))
        if covered and required_window is not None:
            imported = interval(payload["calendar_window"])
            covered = imported.start <= required_window.start and imported.end >= required_window.end
        if (not force and snap.calendar_refreshed_at and snap.calendar_refreshed_at >= now() - timedelta(seconds=60)
                and covered):
            return snap
        require_writes()
        if app.state.calendar is None:
            raise HTTPException(503, "Calendar is connected but its connector is unavailable; old busy blocks are retained")
        window = Interval(now(), now() + timedelta(days=14))
        try:
            blocks = app.state.calendar.fetch_busy(user_id, window)
        except (CalendarError, ValueError):
            raise HTTPException(503, "Calendar refresh failed; existing busy blocks were retained") from None
        payload = dict(payload)
        payload.update(calendar_busy=[dict(id=b.id, title=b.title, **interval_json(b.interval)) for b in blocks],
                       calendar_refreshed_at=now().isoformat(), calendar_window=interval_json(window))
        try:
            version = store.save_schedule(user_id, payload, version)
        except ConflictError:
            raise HTTPException(409, "Schedule changed during calendar refresh; retry") from None
        return snapshot(user_id, payload, version)

    def sign(payload):
        data = {**payload, "expires": int(now().timestamp()) + 600}
        raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        mac = hmac.new(app.state.signing_key, token.encode(), hashlib.sha256).hexdigest()
        return token + "." + mac

    def verify(token, required):
        try:
            part, mac = token.split(".")
            target = hmac.new(app.state.signing_key, part.encode(), hashlib.sha256).hexdigest()
            if not secrets.compare_digest(target, mac):
                raise ValueError()
            data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            if data.pop("expires") < int(now().timestamp()) or data != required:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(409, "Confirmation expired or content changed; review again") from None

    def override_payload(user_id, request_id, value, version, snap):
        busy = sorted((x.id, x.interval.start.astimezone(UTC).isoformat(),
                       x.interval.end.astimezone(UTC).isoformat()) for x in snap.calendar_busy
                      if x.interval.start < value.end and x.interval.end > value.start)
        fingerprint = hashlib.sha256(json.dumps(busy, separators=(",", ":")).encode()).hexdigest()
        return dict(kind="owner_calendar_override", owner_id=user_id, request_id=request_id,
                    interval=interval_json(value), schedule_version=version, calendar_conflicts_hash=fingerprint)

    def full_interval_available(snap, value):
        return any(x.start <= value.start and x.end >= value.end for x in compute_availability(snap, value))

    def session_for_owner(session_id, user_id):
        with store.transaction() as db:
            row = db.get(ConfirmedSession, session_id)
            if row is None or row.payload["organizer_id"] != user_id:
                raise HTTPException(404, "Session not found")
            return dict(row.payload)

    def session_valid(data):
        value = interval(data)
        check_horizon(value)
        for participant in data["participant_ids"]:
            snap = fresh(participant, force=True)
            # The already-created private copy must not obstruct its own receipt reconciliation.
            # The connector still validates that event's exact content before reporting success.
            if participant == data["organizer_id"]:
                event_id = calendar_event_id(participant, data["id"])
                snap = replace(snap, calendar_busy=tuple(x for x in snap.calendar_busy if x.id != event_id))
            if participant == data["organizer_id"] and data.get("owner_override_token"):
                # An explicit per-request override removes only this owner's calendar blocks.
                verify(data["owner_override_token"], override_payload(participant, data["request_id"], value,
                       data["override_schedule_version"], snap))
                old_payload, old_version, _ = load(participant)
                # Calendar refresh changes the storage revision; edits to user hours still invalidate the grant.
                if old_payload.get("user_version", old_version) != data["override_user_version"]:
                    raise HTTPException(409, "Owner hours changed; review the override again")
                snap = replace(snap, calendar_busy=())
            if not full_interval_available(snap, value):
                raise HTTPException(409, "A participant's current availability conflicts with the session")

    def preview_payload(data):
        safe = {k: data[k] for k in ("id", "organizer_id", "participant_ids", "accepted_ids", "subject", "place", "start", "end", "version")}
        return dict(kind="organizer_calendar_export", payload=safe, calendar_id="primary", attendees=[])

    @app.exception_handler(ValueError)
    async def invalid_input(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={"detail": "Invalid time, timezone or schedule data; use explicit offsets for ambiguous times"})

    @app.get("/health")
    def health():
        return {"service": "schedule", "storage": "postgres" if store.engine.dialect.name == "postgresql" else "sqlite_demo",
                "google_configured": app.state.calendar is not None, "writes_enabled": app.state.writes_enabled}

    @app.get("/schedule/me")
    def get_me(user_id=Depends(authenticated_user)):
        payload, version, _ = load(user_id)
        return {**payload, "owner_id": user_id, "version": version}

    @app.put("/schedule/me")
    def put_me(body: ScheduleInput, user_id=Depends(authenticated_user)):
        require_writes()
        try:
            ZoneInfo(body.timezone)
        except ZoneInfoNotFoundError:
            raise HTTPException(422, "Choose a valid IANA timezone") from None
        value = body.model_dump(mode="json", exclude={"expected_version"})
        try:
            old, previous = store.schedule(user_id)
            value.update({k: old[k] for k in ("calendar_busy", "calendar_connected", "calendar_refreshed_at", "calendar_window") if k in old})
            value["user_version"] = old.get("user_version", previous) + 1
        except KeyError:
            value["user_version"] = 1
        # Construct the domain object before any write so invalid timezone/time never persists.
        snapshot(user_id, value, 1)
        try:
            version = store.save_schedule(user_id, value, body.expected_version)
        except ConflictError:
            raise HTTPException(409, "Schedule changed; reload before saving") from None
        return {"owner_id": user_id, "version": version, "saved": True}

    @app.post("/schedule/me/availability")
    def available(body: AvailabilityInput, user_id=Depends(authenticated_user)):
        value = body.interval()
        check_horizon(value)
        snap = fresh(user_id, required_window=value)
        slots = [x for x in compute_availability(snap, value)
                 if (x.end - x.start).total_seconds() >= body.duration_minutes * 60]
        return {"owner_id": user_id, "slots": [interval_json(x) for x in slots],
                "priority_windows": jsonable_encoder(priority_windows(snap, value)), "version": snap.version,
                "calendar_status": "refreshed" if snap.calendar_connected else "not_connected"}

    @app.post("/schedule/me/check")
    def owner_check(body: OwnerCheck, user_id=Depends(authenticated_user)):
        value = body.interval()
        check_horizon(value)
        snap = fresh(user_id, force=True)
        conflicts = owner_conflicts(snap, value)
        # Exam/manual exclusions remain hard. Only private calendar conflicts can be overridden.
        can_override = bool(conflicts) and all(x.source in ("calendar", "google_calendar") for x in conflicts)
        granted = body.study_anyway and can_override
        considered = replace(snap, calendar_busy=()) if granted else snap
        allowed = full_interval_available(considered, value)
        token = sign(override_payload(user_id, body.request_id, value, snap.version, snap)) if granted and allowed else None
        alternative = next_available([snap], Interval(value.start, now() + timedelta(days=14)), body.duration_minutes)
        return {"allowed": allowed, "conflicts": [dict(id=x.id, source=x.source, title=x.title,
                    **interval_json(x.interval)) for x in conflicts], "can_override_own_calendar": can_override,
                "override_token": token, "alternative": interval_json(alternative) if alternative else None,
                "scope": "this request only", "schedule_version": snap.version}

    @app.post("/internal/availability", dependencies=[Depends(matching_service)])
    def matching(body: MatchingInput):
        value = body.interval()
        check_horizon(value)
        if len(set(body.participant_ids)) != len(body.participant_ids):
            raise HTTPException(422, "Participants must be distinct")
        snaps = [fresh(uid, required_window=value) for uid in body.participant_ids]
        if body.owner_override_token:
            owner = next(s for s in snaps if s.owner_id == body.organizer_id)
            verify(body.owner_override_token, override_payload(owner.owner_id, body.request_id, value,
                   owner.version, owner))
            snaps = [replace(s, calendar_busy=()) if s.owner_id == body.organizer_id else s for s in snaps]
        slots = intersect_availability(snaps, value, body.duration_minutes)
        # Deliberately no titles, busy sources, emails, or override option in this projection.
        return {"participant_ids": body.participant_ids, "slots": [interval_json(x) for x in slots],
                "versions": {s.owner_id: s.version for s in snaps},
                "organizer_override_applied": body.owner_override_token is not None,
                "calendar_status": {s.owner_id: "refreshed" if s.calendar_connected else "not_connected" for s in snaps}}

    @app.put("/internal/confirmed-sessions/{session_id}", dependencies=[Depends(matching_service)])
    def project_session(session_id: str, body: SessionProjection):
        require_writes()
        if body.id != session_id:
            raise HTTPException(422, "Session id mismatch")
        data = body.model_dump(mode="json")
        if body.owner_override_token:
            payload, revision, _ = load(body.organizer_id)
            verify(body.owner_override_token, override_payload(body.organizer_id, body.request_id, body.interval(), revision,
                   load(body.organizer_id)[2]))
            data.update(override_schedule_version=revision, override_user_version=payload.get("user_version", revision))
        with store.transaction() as db:
            row = db.get(ConfirmedSession, session_id)
            if row is not None and row.payload["version"] >= body.version:
                if all(row.payload.get(k) == v for k, v in data.items()):
                    return {"id": session_id, "version": body.version}
                raise HTTPException(409, "Session version must increase after a change")
            if row is not None:
                changed = db.execute(update(ConfirmedSession).where(
                    ConfirmedSession.id == session_id, ConfirmedSession.version == row.version,
                ).values(payload=data, version=body.version))
                if changed.rowcount != 1:
                    raise HTTPException(409, "Session changed; reload its latest version")
            else:
                db.add(ConfirmedSession(id=session_id, payload=data, version=body.version))
        return {"id": session_id, "version": body.version}

    @app.post("/calendar/google/connect")
    def connect(body: ConnectInput, user_id=Depends(authenticated_user)):
        require_writes()
        load(user_id)
        if not app.state.vault:
            raise HTTPException(503, "Configure Google OAuth and token encryption before connecting")
        nonce = secrets.token_urlsafe(32)
        result = JSONResponse({"authorization_url": app.state.vault.begin(user_id, body.enable_calendar_write,
                               browser_nonce=nonce), "write_requested": body.enable_calendar_write})
        secure = str(getattr(app.state.vault.oauth, "redirect_uri", "")).startswith("https://")
        result.set_cookie("study_calendar_flow", nonce, max_age=600, path="/calendar/google",
                          httponly=True, samesite="lax", secure=secure)
        return result

    @app.get("/calendar/google/callback")
    def oauth_callback(request: Request, state: str, code: str):
        require_writes()
        if not app.state.vault:
            raise HTTPException(503, "Google OAuth is not configured")
        try:
            user_id = app.state.vault.finish(state, code,
                                           browser_nonce=request.cookies.get("study_calendar_flow", ""))
            connected = fresh(user_id, force=True).calendar_connected
        except (ValueError, CalendarError, ConflictError):
            raise HTTPException(400, "Calendar authorization failed or expired; reconnect") from None
        result = JSONResponse({"connected": connected, "message": "Only the owner can see imported event titles."})
        result.delete_cookie("study_calendar_flow", path="/calendar/google")
        return result

    @app.post("/calendar/google/refresh")
    def refresh(user_id=Depends(authenticated_user)):
        require_writes()
        value = fresh(user_id, force=True)
        return {"connected": value.calendar_connected, "refreshed_at": value.calendar_refreshed_at}

    @app.delete("/calendar/google/connection")
    def disconnect(user_id=Depends(authenticated_user)):
        require_writes()
        try:
            store.delete_calendar(user_id)
        except ConflictError:
            raise HTTPException(409, "Schedule changed while disconnecting; retry") from None
        return {"disconnected": True, "local_tokens_and_imported_events_deleted": True,
                "google_account_grant_revoked": False}

    @app.get("/sessions/{session_id}/calendar-preview")
    def calendar_preview(session_id: str, user_id=Depends(authenticated_user)):
        data = session_for_owner(session_id, user_id)
        session_valid(data)
        payload = preview_payload(data)
        return {**payload, "confirmation_token": sign(payload), "effect": "Add one event to organizer's primary calendar; no guest invitations"}

    @app.post("/sessions/{session_id}/calendar")
    def calendar_export(session_id: str, body: Confirmation, user_id=Depends(authenticated_user)):
        require_writes()
        data = session_for_owner(session_id, user_id)
        verify(body.confirmation_token, preview_payload(data))
        session_valid(data)
        if app.state.calendar is None:
            raise HTTPException(503, "Calendar connector is not configured; nothing was added")
        try:
            receipt = app.state.calendar.create_event(user_id, session_id, data["subject"], interval(data),
                                                      data["place"], payload_version=data["version"])
        except CalendarWriteUncertain:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=202, content={"status": "UNKNOWN", "message": "Write outcome needs verification; keep the same session id"})
        except (CalendarError, ValueError):
            raise HTTPException(502, "Calendar operation was not verified; do not assume an event was added") from None
        return {"event_id": receipt.event_id, "status": receipt.status, "html_link": receipt.html_link,
                "calendar_owner_id": user_id, "guest_invites_sent": False}

    return app
