"""Where user data lives: accounts, profiles, the course catalog, and rooms.

DATABASE_URL (in .env or the environment) points at the cloud Postgres, for
example Supabase. Without it, data goes to a local SQLite file so it still
survives restarts. Schedules use the same URL (see app.scheduling.runtime).

STUDYMATE_DB=memory keeps everything in memory instead (the tests set it).
"""

import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, create_engine, delete, func, select
from sqlalchemy.dialects import postgresql, sqlite

ROOT = Path(__file__).resolve().parent.parent
LOCAL_URL = f"sqlite+pysqlite:///{(ROOT / 'data' / 'studymate.db').as_posix()}"

metadata = MetaData()
# Same table and columns as the private calendar host's PersistentAccounts.
accounts = Table(
    "studymate_accounts", metadata,
    Column("id", String, primary_key=True),
    Column("email", String, unique=True, nullable=False),
    Column("name", String, nullable=False),
    Column("password_hash", String, nullable=False),
    Column("timezone", String, nullable=False),
)
profiles = Table(
    "sm_profiles", metadata,
    Column("user_id", String(128), primary_key=True),
    Column("data", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
)
courses = Table(
    "sm_courses", metadata,
    Column("id", String(64), primary_key=True),
    Column("code", String(64), nullable=False),
    Column("name", String(200), nullable=False),
)
rooms = Table(
    "sm_rooms", metadata,
    Column("id", String(128), primary_key=True),
    Column("data", JSON, nullable=False),
)
markers = Table("sm_seed_markers", metadata, Column("key", String(128), primary_key=True))


def _dotenv(name: str) -> str:
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return ""


def enabled() -> bool:
    return os.environ.get("STUDYMATE_DB") != "memory"


def cloud_url() -> str:
    """The configured cloud database URL, normalized for SQLAlchemy, or "" if none."""
    url = os.environ.get("DATABASE_URL") or _dotenv("DATABASE_URL")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def url() -> str:
    return cloud_url() or LOCAL_URL


def where() -> str:
    """A safe description for logs and pages: never the password."""
    configured = cloud_url()
    if not configured:
        return "local file data/studymate.db"
    host = configured.split("@", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return f"Postgres at {host}"


def make_engine(database_url: str):
    if database_url.startswith("sqlite"):
        database = database_url.split("///", 1)[-1]
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(database_url, connect_args={"check_same_thread": False})
    # No pre-ping: each one costs a network round trip. Recycling replaces idle connections instead.
    # Supabase's transaction pooler (port 6543) cannot reuse prepared statements.
    return create_engine(database_url, pool_recycle=300, connect_args={"prepare_threshold": None})


def lock_down(engine, tables) -> None:
    """Turn on Row Level Security so Supabase's public web API cannot read these tables.

    Supabase exposes the public schema to anyone holding the project's publishable key.
    With RLS on and no policies, that API sees nothing; this server connects as the
    database owner, which RLS does not restrict.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for table in tables:
            connection.exec_driver_sql(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY')


@lru_cache(maxsize=4)
def _engine_for(database_url: str):
    engine = make_engine(database_url)
    metadata.create_all(engine)
    lock_down(engine, metadata.sorted_tables)
    return engine


def engine():
    return _engine_for(url())


def reader():
    """A connection for reads only. No transaction, so one round trip per query instead of three."""
    return engine().connect().execution_options(isolation_level="AUTOCOMMIT")


def insert_ignore(table, **values):
    """INSERT that skips rows whose key already exists, on SQLite and Postgres."""
    dialect = postgresql if engine().dialect.name == "postgresql" else sqlite
    return dialect.insert(table).values(**values).on_conflict_do_nothing()


def upsert(table, key: str, **values):
    dialect = postgresql if engine().dialect.name == "postgresql" else sqlite
    statement = dialect.insert(table).values(**values)
    changed = {name: statement.excluded[name] for name in values if name != key}
    return statement.on_conflict_do_update(index_elements=[key], set_=changed)


def seed_once(marker: str) -> bool:
    """True the first time a seed marker is claimed in this database."""
    with engine().begin() as connection:
        # rowcount is -1 ("unknown") through some Postgres poolers, so ask for the row back.
        return connection.execute(insert_ignore(markers, key=marker).returning(markers.c.key)).first() is not None


# Profiles


def load_profile(user_id: str) -> dict | None:
    with reader() as connection:
        row = connection.execute(select(profiles.c.data).where(profiles.c.user_id == user_id)).first()
    return dict(row[0]) if row else None


def save_profile(user_id: str, data: dict) -> None:
    with engine().begin() as connection:
        connection.execute(upsert(profiles, "user_id", user_id=user_id, data=data))


def all_profiles() -> list[dict]:
    with reader() as connection:
        rows = connection.execute(select(profiles.c.data).order_by(profiles.c.user_id)).all()
    return [dict(row[0]) for row in rows]


def seed_profiles(seeded: list[dict]) -> None:
    with engine().begin() as connection:
        for profile in seeded:
            connection.execute(insert_ignore(profiles, user_id=profile["id"], data=profile))


# Courses


def load_courses() -> list[dict]:
    with reader() as connection:
        rows = connection.execute(select(courses).order_by(courses.c.id)).mappings().all()
    return [dict(row) for row in rows]


def save_course(course: dict) -> None:
    with engine().begin() as connection:
        connection.execute(insert_ignore(courses, id=course["id"], code=course["code"], name=course["name"]))


# Rooms


def load_rooms() -> list[dict]:
    with reader() as connection:
        rows = connection.execute(select(rooms.c.data).order_by(rooms.c.id)).all()
    return [dict(row[0]) for row in rows]


def save_rooms(current: list[dict], loaded_ids: set[str]) -> None:
    """Write every room, and remove rooms that were loaded but are gone now."""
    with engine().begin() as connection:
        for room in current:
            connection.execute(upsert(rooms, "id", id=room["id"], data=room))
        removed = loaded_ids - {room["id"] for room in current}
        if removed:
            connection.execute(delete(rooms).where(rooms.c.id.in_(removed)))
