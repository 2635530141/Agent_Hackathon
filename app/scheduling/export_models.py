"""Calendar's read model of Marc's session, never a second session lifecycle."""
import hashlib
import json
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import Field, model_validator
from .core.schemas import Interval, Model, OpaqueId


class ExportSession(Interval):
    session_id: OpaqueId
    organizer_id: OpaqueId
    participant_ids: frozenset[OpaqueId]
    accepted_ids: frozenset[OpaqueId]
    revision: str = Field(min_length=1,max_length=128)
    timezone: str
    course: str = Field(min_length=1,max_length=120)
    place: str = Field(min_length=1,max_length=200)
    status: Literal["pending","confirmed","cancelled","expired"]
    authoritative: bool = False

    @model_validator(mode="after")
    def valid_participants(self):
        ZoneInfo(self.timezone)
        if self.organizer_id not in self.participant_ids or not self.accepted_ids<=self.participant_ids:
            raise ValueError("invalid session participants")
        return self

    def fingerprint(self):
        content = self.model_dump(mode="json",exclude={"accepted_ids","status","authoritative"})
        content["participant_ids"] = sorted(self.participant_ids)
        return hashlib.sha256(json.dumps(content,sort_keys=True,separators=(",",":")).encode()).hexdigest()

    def event_body(self):
        # Public course and venue label only; no notes, profiles, attendee lists or addresses.
        return {"summary":f"StudyMate · {self.course}","location":self.place,
            "description":"Confirmed StudyMate study session. Invitations are managed in StudyMate.",
            "start":{"dateTime":self.start.isoformat(),"timeZone":self.timezone},
            "end":{"dateTime":self.end.isoformat(),"timeZone":self.timezone},
            "reminders":{"useDefault":False}}


class AuthoritativeSessionGateway:
    """Host must bind its transaction/lock + revision-aware reader and reservations.

    The guard must exclude concurrent session mutation through the final read/write.
    No default authority: the demo gateway cannot be upgraded by browser flags.
    """
    authoritative = True

    def __init__(self, read_session, confirmed_reservations, guard):
        self.read_session = read_session
        self.confirmed_reservations = confirmed_reservations
        self.guard = guard


class ExportError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code,status
