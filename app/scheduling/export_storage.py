"""Durable logical operation, approval and lease; no transaction across network IO."""
import hashlib
from datetime import timedelta
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4
from sqlalchemy import Column, Integer, JSON, MetaData, String, Table, insert, select, update, or_
from .core.storage import UTCDateTime

meta = MetaData()
operations = Table("smcal_exports",meta,
    Column("logical_key",String,primary_key=True),Column("owner_id",String),Column("session_id",String),
    Column("calendar_id",String),Column("event_id",String),Column("marker",String),
    Column("fingerprint",String),Column("session_revision",String),Column("account_subject",String),Column("generation",Integer),
    Column("payload",JSON),Column("status",String),Column("intent",String),Column("last_error",String),Column("html_link",String),
    Column("lease_owner",String),Column("lease_until",UTCDateTime),Column("exported_at",UTCDateTime),
    Column("removed_at",UTCDateTime),Column("updated_at",UTCDateTime))
approvals = Table("smcal_export_approvals",meta,
    Column("approval_hash",String,primary_key=True),Column("logical_key",String),
    Column("owner_id",String),Column("fingerprint",String),Column("generation",Integer),
    Column("kind",String),Column("expires_at",UTCDateTime))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class ExportRepository:
    def __init__(self, engine):
        self.engine = engine

    def migrate(self):
        source = (Path(__file__).parent/"core/migrations/003_export_operations.sql").read_text(encoding="utf-8")
        with self.engine.begin() as conn:
            for statement in source.split(";"):
                if statement.strip():
                    conn.exec_driver_sql(statement)

    def get(self, owner_id, session_id, now, create=False):
        key = digest(owner_id+"\0"+session_id+"\0primary")
        with self.engine.begin() as conn:
            if create:
                marker = uuid4().hex
                conn.execute(insert(operations).prefix_with("OR IGNORE").values(
                    logical_key=key,owner_id=owner_id,session_id=session_id,calendar_id="primary",
                    event_id="smcal"+digest(key+marker),marker=marker,status="new",updated_at=now))
            row = conn.execute(select(operations).where(operations.c.logical_key==key)).mappings().first()
        return dict(row) if row else None

    def approve(self, op, fingerprint, generation, kind, now):
        token = token_urlsafe(32)
        with self.engine.begin() as conn:
            conn.execute(insert(approvals).values(approval_hash=digest(token),logical_key=op["logical_key"],
                owner_id=op["owner_id"],fingerprint=fingerprint,generation=generation,kind=kind,
                expires_at=now+timedelta(minutes=10)))
        return token

    def approved(self, op, token, fingerprint, generation, kind, now):
        with self.engine.connect() as conn:
            return conn.execute(select(approvals.c.approval_hash).where(
                approvals.c.approval_hash==digest(token),approvals.c.logical_key==op["logical_key"],
                approvals.c.owner_id==op["owner_id"],approvals.c.fingerprint==fingerprint,
                approvals.c.generation==generation,approvals.c.kind==kind,
                approvals.c.expires_at>now)).first() is not None

    def claim(self, op, status, now):
        lease = uuid4().hex
        with self.engine.begin() as conn:
            matched = conn.execute(update(operations).where(
                operations.c.logical_key==op["logical_key"],operations.c.status==op["status"],
                or_(operations.c.lease_until.is_(None),operations.c.lease_until<=now)).values(
                    status=status,lease_owner=lease,lease_until=now+timedelta(minutes=2),updated_at=now)).rowcount
        return lease if matched else None

    def save(self, op, lease, now, **values):
        with self.engine.begin() as conn:
            return conn.execute(update(operations).where(operations.c.logical_key==op["logical_key"],
                operations.c.lease_owner==lease).values(**values,updated_at=now,lease_owner=None,lease_until=None)).rowcount==1

    def checkpoint(self, op, lease, **values):
        with self.engine.begin() as conn:
            return conn.execute(update(operations).where(operations.c.logical_key==op["logical_key"],
                operations.c.lease_owner==lease).values(**values)).rowcount==1
