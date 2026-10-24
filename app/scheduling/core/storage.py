"""SQLite scheduling repository. Private event metadata is in its own table.

Explicit schema migration only; no DB connections or migrations on import.
Postgres compatibility must be verified separately before integration.
"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import (Column, DateTime, Integer, JSON, MetaData, String, Table, event, inspect,
                        create_engine, delete, insert, select, update)
from sqlalchemy.engine import make_url
from sqlalchemy.types import TypeDecorator

from .schemas import (BusyBlock, Exam, ExamInput, ExceptionInput, PrivateEvent,
                      ProviderSnapshot, ScheduleException, WeeklyRule, WeeklyRuleInput)


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC timestamp requires an offset")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


metadata = MetaData()
versions = Table("smcal_schema_version", metadata, Column("version", Integer, primary_key=True))
markers = Table("smcal_seed_markers", metadata, Column("key", String(128), primary_key=True))
states = Table(
    "smcal_schedule_state", metadata,
    Column("owner_id", String(128), primary_key=True),
    Column("schedule_version", Integer, nullable=False, default=0),
    Column("snapshot_version", Integer, nullable=False, default=0),
    Column("refresh_generation", Integer, nullable=False, default=0),
    Column("connection_status", String(24), nullable=False, default="not_connected"),
    Column("last_success_at", UTCDateTime),
    Column("coverage_start", UTCDateTime), Column("coverage_end", UTCDateTime),
)
records = Table(
    "smcal_records", metadata,
    Column("id", String(128), primary_key=True),
    Column("owner_id", String(128), nullable=False, index=True),
    Column("kind", String(16), nullable=False), Column("payload", JSON, nullable=False),
)
busy = Table(
    "smcal_busy_intervals", metadata,
    Column("owner_id", String(128), primary_key=True), Column("id", String(128), primary_key=True),
    Column("start", UTCDateTime, nullable=False), Column("end", UTCDateTime, nullable=False),
)
private = Table(
    "smcal_private_events", metadata,
    Column("owner_id", String(128), primary_key=True), Column("id", String(128), primary_key=True),
    Column("start", UTCDateTime, nullable=False), Column("end", UTCDateTime, nullable=False),
    Column("private_title", String(500), nullable=False),
)
INPUTS = {"weekly-rules": WeeklyRuleInput, "exceptions": ExceptionInput, "exams": ExamInput}
MODELS = {"weekly-rules": WeeklyRule, "exceptions": ScheduleException, "exams": Exam}


class SQLiteScheduleRepository:
    def __init__(self, url: str):
        parsed = make_url(url)
        if parsed.get_backend_name() != "sqlite":
            raise ValueError("M1 repository supports SQLite only; Postgres is not verified")
        self.engine = create_engine(url, echo=False, hide_parameters=True,
                                    connect_args={"check_same_thread": False, "timeout": 10})
        # Explicit BEGIN also covers SELECTs and DDL on Python's legacy sqlite3 driver.
        # This makes read_bundle a consistent read transaction on Python 3.11+.
        @event.listens_for(self.engine, "connect")
        def configure_sqlite(dbapi_connection, connection_record):
            dbapi_connection.isolation_level = None

        @event.listens_for(self.engine, "begin")
        def explicit_begin(connection):
            connection.exec_driver_sql("BEGIN")

    def migrate(self):
        """Apply M1 schema v1 explicitly on startup/seed, only to the configured local DB."""
        database = self.engine.url.database
        if database and database != ":memory:":
            Path(database).resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.engine.begin() as conn:
            existing = (conn.execute(select(versions.c.version)).scalars().all()
                        if inspect(conn).has_table("smcal_schema_version") else [])
            if existing and existing != [1]:
                raise RuntimeError("unsupported calendar schema version")
            source = (Path(__file__).parent / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
            source = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("--"))
            for statement in source.split(";"):
                if statement.strip():
                    conn.exec_driver_sql(statement)
            if not existing:
                conn.execute(insert(versions).values(version=1))

    def close(self):
        self.engine.dispose()

    def _ensure_state(self, conn, owner_id):
        # SQLite INSERT OR IGNORE is scoped to the M1 repository.
        conn.execute(insert(states).values(owner_id=owner_id).prefix_with("OR IGNORE"))

    def _bump(self, conn, owner_id):
        self._ensure_state(conn, owner_id)
        conn.execute(update(states).where(states.c.owner_id == owner_id).values(
            schedule_version=states.c.schedule_version + 1))

    def _record(self, row):
        return MODELS[row["kind"]].model_validate({**row["payload"], "id": row["id"], "owner_id": row["owner_id"]})

    def list_records(self, owner_id, kind):
        with self.engine.connect() as conn:
            rows = conn.execute(select(records).where(records.c.owner_id == owner_id, records.c.kind == kind)
                                .order_by(records.c.id)).mappings().all()
        return [self._record(r) for r in rows]

    def create_record(self, owner_id, kind, data: BaseModel, record_id=None):
        data = INPUTS[kind].model_validate(data.model_dump())
        record_id = record_id or uuid4().hex
        with self.engine.begin() as conn:
            conn.execute(insert(records).values(id=record_id, owner_id=owner_id, kind=kind,
                                                payload=data.model_dump(mode="json")))
            self._bump(conn, owner_id)
        return MODELS[kind](**data.model_dump(), id=record_id, owner_id=owner_id)

    def patch_record(self, owner_id, kind, record_id, changes):
        with self.engine.begin() as conn:
            row = conn.execute(select(records).where(records.c.id == record_id,
                records.c.owner_id == owner_id, records.c.kind == kind)).mappings().first()
            if row is None:
                return None
            data = INPUTS[kind].model_validate({**row["payload"], **changes})
            conn.execute(update(records).where(records.c.id == record_id, records.c.owner_id == owner_id)
                         .values(payload=data.model_dump(mode="json")))
            self._bump(conn, owner_id)
        return MODELS[kind](**data.model_dump(), id=record_id, owner_id=owner_id)

    def delete_record(self, owner_id, kind, record_id):
        with self.engine.begin() as conn:
            changed = conn.execute(delete(records).where(records.c.id == record_id,
                records.c.owner_id == owner_id, records.c.kind == kind)).rowcount
            if changed:
                self._bump(conn, owner_id)
        return bool(changed)

    def read_bundle(self, owner_id):
        # A single read transaction gives the schedule and busy intervals one consistent view.
        # No query here references the private metadata table.
        with self.engine.begin() as conn:
            state = conn.execute(select(states).where(states.c.owner_id == owner_id)).mappings().first()
            all_records = conn.execute(select(records).where(records.c.owner_id == owner_id)
                                       .order_by(records.c.id)).mappings().all()
            blocks = conn.execute(select(busy.c.id, busy.c.start, busy.c.end)
                                  .where(busy.c.owner_id == owner_id).order_by(busy.c.id)).mappings().all()
        result = {kind: [] for kind in MODELS}
        for row in all_records:
            result[row["kind"]].append(self._record(row))
        result["state"] = dict(state) if state else {
            "schedule_version": 0, "snapshot_version": 0, "connection_status": "not_connected",
            "last_success_at": None, "coverage_start": None, "coverage_end": None}
        result["busy"] = [BusyBlock(**row) for row in blocks]
        return result

    def private_events(self, owner_id):
        with self.engine.connect() as conn:
            rows = conn.execute(select(private.c.id, private.c.start, private.c.end, private.c.private_title)
                                .where(private.c.owner_id == owner_id).order_by(private.c.id)).mappings().all()
        return [PrivateEvent(**r) for r in rows]

    def begin_refresh(self, owner_id):
        with self.engine.begin() as conn:
            self._ensure_state(conn, owner_id)
            return conn.execute(update(states).where(states.c.owner_id == owner_id).values(
                refresh_generation=states.c.refresh_generation + 1,
                connection_status="stale", schedule_version=states.c.schedule_version + 1)
                .returning(states.c.refresh_generation)).scalar_one()

    def _replace_snapshot(self, conn, owner_id, snapshot: ProviderSnapshot, now):
        if not snapshot.complete:
            raise ValueError("incomplete snapshot cannot replace stored intervals")
        conn.execute(delete(busy).where(busy.c.owner_id == owner_id))
        conn.execute(delete(private).where(private.c.owner_id == owner_id))
        if snapshot.busy:
            conn.execute(insert(busy), [{**b.model_dump(), "owner_id": owner_id} for b in snapshot.busy])
        if snapshot.private_events:
            conn.execute(insert(private), [{**b.model_dump(), "owner_id": owner_id} for b in snapshot.private_events])
        conn.execute(update(states).where(states.c.owner_id == owner_id).values(
            connection_status="synced", last_success_at=now,
            coverage_start=snapshot.coverage.start, coverage_end=snapshot.coverage.end,
            snapshot_version=states.c.snapshot_version + 1,
            schedule_version=states.c.schedule_version + 1))

    def finish_refresh(self, owner_id, ticket, snapshot, now):
        if not snapshot.complete:
            self.fail_refresh(owner_id, ticket)
            return False
        with self.engine.begin() as conn:
            # Acquire the SQLite write lock before testing generation, avoiding a read/write race.
            matched = conn.execute(update(states).where(states.c.owner_id == owner_id,
                states.c.refresh_generation == ticket).values(connection_status="stale")).rowcount
            if not matched:
                return False
            self._replace_snapshot(conn, owner_id, snapshot, now)
        return True

    def fail_refresh(self, owner_id, ticket):
        with self.engine.begin() as conn:
            conn.execute(update(states).where(states.c.owner_id == owner_id,
                states.c.refresh_generation == ticket).values(connection_status="stale"))

    def disconnect(self, owner_id):
        with self.engine.begin() as conn:
            self._ensure_state(conn, owner_id)
            conn.execute(delete(busy).where(busy.c.owner_id == owner_id))
            conn.execute(delete(private).where(private.c.owner_id == owner_id))
            conn.execute(update(states).where(states.c.owner_id == owner_id).values(
                connection_status="disconnected", last_success_at=None, coverage_start=None, coverage_end=None,
                schedule_version=states.c.schedule_version + 1,
                snapshot_version=states.c.snapshot_version + 1,
                refresh_generation=states.c.refresh_generation + 1))

    def seed_once(self, marker, owner_ids, seed_records, snapshots, now):
        """Atomic and non-destructive, including when seeded records were later edited/deleted."""
        with self.engine.begin() as conn:
            inserted = conn.execute(insert(markers).values(key=marker).prefix_with("OR IGNORE")).rowcount
            if not inserted:
                return False
            for owner_id in owner_ids:
                self._ensure_state(conn, owner_id)
            for kind, model in seed_records:
                payload = INPUTS[kind].model_validate(model.model_dump(exclude={"id", "owner_id"}))
                conn.execute(insert(records).values(id=model.id, owner_id=model.owner_id, kind=kind,
                                                    payload=payload.model_dump(mode="json")))
                self._bump(conn, model.owner_id)
            for owner_id, snapshot in snapshots.items():
                self._replace_snapshot(conn, owner_id, snapshot, now)
        return True
