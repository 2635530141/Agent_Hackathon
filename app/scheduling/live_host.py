"""Loopback-only Google read preview; no synthetic identities, sessions or writes.

Production composition still needs the team's auth, course and Session ports.
"""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Request
from .core.storage import SQLiteScheduleRepository
from .core.schemas import Actor
from .google_config import GoogleActor
from .runtime import CalendarRuntime


class CalendarOnlySessions:
    # This preview has no Session feature. Never authorize an export through it.
    authoritative = False

    def confirmed_reservations(self, subject_id, horizon):
        return []


@dataclass
class OwnerContext:
    actor: GoogleActor
    runtime: CalendarRuntime
    host: object


class PrivateCalendarHost:
    def __init__(self, repository, active, accounts, session_secret):
        if len(session_secret) < 43 or session_secret == "studymate-demo":
            raise ValueError("A private random session secret is required")
        self.repository, self.active, self.accounts = repository, active, accounts
        self.session_secret = session_secret
        self.runtime = CalendarRuntime(repository, active.service)
        uri = urlsplit(active.oauth.config.redirect_uri)
        if uri.scheme != "http" or uri.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("The private preview requires an HTTP loopback callback")
        self.origin = f"{uri.scheme}://{uri.netloc}"

    def context(self, user, session):
        from .core.service import ScheduleService
        runtime = CalendarRuntime(self.repository, ScheduleService(self.repository, self.active.provider,
            self.active.service.sessions, timezone_name=user["timezone"], clock=self.active.oauth.clock))
        return OwnerContext(GoogleActor(subject_id=user["id"], timezone=user["timezone"],
            session_binding=session["login_binding"], csrf_token=session["calendar_editor_csrf"]), runtime, self)

    def status(self, actor):
        from .google_provider import google_window
        state = self.repository.read_bundle(actor.subject_id)["state"]
        connected = state["connection_status"] not in {"not_connected", "disconnected"}
        checked = self.active.service._calendar_status(state,google_window(self.active.oauth.clock(),actor.timezone))
        return {"mode":"google-readonly", "connected":connected, "configuration_status":"configured",
            "connection_status":checked.connection_status,
            "message":"Only your primary Google calendar is read. Titles are private to your account.",
            "last_success_at":state.get("last_success_at"), "coverage_start":state.get("coverage_start"),
            "coverage_end":state.get("coverage_end")}


def build_private_host(*, database_url, client_file, encryption_key_file, session_secret_file, redirect_uri):
    from app.accounts import PersistentAccounts
    from .google_setup import build_google_integration
    from sqlalchemy import inspect, select
    from sqlalchemy.engine import make_url
    from .core.storage import markers
    if make_url(database_url).get_backend_name() != "sqlite":
        raise ValueError("The private preview requires a local SQLite database")
    repository = SQLiteScheduleRepository(database_url)
    if inspect(repository.engine).has_table(markers.name):
        with repository.engine.connect() as connection:
            if set(connection.execute(select(markers.c.key)).scalars()) & {"team-synthetic-v1","synthetic-fixture-v1"}:
                repository.close()
                raise ValueError("The private preview must not use the synthetic database")
    repository.migrate()
    sessions = CalendarOnlySessions()
    accounts = PersistentAccounts(repository.engine)
    active = build_google_integration(repository, sessions,
        lambda owner: accounts.get(owner)["timezone"], client_file=client_file,
        encryption_key_file=encryption_key_file, redirect_uri=redirect_uri)
    return PrivateCalendarHost(repository, active, accounts, Path(session_secret_file).read_text().strip())


async def owner_access(request: Request):
    """Owner dependency shared by legacy forms and the timetable JSON API."""
    from .context import live_context
    context = live_context()
    if context:
        from fastapi import HTTPException
        attempted = request.query_params.get("user_id") or request.query_params.get("owner_id")
        if attempted and attempted != context.actor.subject_id:
            raise HTTPException(403, "NOT_OWNER")
        return Actor(subject_id=context.actor.subject_id)
    from .demo import demo_owner
    return demo_owner(request)
