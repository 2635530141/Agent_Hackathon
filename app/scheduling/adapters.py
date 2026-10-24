"""Read the team's existing synthetic Session state; no duplicate session DB."""
from app import store
from .core.intervals import overlaps
from .core.schemas import Reservation
from .time import parse_instant


class TeamSessionGateway:
    """Synthetic until Marc supplies authoritative session ID/revision/reservations.

    This never authorizes calendar export, and is not acceptance proof for live use.
    """
    authoritative = False

    def guard(self, session_id):
        from contextlib import nullcontext
        return nullcontext()

    def read_session(self, session_id):
        import hashlib
        import json
        from .export_models import ExportSession
        from .time import display_zone
        items = store.latest_invitations()
        if not items or session_id != "session-"+items[0]["id"]:
            return None
        first = items[0]
        fields = ("starts_at","ends_at","place_name","course_code")
        if any(any(item.get(key)!=first.get(key) for key in fields) for item in items):
            return None
        revision = hashlib.sha256(json.dumps([{k:v for k,v in i.items() if k not in
            {"status","message","first_name"}} for i in items],sort_keys=True).encode()).hexdigest()
        accepted = {store.OWNER_ID, *(i["person_id"] for i in items if i["status"]=="accepted")}
        return ExportSession(session_id=session_id,organizer_id=store.OWNER_ID,
            participant_ids={store.OWNER_ID,*(i["person_id"] for i in items)},accepted_ids=accepted,
            revision=revision,start=parse_instant(first["starts_at"]),end=parse_instant(first["ends_at"]),
            timezone=display_zone(),course=first.get("course_code","Study session"),
            place=first.get("place_name","Study venue"),
            status="cancelled" if any(i["status"]=="declined" for i in items) else
                "confirmed" if store.batch_all_accepted() else "pending",authoritative=False)

    def current_session(self):
        items = store.latest_invitations()
        return self.read_session("session-"+items[0]["id"]) if items else None
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
