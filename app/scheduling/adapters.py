"""Read the team's existing synthetic Session state; no duplicate session DB."""
from app import store
from .core.intervals import overlaps
from .core.schemas import Reservation
from .time import parse_instant


class TeamSessionGateway:
    """Synthetic until Marc supplies authoritative session ID/revision/reservations.

    This never authorizes calendar export, and is not acceptance proof for live use.
    """
    def confirmed_reservations(self, subject_id, horizon):
        items = store.latest_invitations()
        if not items or not store.batch_all_accepted():
            return []
        participants = {store.OWNER_ID, *(i["person_id"] for i in items)}
        if subject_id not in participants:
            return []
        first = items[0]
        block = Reservation(session_id="session-" + first["id"],
            start=parse_instant(first["starts_at"]), end=parse_instant(first["ends_at"]))
        return [block] if overlaps(block, horizon) else []
