"""Postgres-backed persistence; SQLite is an explicit local demo/test option."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Boolean, Integer, String, Text, create_engine, delete, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


class ScheduleRow(Base):
    __tablename__ = "study_schedules"
    owner_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSON)


class CalendarCredential(Base):
    __tablename__ = "study_calendar_credentials"
    owner_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)


class OAuthState(Base):
    __tablename__ = "study_calendar_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[int] = mapped_column(Integer)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    claimed: Mapped[bool] = mapped_column(Boolean, default=False)


class ConfirmedSession(Base):
    """Projection supplied by the trusted session/invitation service, never by the model."""
    __tablename__ = "study_confirmed_sessions"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, url: str):
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool
            elif url.startswith("sqlite:///"):
                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, **kwargs)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

    @contextmanager
    def transaction(self):
        try:
            with self.session_factory.begin() as db:
                yield db
        except IntegrityError as exc:
            raise ConflictError("A concurrent change must be reloaded") from exc

    def schedule(self, owner_id: str) -> tuple[dict, int]:
        with self.transaction() as db:
            row = db.get(ScheduleRow, owner_id)
            if row is None:
                raise KeyError("No schedule has been saved")
            return dict(row.payload), row.version

    def save_schedule(self, owner_id: str, payload: dict, expected_version: int) -> int:
        with self.transaction() as db:
            row = db.get(ScheduleRow, owner_id)
            if row is None:
                if expected_version != 0:
                    raise ConflictError("Schedule changed; reload before saving")
                db.add(ScheduleRow(owner_id=owner_id, version=1, payload=payload))
                return 1
            result = db.execute(update(ScheduleRow).where(
                ScheduleRow.owner_id == owner_id, ScheduleRow.version == expected_version,
            ).values(payload=payload, version=expected_version + 1))
            if result.rowcount != 1:
                raise ConflictError("Schedule changed; reload before saving")
            return expected_version + 1

    def delete_calendar(self, owner_id: str):
        """Disconnect removes OAuth grants locally and all imported private event titles."""
        with self.transaction() as db:
            db.execute(delete(OAuthState).where(OAuthState.owner_id == owner_id))
            db.execute(delete(CalendarCredential).where(CalendarCredential.owner_id == owner_id))
            row = db.get(ScheduleRow, owner_id)
            if row:
                payload = dict(row.payload)
                payload.update(calendar_busy=[], calendar_connected=False,
                               calendar_refreshed_at=None, calendar_window=None)
                result = db.execute(update(ScheduleRow).where(
                    ScheduleRow.owner_id == owner_id, ScheduleRow.version == row.version,
                ).values(payload=payload, version=row.version + 1))
                if result.rowcount != 1:
                    raise ConflictError("Schedule changed while disconnecting; retry")
