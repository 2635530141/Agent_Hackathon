"""Explicit opt-in composition for an authenticated host, not the synthetic runtime."""
from concurrent.futures import ThreadPoolExecutor
from .core.schemas import Actor, TrustedMatchContext
from .core.service import AuthorizationError, ScheduleService
from .google_config import GoogleActor
from .google_provider import google_window
from .google_storage import GoogleStateError


class GoogleCalendarIntegration:
    def __init__(self, repository, vault, oauth, provider, session_gateway):
        if repository.engine is not vault.engine:
            raise ValueError("OAuth and schedule changes must share one transaction boundary")
        self.repository, self.vault, self.oauth, self.provider = repository,vault,oauth,provider
        self.service = ScheduleService(repository,provider,session_gateway,clock=oauth.clock)

    def complete(self, actor, code, state):
        with self.oauth.owner_lock(actor.subject_id):
            self.oauth.complete(actor,code,state,after_save=lambda conn:
                self.repository.mark_connected(actor.subject_id,connection=conn))

    def refresh(self, actor):
        if not isinstance(actor,GoogleActor):
            raise AuthorizationError("AUTHENTICATED_TEAM_OWNER_REQUIRED")
        with self.oauth.owner_lock(actor.subject_id):
            self.vault.load(actor.subject_id)  # disconnected is not silently reconnected
            window = google_window(self.oauth.clock(),actor.timezone)
            return self.service.refresh_calendar(Actor(subject_id=actor.subject_id),window)

    def disconnect(self, actor):
        with self.oauth.owner_lock(actor.subject_id):
            self.vault.disconnect(actor.subject_id,on_disconnected=lambda conn:
                self.repository.disconnect(actor.subject_id,connection=conn))

    def refresh_calendars(self, context, subject_ids):
        """Marc's server-only coordinator seam; not used by Matching or browser IDs."""
        if (not isinstance(context,TrustedMatchContext) or len(subject_ids)>100
            or not set(subject_ids)<=context.eligible_subject_ids):
            raise AuthorizationError("NOT_AUTHORIZED")
        def refresh_one(subject_id):
            try:
                self.vault.load(subject_id)
            except GoogleStateError as exc:
                if str(exc)=="GOOGLE_NOT_CONNECTED":
                    return subject_id,"manual_only"
                return subject_id,"unknown"
            window = google_window(self.oauth.clock(),self.provider.timezone_for_owner(subject_id))
            status = self.service.refresh_calendar(Actor(subject_id=subject_id),window)
            return subject_id,status.verification_level
        with ThreadPoolExecutor(max_workers=4) as executor:
            return dict(executor.map(refresh_one,sorted(set(subject_ids))))
