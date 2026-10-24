"""Demo accounts. Passwords stay here, never on a profile or an invite."""

import hashlib
import hmac

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
        "id": next_id("user"),
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
