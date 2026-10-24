"""Shared shapes. Feature tracks extend behavior, not these names."""

from typing import Literal

from pydantic import BaseModel, Field

StudyStyleChoice = Literal["quiet", "talk", "drill"]
# "any" means no preference. Only the derived single study_style uses it.
StudyStyle = Literal["quiet", "talk", "drill", "any"]
Anchor = Literal["home", "campus"]
PlaceType = Literal["coffee", "library", "outdoor"]
GroupSize = Literal[2, 3, 4, 5]
BusySource = Literal["weekly", "exam", "calendar", "exception"]
InvitationStatus = Literal["draft", "pending", "accepted", "declined"]
GenderChoice = Literal["male", "female", "other"]
Gender = Literal["", "male", "female", "other"]


class Course(BaseModel):
    id: str
    code: str
    name: str


class PublicProfile(BaseModel):
    """study_styles, group_min/group_max, and place_note are the student's answers.

    study_style and group_size are derived from them so older matching code
    keeps working: the first ticked style ("any" when none), and group_min.
    """

    id: str
    name: str
    study_style: StudyStyle
    study_styles: list[StudyStyleChoice] = []
    study_style_note: str = ""
    radius_meters: int
    anchor: Anchor
    anchor_lat: float
    anchor_lng: float
    group_size: GroupSize
    group_min: int = Field(default=2, ge=2, le=5)
    group_max: int = Field(default=2, ge=2, le=5)
    place_types: list[PlaceType]
    place_note: str = ""
    major: str
    year: int = Field(ge=1, le=5)
    course_ids: list[str]
    bio: str = ""


class PrivateProfile(PublicProfile):
    """Owner-only. Matching must not receive this model."""

    email: str
    street_address: str
    gender_self: Gender = ""
    gender_preference: list[GenderChoice] = []
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
