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
    def scheduled_rooms(self, subject_id, horizon):
        """Read current seats from Session; joining is the existing acceptance action."""
        result = []
        for room in store.rooms():
            if room.get("status") in {"cancelled", "closed"}:
                continue
            if subject_id not in {person["id"] for person in room["members"]}:
                continue
            block = Reservation(session_id=room["id"], start=parse_instant(room["starts_at"]),
                                end=parse_instant(room["ends_at"]))
            if overlaps(block, horizon):
                result.append((block, room))
        return result

    def reservation_details(self, subject_id, horizon):
        # Owner-facing display metadata only; never part of Matching output.
        from urllib.parse import quote
        return {block.session_id: {"label": room.get("course_code") or "Study session",
                "place": room.get("place_name", ""),
                "session_url": "/session?open="+quote(room["id"], safe="")}
                for block, room in self.scheduled_rooms(subject_id, horizon)}

    def confirmed_reservations(self, subject_id, horizon):
        blocks = {block.session_id: block for block, _ in self.scheduled_rooms(subject_id, horizon)}
        # Keep the older accepted-invitation flow compatible, without copying rooms
        # into Calendar or creating a second Session lifecycle.
        items = store.latest_invitations()
        participants = {store.OWNER_ID, *(i["person_id"] for i in items)}
        if items and store.batch_all_accepted() and subject_id in participants:
            first = items[0]
            block = Reservation(session_id="session-"+first["id"],
                start=parse_instant(first["starts_at"]), end=parse_instant(first["ends_at"]))
            if overlaps(block, horizon):
                blocks[block.session_id] = block
        return sorted(blocks.values(), key=lambda block: (block.start, block.end, block.session_id))
