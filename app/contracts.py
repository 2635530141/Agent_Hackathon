"""Shared shapes. Feature tracks extend behavior, not these names."""

from typing import Literal

from pydantic import BaseModel, Field

StudyStyle = Literal["quiet", "talk", "drill"]
Anchor = Literal["home", "campus"]
PlaceType = Literal["coffee", "library", "outdoor"]
GroupSize = Literal[2, 3, 4]
BusySource = Literal["weekly", "exam", "calendar", "exception"]
InvitationStatus = Literal["draft", "pending", "accepted", "declined"]
Gender = Literal["", "woman", "man", "nonbinary"]


class Course(BaseModel):
    id: str
    code: str
    name: str


class PublicProfile(BaseModel):
    id: str
    name: str
    study_style: StudyStyle
    radius_meters: int
    anchor: Anchor
    anchor_lat: float
    anchor_lng: float
    group_size: GroupSize
    place_types: list[PlaceType]
    major: str
    year: int = Field(ge=1, le=5)
    course_ids: list[str]


class PrivateProfile(PublicProfile):
    """Owner-only. Matching must not receive this model."""

    email: str
    street_address: str
    gender_self: Gender = ""
    gender_preference: Gender = ""
    under_18: bool = False


class BusyInterval(BaseModel):
    id: str
    source: BusySource
    starts_at: str
    ends_at: str
    title: str | None = None
    kind: Literal["free", "busy"] | None = None


class MatchBusyInterval(BaseModel):
    """Busy interval with the event title removed."""

    id: str
    source: BusySource
    starts_at: str
    ends_at: str
    kind: Literal["free", "busy"] | None = None


class Exam(BaseModel):
    id: str
    course_id: str
    starts_at: str
    ends_at: str
    priority: bool = False
    priority_starts_at: str | None = None


class StudyRequest(BaseModel):
    course_id: str
    starts_at: str
    ends_at: str
    place_type: PlaceType
    note: str = ""
    study_anyway: bool = False


class ProposalPerson(BaseModel):
    """Card copy is first name, year, major, and the shared course. No email."""

    id: str
    first_name: str
    year: int
    major: str
    course_code: str


class ProposalPlace(BaseModel):
    name: str
    address: str
    kind: PlaceType
    hours: str
    rating: str
    map_url: str


class Proposal(BaseModel):
    people: list[ProposalPerson]
    starts_at: str
    ends_at: str
    place: ProposalPlace
    reason: str
    invite: str


class Invitation(BaseModel):
    id: str
    course_code: str
    starts_at: str
    ends_at: str
    place_name: str
    status: InvitationStatus
    calendar_written: bool = False
