"""Thin connections to Marc's existing request/Send entry points. No scorer changes."""
from .adapters import TeamSessionGateway
from .google_config import GoogleActor
from .google_integration import GoogleCalendarIntegration
from .export_models import ExportError
from .export_service import CalendarExportService
from .core.schemas import TrustedMatchContext, Actor


def check_room_time(request, participant_ids, starts_at, ends_at):
    """Recheck server-resolved room members and times before a room mutation.

    This is a Session coordinator hook, not a model or Matching tool. It returns
    no private titles and does not turn synthetic rooms into live Session authority.
    """
    from app import store
    from .runtime import get_runtime
    from .time import slot
    from .core.schemas import Horizon
    from .core.service import AuthorizationError
    current_id = (request.session.get("user") or {}).get("id")
    subject_ids = set(participant_ids) | {current_id}
    active = getattr(request.app.state, "google_calendar_integration", None)
    try:
        requested = slot(starts_at, ends_at)
        horizon = Horizon(start=requested.start, end=requested.end)
        if isinstance(active, GoogleCalendarIntegration):
            actor = getattr(request.state, "calendar_actor", None)
            resolver = getattr(request.app.state, "calendar_match_context_for_actor", None)
            if not isinstance(actor, GoogleActor) or actor.subject_id != current_id or not callable(resolver):
                raise AuthorizationError()
            context = resolver(actor)
            if not isinstance(context, TrustedMatchContext) or not subject_ids <= context.eligible_subject_ids:
                raise AuthorizationError()
            active.refresh_calendars(context, sorted(subject_ids))
            service = active.service
        else:
            known = {store.OWNER_ID, *(person["id"] for person in store.others())}
            if not current_id or not subject_ids <= known:
                raise AuthorizationError()
            context = TrustedMatchContext(eligible_subject_ids=frozenset(subject_ids))
            runtime = get_runtime()
            from .demo import FIXTURE_HORIZON
            for subject_id in sorted(subject_ids):
                runtime.service.refresh_calendar(Actor(subject_id=subject_id), FIXTURE_HORIZON)
            service = runtime.service
        codes = set()
        for subject_id in sorted(subject_ids):
            decision = service.check_candidate_slot(context, subject_id, requested, horizon)
            codes.update(conflict.code for conflict in decision.conflicts)
    except (AuthorizationError, ConnectionError):
        raise ValueError("Participant calendars could not be verified. Refresh or connect the team account before continuing.") from None
    except ValueError:
        raise ValueError("Choose a valid time and resolve any timezone or DST ambiguity.") from None
    if "CALENDAR_UNKNOWN" in codes:
        raise ValueError("A participant's calendar is unknown or stale. Refresh before continuing.")
    if "EXAM_BUSY" in codes:
        raise ValueError("That time overlaps a participant's exam. Choose another time.")
    if codes:
        raise ValueError("That time is outside a participant's study hours or overlaps a busy block. Choose another time.")


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
