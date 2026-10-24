"""Explicit owner/private and matching-safe schemas. All concrete instants are UTC."""
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OpaqueId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Interval(Model):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value):
        return as_utc(value)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Horizon(Interval):
    @model_validator(mode="after")
    def bounded(self):
        if self.end - self.start > timedelta(days=31):
            raise ValueError("horizon must be at most 31 elapsed days")
        return self


class WeeklyRuleInput(Model):
    weekday: Annotated[int, Field(ge=0, le=6, strict=True)]
    start_local: time
    end_local: time
    ends_next_day: bool = False
    timezone: str = "America/Los_Angeles"
    valid_from: date | None = None
    valid_until: date | None = None

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("valid IANA timezone required") from None
        return value

    @field_validator("start_local", "end_local")
    @classmethod
    def wall_time(cls, value):
        if value.tzinfo is not None:
            raise ValueError("weekly times must be local wall times without offsets")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if not self.ends_next_day and self.end_local <= self.start_local:
            raise ValueError("overnight rules require ends_next_day=true")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until must be on or after valid_from")
        return self


class WeeklyRule(WeeklyRuleInput):
    id: OpaqueId
    owner_id: OpaqueId


class ExceptionInput(Interval):
    kind: Literal["available", "unavailable"]


class ScheduleException(ExceptionInput):
    id: OpaqueId
    owner_id: OpaqueId


class ExamInput(Interval):
    course_id: OpaqueId
    priority_start: datetime | None = None

    @field_validator("priority_start")
    @classmethod
    def priority_utc(cls, value):
        return as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def priority_before_exam(self):
        if self.priority_start is not None and self.priority_start >= self.start:
            raise ValueError("priority_start must precede exam start")
        return self


class Exam(ExamInput):
    id: OpaqueId
    owner_id: OpaqueId


class Actor(Model):
    subject_id: OpaqueId


class TrustedMatchContext(Model):
    """Construct only in trusted server auth/eligibility adapters, never deserialize a browser body."""
    eligible_subject_ids: frozenset[OpaqueId]


class BusyBlock(Interval):
    id: OpaqueId


class PrivateEvent(BusyBlock):
    private_title: Annotated[str, Field(max_length=500)] = Field(repr=False)


class ProviderSnapshot(Model):
    provider_kind: Literal["fake", "google"] = "fake"
    coverage: Horizon
    busy: list[BusyBlock]
    private_events: list[PrivateEvent] = Field(default_factory=list, repr=False)
    complete: bool = True


class Reservation(Interval):
    session_id: OpaqueId


class CalendarStatus(Model):
    provider_kind: Literal["fake", "google", "none"] = "none"
    connection_status: Literal["not_connected", "disconnected", "synced", "stale"] = "not_connected"
    verification_level: Literal["synthetic", "manual_only", "google_current", "unknown"] = "manual_only"
    last_success_at: datetime | None = None
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    snapshot_version: int = 0


class PriorityWindow(Interval):
    course_id: OpaqueId


class AvailabilitySnapshot(Model):
    subject_id: OpaqueId
    timezone: str
    horizon: Horizon
    available: list[Interval]
    priority_windows: list[PriorityWindow]
    calendar: CalendarStatus
    schedule_version: int
    warnings: list[str]


class OwnerScheduleView(Model):
    weekly_rules: list[WeeklyRule]
    exceptions: list[ScheduleException]
    exams: list[Exam]
    private_calendar_events: list[PrivateEvent] = Field(repr=False)
    confirmed_reservations: list[Reservation]
    availability: AvailabilitySnapshot


ConflictCode = Literal["EXAM_BUSY", "MANUAL_BUSY", "CALENDAR_BUSY", "CONFIRMED_SESSION_BUSY", "OUTSIDE_STUDY_HOURS", "CALENDAR_UNKNOWN"]


class SafeConflict(Model):
    code: ConflictCode
    interval: Interval
    blocking: bool = True
    override_eligible: bool = False


class OwnerConflict(SafeConflict):
    owner_only_private_title: str | None = Field(default=None, repr=False)


class ConflictDecision(Model):
    slot: Interval
    conflicts: list[SafeConflict]
    alternative_slot: Interval | None
    calendar_verification_level: Literal["synthetic", "manual_only", "google_current", "unknown"]
    schedule_version: int
    blocked: bool
    preview_only: Literal[True] = True


class OwnerConflictDecision(ConflictDecision):
    conflicts: list[OwnerConflict]


class SlotCheck(Model):
    slot: Interval
    horizon: Horizon

    @model_validator(mode="after")
    def contained(self):
        if self.slot.start < self.horizon.start or self.slot.end > self.horizon.end:
            raise ValueError("slot must be contained in horizon")
        return self
