"""OAuth state is one-use and user-bound. Tokens are encrypted before database storage."""
from __future__ import annotations

import hashlib
import json
import secrets
import hmac
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy import delete, select, update

from .storage import CalendarCredential, OAuthState, ScheduleRow, Store, ConflictError

READ_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"


class CredentialVault:
    def __init__(self, store: Store, key: str, oauth_client, clock):
        self.store, self.cipher, self.oauth, self.clock = store, Fernet(key.encode()), oauth_client, clock

    def begin(self, owner_id: str, write: bool = False, *, browser_nonce: str) -> str:
        if not browser_nonce:
            raise ValueError("OAuth requires an originating browser flow")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        data = self.cipher.encrypt(json.dumps({"verifier": verifier, "write": write,
                    "browser_nonce_hash": hashlib.sha256(browser_nonce.encode()).hexdigest()}).encode()).decode()
        with self.store.transaction() as db:
            db.execute(delete(OAuthState).where(OAuthState.expires_at < int(self.clock().timestamp())))
            db.add(OAuthState(state_hash=hashlib.sha256(state.encode()).hexdigest(),
                              owner_id=owner_id, expires_at=int(self.clock().timestamp()) + 600,
                              encrypted_payload=data))
        return self.oauth.authorization_url(state, write=write, code_verifier=verifier)

    def finish(self, state: str, code: str, *, browser_nonce: str) -> str:
        digest = hashlib.sha256(state.encode()).hexdigest()
        with self.store.transaction() as db:
            row = db.execute(select(OAuthState).where(OAuthState.state_hash == digest)
                             .with_for_update()).scalar_one_or_none()
            if row is None or row.claimed or row.expires_at < int(self.clock().timestamp()):
                raise ValueError("OAuth state is invalid or expired; start connection again")
            owner_id = row.owner_id
            data = json.loads(self.cipher.decrypt(row.encrypted_payload.encode()))
            if not browser_nonce or not hmac.compare_digest(data["browser_nonce_hash"],
                    hashlib.sha256(browser_nonce.encode()).hexdigest()):
                raise ValueError("Complete OAuth in the same browser that started it")
            claimed = db.execute(update(OAuthState).where(OAuthState.state_hash == digest,
                                 OAuthState.claimed == False).values(claimed=True))
            if claimed.rowcount != 1:
                raise ValueError("OAuth state was already used")
        token = self.oauth.exchange_code(code, code_verifier=data["verifier"])
        # Disconnect deletes this in-flight state. Save checks it again after the
        # network call, in the same transaction that stores credentials.
        self.save(owner_id, token, write=data["write"], required_state_hash=digest)
        return owner_id

    def save(self, owner_id: str, token: dict, write: bool = False, *, is_refresh: bool = False,
             expected_ciphertext: str | None = None, required_state_hash: str | None = None):
        with self.store.transaction() as db:
            if required_state_hash:
                state = db.execute(select(OAuthState).where(OAuthState.state_hash == required_state_hash)
                                   .with_for_update()).scalar_one_or_none()
                if state is None or not state.claimed or state.owner_id != owner_id:
                    raise ValueError("Calendar connection was canceled")
            row = db.execute(select(CalendarCredential).where(CalendarCredential.owner_id == owner_id)
                             .with_for_update()).scalar_one_or_none()
            if is_refresh and (row is None or row.encrypted_payload != expected_ciphertext):
                raise ValueError("Calendar connection changed during refresh; reconnect")
            # A new authorization can choose a different Google account. Never attach
            # its access token to a previous account's refresh token.
            old = json.loads(self.cipher.decrypt(row.encrypted_payload.encode())) if row and is_refresh else {}
            if not token.get("access_token"):
                raise ValueError("Provider did not return an access token")
            result = {**old, **token}
            result["expires_at"] = int(self.clock().timestamp()) + int(token.get("expires_in", 3600))
            if "scope" not in token:
                result["scope"] = " ".join([READ_SCOPE, WRITE_SCOPE] if write else [READ_SCOPE])
            encrypted = self.cipher.encrypt(json.dumps(result).encode()).decode()
            if row:
                result_update = db.execute(update(CalendarCredential).where(
                    CalendarCredential.owner_id == owner_id,
                    CalendarCredential.encrypted_payload == row.encrypted_payload,
                ).values(encrypted_payload=encrypted))
                if result_update.rowcount != 1:
                    raise ValueError("Calendar connection changed during refresh")
            else:
                db.add(CalendarCredential(owner_id=owner_id, encrypted_payload=encrypted))
            if required_state_hash:
                deleted = db.execute(delete(OAuthState).where(OAuthState.state_hash == required_state_hash,
                                     OAuthState.claimed == True))
                if deleted.rowcount != 1:
                    raise ValueError("Calendar connection was canceled")
                schedule = db.get(ScheduleRow, owner_id)
                if schedule:
                    payload = dict(schedule.payload)
                    payload["calendar_connected"] = True
                    result_update = db.execute(update(ScheduleRow).where(
                        ScheduleRow.owner_id == owner_id, ScheduleRow.version == schedule.version,
                    ).values(payload=payload, version=schedule.version + 1))
                    if result_update.rowcount != 1:
                        raise ConflictError("Schedule changed during authorization")

    def token(self, owner_id: str, write: bool = False) -> str:
        with self.store.transaction() as db:
            row = db.get(CalendarCredential, owner_id)
            if row is None:
                raise ValueError("Connect Google Calendar first")
            ciphertext = row.encrypted_payload
            data = json.loads(self.cipher.decrypt(row.encrypted_payload.encode()))
        scope = WRITE_SCOPE if write else READ_SCOPE
        if scope not in data.get("scope", "").split():
            raise ValueError("Calendar write access needs a separate explicit OAuth grant" if write
                             else "Calendar read access has not been granted")
        if data["expires_at"] <= int(self.clock().timestamp()) + 60:
            if not data.get("refresh_token"):
                raise ValueError("Calendar connection expired; reconnect")
            refreshed = self.oauth.refresh_token(data["refresh_token"])
            if "scope" not in refreshed:
                refreshed["scope"] = data["scope"]
            self.save(owner_id, refreshed, write=write, is_refresh=True, expected_ciphertext=ciphertext)
            return refreshed["access_token"]
        return data["access_token"]
