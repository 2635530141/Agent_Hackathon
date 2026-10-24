"""Thin connections to Marc's existing request/Send entry points. No scorer changes."""
from .adapters import TeamSessionGateway
from .google_config import GoogleActor
from .google_integration import GoogleCalendarIntegration
from .export_models import ExportError
from .export_service import CalendarExportService
from .core.schemas import TrustedMatchContext, Actor


def refresh_before_request(request):
    active = getattr(request.app.state,"google_calendar_integration",None)
    actor = getattr(request.state,"calendar_actor",None)
    if isinstance(active,GoogleCalendarIntegration):
        resolver = getattr(request.app.state,"calendar_match_context_for_actor",None)
        if not isinstance(actor,GoogleActor) or not callable(resolver):
            raise ValueError("Verified identity and server candidate eligibility are required.")
        context = resolver(actor)
        if not isinstance(context,TrustedMatchContext) or actor.subject_id not in context.eligible_subject_ids:
            raise ValueError("Invalid server candidate eligibility.")
        return active.refresh_calendars(context,sorted(context.eligible_subject_ids))
    # Known synthetic fixtures only, never contact Google from the shared demo identity.
    from app import store
    from .runtime import get_runtime
    from .demo import FIXTURE_HORIZON
    runtime = get_runtime()
    for user in [store.OWNER_ID,*(p["id"] for p in store.others())]:
        runtime.service.refresh_calendar(Actor(subject_id=user),FIXTURE_HORIZON)
    return {"mode":"synthetic"}


def export_card(request):
    active = getattr(request.app.state,"calendar_export_service",None)
    actor = getattr(request.state,"calendar_actor",None)
    if isinstance(active,CalendarExportService) and isinstance(actor,GoogleActor):
        # Host resolves the current Session ID from its own backend, not a browser field.
        resolver = getattr(request.app.state,"calendar_current_session_id",None)
        session_id = resolver(actor) if callable(resolver) else None
        if session_id:
            try:
                return {**active.preview(actor,session_id),"csrf":actor.csrf_token,"live":active.writer.is_live}
            except ExportError:
                return {"status":"AUTHORITATIVE_SESSION_REQUIRED","live":False}
    current = TeamSessionGateway().current_session()
    return {"status":"WAITING_FOR_BOTH_ACCEPTANCES" if current and current.status=="pending" else
        "AUTHORITATIVE_SESSION_REQUIRED","live":False,
        "course":current.course if current else None,"place":current.place if current else None}
