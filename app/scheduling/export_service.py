"""Approval-gated organizer executor. Not a model tool or invitation system."""
from urllib.parse import urlsplit
from .core.schemas import TrustedMatchContext, Horizon, Interval
from .export_models import ExportError, ExportSession
from .google_config import GoogleActor
from .google_storage import GoogleStateError


class CalendarExportService:
    def __init__(self, repository, sessions, writer, schedules, refresh, clock):
        self.repository,self.sessions,self.writer = repository,sessions,writer
        self.schedules,self.refresh,self.clock = schedules,refresh,clock

    def session(self, actor, session_id, eligible=True):
        if not isinstance(actor,GoogleActor):
            raise ExportError("AUTHENTICATED_OWNER_REQUIRED",403)
        session = self.sessions.read_session(session_id)
        if not isinstance(session,ExportSession):
            raise ExportError("SESSION_NOT_FOUND",404)
        if session.organizer_id!=actor.subject_id:
            raise ExportError("ORGANIZER_ONLY",403)
        if eligible:
            if session.status in {"cancelled","expired"} or session.end<=self.clock():
                raise ExportError("SESSION_INACTIVE")
            if len(session.participant_ids)!=2:
                raise ExportError("GROUP_EXPORT_POLICY_PENDING")
            if session.status!="confirmed" or session.accepted_ids!=session.participant_ids:
                raise ExportError("WAITING_FOR_BOTH_ACCEPTANCES")
            if self.writer.is_live and not (session.authoritative and self.sessions.authoritative):
                raise ExportError("AUTHORITATIVE_SESSION_REQUIRED")
        return session

    def binding(self, owner_id):
        try:
            return self.writer.binding(owner_id)
        except GoogleStateError:
            raise ExportError("GOOGLE_CONNECTION_REQUIRED") from None

    def preview(self, actor, session_id):
        session = self.session(actor,session_id,eligible=False)
        op = self.repository.get(actor.subject_id,session_id,self.clock())
        result = {"session_id":session_id,"fingerprint":session.fingerprint(),
            "course":session.course,"starts_at":session.start.isoformat(),"ends_at":session.end.isoformat(),
            "timezone":session.timezone,"place":session.place,"target":"Primary calendar",
            "status":"ready","synthetic":not self.writer.is_live}
        account,generation = None,None
        try:
            self.session(actor,session_id)
            account,generation = self.binding(actor.subject_id)
            result["target"] = "Google account …"+account[-6:]+" · Primary calendar"
        except ExportError as exc:
            result["status"] = exc.code
        if op:
            result.update(operation_status=op["status"],html_link=op["html_link"],intent=op["intent"])
            permission_codes={"GOOGLE_CONNECTION_REQUIRED","WRITE_SCOPE_REQUIRED","GOOGLE_ACCOUNT_BINDING_REQUIRED"}
            if op["status"] in {"added","unknown","adding","removing","removed"} and result["status"] not in permission_codes:
                result["status"] = op["status"]
            if op["fingerprint"] and op["fingerprint"]!=session.fingerprint():
                result["status"] = "SESSION_CHANGED_NEW_INVITATION_REQUIRED"
            elif op["status"]=="failed" and result["status"]=="ready":
                result["status"] = "WRITE_SCOPE_REQUIRED" if op["last_error"]=="WRITE_SCOPE_REQUIRED" and op["generation"]==generation else "failed"
            result["delete_fingerprint"] = op["fingerprint"]
            if account and op["account_subject"] and account!=op["account_subject"]:
                result["status"] = "GOOGLE_ACCOUNT_BINDING_CHANGED"
        return result

    def approve(self, actor, session_id, fingerprint, kind="export"):
        self.session(actor,session_id,eligible=kind=="export")
        account,generation = self.binding(actor.subject_id)
        op = self.repository.get(actor.subject_id,session_id,self.clock(),create=kind=="export")
        if not op:
            raise ExportError("NO_APP_EXPORT")
        if op["account_subject"] and op["account_subject"]!=account:
            raise ExportError("GOOGLE_ACCOUNT_BINDING_CHANGED")
        expected = self.session(actor,session_id).fingerprint() if kind=="export" else op["fingerprint"]
        if fingerprint!=expected or (kind=="delete" and op["account_subject"]!=account):
            raise ExportError("PREVIEW_CHANGED")
        return self.repository.approve(op,fingerprint,generation,kind,self.clock())

    @staticmethod
    def expected_body(session, op):
        return {**session.event_body(),"id":op["event_id"],"extendedProperties":{"private":{
            "studymate_operation":op["marker"],"studymate_fingerprint":session.fingerprint()}}}

    @staticmethod
    def verify_remote(remote, op, body):
        try:
            if (remote["id"]!=op["event_id"] or
                remote["extendedProperties"]["private"]!=body["extendedProperties"]["private"] or
                remote.get("summary")!=body["summary"] or remote.get("location","")!=body["location"] or
                Interval(start=remote["start"]["dateTime"],end=remote["end"]["dateTime"]) !=
                Interval(start=body["start"]["dateTime"],end=body["end"]["dateTime"])):
                raise ValueError("remote mismatch")
            link = remote["htmlLink"]
            parsed = urlsplit(link)
            if parsed.scheme!="https" or parsed.hostname not in {"www.google.com","calendar.google.com"} or parsed.username:
                raise ValueError("unsafe provider link")
            return link
        except (KeyError,TypeError,ValueError):
            raise ExportError("REMOTE_EVENT_CHANGED") from None

    def _approval(self, op, token, fingerprint, generation, kind):
        if not self.repository.approved(op,token,fingerprint,generation,kind,self.clock()):
            raise ExportError("CURRENT_EXPORT_APPROVAL_REQUIRED",403)

    def preflight(self, session, existing_op=None, remote=None):
        context = TrustedMatchContext(eligible_subject_ids=session.participant_ids)
        statuses = self.refresh(context,sorted(session.participant_ids))
        allowed = {"google_current","manual_only"} | ({"synthetic"} if not self.writer.is_live else set())
        if any(statuses.get(p) not in allowed for p in session.participant_ids):
            raise ExportError("CALENDAR_UNVERIFIED")
        excluded = frozenset()
        if existing_op and remote:
            import hashlib
            if (existing_op["owner_id"]!=session.organizer_id or existing_op["session_id"]!=session.session_id
                or existing_op["account_subject"]!=self.binding(session.organizer_id)[0]):
                raise ExportError("UNTRUSTED_SELF_EXCLUSION",403)
            self.verify_remote(remote,existing_op,existing_op["payload"])
            excluded = frozenset({hashlib.sha256((session.organizer_id+"\0"+existing_op["event_id"]).encode()).hexdigest()})
        for subject in sorted(session.participant_ids):
            decision = self.schedules._check(subject,Interval(start=session.start,end=session.end),
                Horizon(start=session.start,end=session.end),exclude_session_id=session.session_id,
                exclude_calendar_ids=excluded if subject==session.organizer_id else frozenset())
            if decision.blocked:
                raise ExportError("BUSY_CONFLICT")  # No private title/candidate detail in errors.

    def export(self, actor, session_id, approval_ref):
        session = self.session(actor,session_id)
        account,generation = self.binding(actor.subject_id)
        fp = session.fingerprint()
        op = self.repository.get(actor.subject_id,session_id,self.clock(),create=True)
        self._approval(op,approval_ref,fp,generation,"export")
        if op["status"]=="removed" or op["intent"]=="delete":
            raise ExportError("EXPORT_REMOVED_NEW_INVITATION_REQUIRED")
        if op["fingerprint"] and op["fingerprint"]!=fp:
            raise ExportError("SESSION_CHANGED_NEW_INVITATION_REQUIRED")
        if op["account_subject"] and op["account_subject"]!=account:
            raise ExportError("GOOGLE_ACCOUNT_BINDING_CHANGED")
        lease = self.repository.claim(op,"adding",self.clock())
        if not lease:
            return {"status":"adding"}
        body = self.expected_body(session,op)
        try:
            with self.sessions.guard(session_id):
                if self.session(actor,session_id).fingerprint()!=fp:
                    raise ExportError("PREVIEW_CHANGED")
                if not self.repository.checkpoint(op,lease,fingerprint=fp,account_subject=account,
                    generation=generation,payload=body,intent="export",session_revision=session.revision):
                    raise ExportError("OPERATION_IN_PROGRESS",202)
                remote = self.writer.get_event(actor.subject_id,op["event_id"])
                if remote is None and op["status"] in {"added","removing"}:
                    self.repository.save(op,lease,self.clock(),status="removed",removed_at=self.clock())
                    return {"status":"removed"}
                if remote is None:
                    self.preflight(session)
                    # Last check after all external reads, with host session mutation guard held.
                    if (self.session(actor,session_id).fingerprint()!=fp or
                        self.binding(actor.subject_id)!=(account,generation)):
                        raise ExportError("PREVIEW_CHANGED")
                    remote = self.writer.create_event(actor.subject_id,body)
                    if remote is None:
                        remote = self.writer.get_event(actor.subject_id,op["event_id"])
                    if remote is None:
                        raise ExportError("PROVIDER_RESULT_UNKNOWN",202)
                link = self.verify_remote(remote,op,body)
                self.repository.save(op,lease,self.clock(),status="added",html_link=link,exported_at=op["exported_at"] or self.clock(),last_error=None)
                return {"status":"added","event_id":op["event_id"],"calendar_id":"primary","html_link":link}
        except ExportError as exc:
            self.repository.save(op,lease,self.clock(),status="unknown" if exc.status==202 else "failed",last_error=exc.code)
            raise

    def delete(self, actor, session_id, approval_ref):
        self.session(actor,session_id,eligible=False)
        op = self.repository.get(actor.subject_id,session_id,self.clock())
        if not op or not op["fingerprint"] or not op["payload"]:
            raise ExportError("NO_APP_EXPORT")
        account,generation = self.binding(actor.subject_id)
        if account!=op["account_subject"]:
            raise ExportError("GOOGLE_ACCOUNT_BINDING_CHANGED")
        self._approval(op,approval_ref,op["fingerprint"],generation,"delete")
        if op["status"]=="removed":
            return {"status":"removed"}
        lease = self.repository.claim(op,"removing",self.clock())
        if not lease:
            return {"status":"removing"}
        try:
            self.repository.checkpoint(op,lease,intent="delete")
            remote = self.writer.get_event(actor.subject_id,op["event_id"])
            if remote:
                self.verify_remote(remote,op,op["payload"])
                if not remote.get("etag"):
                    raise ExportError("PROVIDER_RESULT_UNKNOWN",202)
                if self.binding(actor.subject_id)!=(account,generation):
                    raise ExportError("PREVIEW_CHANGED")
                self.writer.delete_event(actor.subject_id,op["event_id"],remote["etag"])
            self.repository.save(op,lease,self.clock(),status="removed",removed_at=self.clock(),last_error=None)
            return {"status":"removed"}
        except ExportError as exc:
            self.repository.save(op,lease,self.clock(),status="unknown" if exc.status==202 else "failed",last_error=exc.code)
            raise
