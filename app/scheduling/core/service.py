"""Scheduling orchestration. Reads saved snapshots; never calls a model or scorer."""
from datetime import datetime, timedelta, timezone
from .intervals import alternative, clip, duration_filter, overlaps, subtract
from .ports import CalendarProvider, ScheduleRepository, SessionGateway
from .schemas import (Actor, AvailabilitySnapshot, CalendarStatus, ConflictDecision,
                      Horizon, Interval, OwnerConflict, OwnerConflictDecision,
                      OwnerScheduleView, PriorityWindow, SafeConflict, SlotCheck,
                      TrustedMatchContext)


class AuthorizationError(Exception):
    pass


class ScheduleService:
    def __init__(self, repository: ScheduleRepository, provider: CalendarProvider,
                 sessions: SessionGateway, clock=None, timezone_name="America/Los_Angeles",
                 freshness_seconds=300):
        self.repository = repository
        self.provider = provider
        self.sessions = sessions
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timezone_name = timezone_name
        self.freshness_seconds = freshness_seconds

    @staticmethod
    def _authorize(context, subject_id):
        if isinstance(context, Actor) and context.subject_id == subject_id:
            return
        if isinstance(context, TrustedMatchContext) and subject_id in context.eligible_subject_ids:
            return
        raise AuthorizationError("NOT_AUTHORIZED")

    def _calendar_status(self, state, horizon):
        disconnected = state["connection_status"] in ("not_connected", "disconnected")
        covered = (state["coverage_start"] is not None and state["coverage_end"] is not None
            and state["coverage_start"] <= horizon.start and state["coverage_end"] >= horizon.end)
        last = state["last_success_at"]
        age = (self.clock() - last).total_seconds() if last is not None else None
        fresh = age is not None and 0 <= age <= self.freshness_seconds
        verified = not disconnected and state["connection_status"] == "synced" and covered and fresh
        return CalendarStatus(
            provider_kind="none" if disconnected else state.get("provider_kind", "fake"),
            connection_status=state["connection_status"] if disconnected or verified else "stale",
            verification_level=("manual_only" if disconnected else
                ("google_current" if state.get("provider_kind") == "google" else "synthetic") if verified else "unknown"),
            last_success_at=last, coverage_start=state["coverage_start"], coverage_end=state["coverage_end"],
            snapshot_version=state["snapshot_version"])

    def _compute(self, subject_id, horizon, bundle, exclude_session_id=None, exclude_session_ids=frozenset()):
        # No study hours: everyone is available unless something blocks the time.
        # Stored weekly rules and "extra study time" are kept but no longer limit anything.
        warnings = []
        base = [Interval(start=horizon.start, end=horizon.end)]
        manual = [Interval(start=e.start, end=e.end) for e in bundle["exceptions"] if e.kind == "unavailable"]
        exams = [Interval(start=e.start, end=e.end) for e in bundle["exams"]]
        reservations = self.sessions.confirmed_reservations(subject_id, horizon)
        reservations = [r for r in reservations if r.session_id != exclude_session_id and r.session_id not in exclude_session_ids]
        free = clip(subtract(base, manual + exams + bundle["busy"] + reservations), horizon)
        priority = []
        for exam in bundle["exams"]:
            if exam.priority_start is not None:
                for window in clip([Interval(start=exam.priority_start, end=exam.start)], horizon):
                    priority.append(PriorityWindow(**window.model_dump(), course_id=exam.course_id))
        status = self._calendar_status(bundle["state"], horizon)
        if status.verification_level == "unknown":
            warnings.append("CALENDAR_UNKNOWN: cached snapshot retained; no eligible slots until successful refresh")
            free = []
        elif status.verification_level == "manual_only":
            warnings.append("MANUAL_ONLY: external calendar not checked")
        elif status.verification_level == "synthetic":
            warnings.append("SYNTHETIC: fake calendar data; not verified Google integration")
        else:
            warnings.append("PRIMARY_CALENDAR_ONLY: complete stored read snapshot; refresh required again before Send")
        snapshot = AvailabilitySnapshot(subject_id=subject_id, timezone=self.timezone_name,
            horizon=horizon, available=free, priority_windows=sorted(priority, key=lambda p: (p.start, p.course_id)),
            calendar=status, schedule_version=bundle["state"]["schedule_version"], warnings=warnings)
        return snapshot, base, manual, reservations

    def effective_availability(self, actor_context, subject_id: str, horizon: Horizon,
                               duration_minutes: int = 1) -> AvailabilitySnapshot:
        self._authorize(actor_context, subject_id)
        if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int) or duration_minutes <= 0:
            raise ValueError("duration_minutes must be a positive integer")
        duration = timedelta(minutes=duration_minutes)
        if duration > horizon.end - horizon.start:
            raise ValueError("duration must not exceed horizon")
        bundle = self.repository.read_bundle(subject_id)
        snapshot, *_ = self._compute(subject_id, horizon, bundle)
        return snapshot.model_copy(update={"available": duration_filter(snapshot.available, duration)})

    def batch_availability(self, context: TrustedMatchContext, subject_ids: list[str],
                           horizon: Horizon, duration_minutes: int):
        if not isinstance(context, TrustedMatchContext) or len(subject_ids) > 100:
            raise AuthorizationError("NOT_AUTHORIZED")
        for subject_id in subject_ids:
            self._authorize(context, subject_id)
        return [self.effective_availability(context, i, horizon, duration_minutes) for i in sorted(set(subject_ids))]

    def owner_schedule(self, actor: Actor, horizon: Horizon) -> OwnerScheduleView:
        if not isinstance(actor, Actor):
            raise AuthorizationError("NOT_OWNER")
        subject_id = actor.subject_id
        bundle = self.repository.read_bundle(subject_id)
        snapshot, _, _, reservations = self._compute(subject_id, horizon, bundle)
        return OwnerScheduleView(weekly_rules=bundle["weekly-rules"], exceptions=bundle["exceptions"],
            exams=bundle["exams"], private_calendar_events=[e for e in self.repository.private_events(subject_id)
                if overlaps(e, horizon)], confirmed_reservations=reservations, availability=snapshot)

    def _check(self, subject_id, slot, horizon, exclude_session_id=None, exclude_calendar_ids=frozenset(), exclude_session_ids=frozenset()):
        SlotCheck(slot=slot, horizon=horizon)
        bundle = self.repository.read_bundle(subject_id)
        bundle["busy"] = [b for b in bundle["busy"] if b.id not in exclude_calendar_ids]
        snapshot, base, manual, reservations = self._compute(subject_id, horizon, bundle, exclude_session_id, exclude_session_ids)
        conflicts = []
        for code, items in [("EXAM_BUSY", bundle["exams"]), ("MANUAL_BUSY", manual),
                            ("CALENDAR_BUSY", bundle["busy"]), ("CONFIRMED_SESSION_BUSY", reservations)]:
            for item in items:
                if overlaps(item, slot):
                    conflicts.append(SafeConflict(code=code, interval=Interval(start=item.start, end=item.end)))
        if snapshot.calendar.verification_level == "unknown":
            conflicts.append(SafeConflict(code="CALENDAR_UNKNOWN", interval=slot))
        conflicts.sort(key=lambda c: (c.interval.start, c.code, c.interval.end))
        return ConflictDecision(slot=slot, conflicts=conflicts,
            alternative_slot=alternative(snapshot.available, slot) if conflicts else None,
            calendar_verification_level=snapshot.calendar.verification_level,
            schedule_version=snapshot.schedule_version, blocked=bool(conflicts))

    def check_slot(self, actor: Actor, slot: Interval, horizon: Horizon) -> OwnerConflictDecision:
        if not isinstance(actor, Actor):
            raise AuthorizationError("NOT_OWNER")
        decision = self._check(actor.subject_id, slot, horizon)
        titles = self.repository.private_events(actor.subject_id)
        conflicts = []
        for conflict in decision.conflicts:
            title = None
            if conflict.code == "CALENDAR_BUSY":
                title = next((e.private_title for e in titles
                    if e.start == conflict.interval.start and e.end == conflict.interval.end), None)
            conflicts.append(OwnerConflict(**conflict.model_dump(), owner_only_private_title=title))
        return OwnerConflictDecision(**decision.model_dump(exclude={"conflicts"}), conflicts=conflicts)

    def check_candidate_slot(self, context: TrustedMatchContext, subject_id, slot, horizon, *, exclude_session_ids=frozenset()) -> ConflictDecision:
        if not isinstance(context, TrustedMatchContext):
            raise AuthorizationError("NOT_AUTHORIZED")
        self._authorize(context, subject_id)
        return self._check(subject_id, slot, horizon, exclude_session_ids=exclude_session_ids)

    def refresh_calendar(self, actor: Actor, horizon: Horizon) -> CalendarStatus:
        if not isinstance(actor, Actor):
            raise AuthorizationError("NOT_OWNER")
        owner_id = actor.subject_id
        ticket = self.repository.begin_refresh(owner_id)
        try:
            snapshot = self.provider.read_snapshot(owner_id, horizon)
            # Partial coverage must not be stored as a completed requested refresh.
            if (snapshot.coverage.start > horizon.start or snapshot.coverage.end < horizon.end
                or not snapshot.complete):
                self.repository.fail_refresh(owner_id, ticket)
            else:
                self.repository.finish_refresh(owner_id, ticket, snapshot, self.clock())
        except (TimeoutError, ConnectionError):
            # Never log provider payloads or exception messages.
            self.repository.fail_refresh(owner_id, ticket)
        return self._calendar_status(self.repository.read_bundle(owner_id)["state"], horizon)
