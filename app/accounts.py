"""Demo accounts. Passwords stay here, never on a profile or an invite."""

import hashlib
import hmac
from uuid import uuid4

from app.store import next_id

TEST_EMAIL = "maya@example.edu"
TEST_PASSWORD = "studymate"

_accounts: dict[str, dict] = {}


def _hash(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${digest.hex()}"


def _matches(password: str, stored: str) -> bool:
    salt, _, _digest = stored.partition("$")
    return hmac.compare_digest(_hash(password, salt), stored)


def _public(account: dict) -> dict:
    return {"id": account["id"], "name": account["name"], "email": account["email"]}


def authenticate(email: str, password: str) -> dict | None:
    account = _accounts.get(email.strip().lower())
    if account is None or not _matches(password, account["password_hash"]):
        return None
    return _public(account)


def register(name: str, email: str, password: str) -> dict:
    clean_name = " ".join(name.split())
    clean_email = email.strip().lower()
    if len(clean_name) < 2:
        raise ValueError("Enter your name.")
    if "@" not in clean_email or "." not in clean_email.split("@")[-1]:
        raise ValueError("Enter a real email address.")
    if len(password) < 8:
        raise ValueError("Use at least 8 characters.")
    if clean_email in _accounts:
        raise ValueError("That email already has an account. Sign in instead.")
    account = {
        "id": "user_" + uuid4().hex,
        "name": clean_name,
        "email": clean_email,
        "password_hash": _hash(password, next_id("salt")),
    }
    _accounts[clean_email] = account
    return _public(account)


def seed() -> None:
    if TEST_EMAIL in _accounts:
        return
    _accounts[TEST_EMAIL] = {
        "id": "u_maya",
        "name": "Maya Chen",
        "email": TEST_EMAIL,
        "password_hash": _hash(TEST_PASSWORD, "maya"),
    }


seed()


class PersistentAccounts:
    """Persistent backend for the EXISTING login, used only by the private read host.

    This is not campus verification. Production hosts should inject team auth.
    No demo user/password is copied into this database.
    """
    def __init__(self, engine):
        from sqlalchemy import MetaData, Table, Column, String
        self.engine = engine
        metadata = MetaData()
        self.table = Table("studymate_accounts", metadata,
            Column("id", String, primary_key=True), Column("email", String, unique=True, nullable=False),
            Column("name", String, nullable=False), Column("password_hash", String, nullable=False),
            Column("timezone", String, nullable=False))
        metadata.create_all(engine)

    def get(self, subject_id):
        from sqlalchemy import select
        with self.engine.connect() as connection:
            row = connection.execute(select(self.table).where(self.table.c.id == subject_id)).mappings().first()
        return {k:row[k] for k in ("id", "name", "email", "timezone")} if row else None

    def authenticate(self, email, password):
        from sqlalchemy import select
        with self.engine.connect() as connection:
            row = connection.execute(select(self.table).where(self.table.c.email == email.strip().lower())).mappings().first()
        if not row or not _matches(password, row["password_hash"]):
            return None
        return self.get(row["id"])

    def register(self, name, email, password):
        from secrets import token_hex
        from uuid import uuid4
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError
        clean_name, clean_email = " ".join(name.split()), email.strip().lower()
        if not 2 <= len(clean_name) <= 100 or not 3 <= len(clean_email) <= 254 or "@" not in clean_email or "." not in clean_email.split("@")[-1]:
            raise ValueError("Enter a name and valid email address.")
        if not 12 <= len(password) <= 1024:
            raise ValueError("Use a password of at least 12 characters.")
        subject_id = "user_" + uuid4().hex
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.table).values(id=subject_id, email=clean_email, name=clean_name,
                    password_hash=_hash(password, token_hex(32)), timezone="America/Los_Angeles"))
        except IntegrityError:
            raise ValueError("That email already has an account. Sign in instead.") from None
        return self.get(subject_id)

    def set_timezone(self, subject_id, timezone):
        from zoneinfo import ZoneInfo
        from sqlalchemy import update
        ZoneInfo(timezone)
        with self.engine.begin() as connection:
            connection.execute(update(self.table).where(self.table.c.id == subject_id).values(timezone=timezone))
