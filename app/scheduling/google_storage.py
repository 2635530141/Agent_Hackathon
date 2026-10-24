"""Encrypted token vault and single-use OAuth state; no raw provider JSON stored."""
import hashlib
import json
from datetime import timedelta
from secrets import token_urlsafe
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Column, Integer, LargeBinary, MetaData, String, Table, delete, insert, select, update
from .core.storage import UTCDateTime


class GoogleStateError(ConnectionError):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class GoogleVault:
    def __init__(self, engine, encryption_key):
        self.engine = engine
        self.cipher = Fernet(encryption_key)
        meta = MetaData()
        self.tokens = Table("smcal_google_credentials", meta,
            Column("owner_id",String(128),primary_key=True), Column("generation",Integer,nullable=False),
            Column("version",Integer,nullable=False),Column("ciphertext",LargeBinary))
        self.states = Table("smcal_google_oauth_states",meta,
            Column("state_hash",String(64),primary_key=True),Column("owner_id",String(128),nullable=False),
            Column("session_hash",String(64),nullable=False),Column("expires_at",UTCDateTime,nullable=False),
            Column("generation",Integer,nullable=False),Column("used",Integer,nullable=False),
            Column("verifier",LargeBinary,nullable=False))
        self.metadata = meta

    def migrate(self):
        self.metadata.create_all(self.engine)

    def _ensure(self, conn, owner_id):
        conn.execute(insert(self.tokens).values(owner_id=owner_id,generation=0,version=0)
                     .prefix_with("OR IGNORE"))

    def generation(self, owner_id):
        with self.engine.connect() as conn:
            result = conn.execute(select(self.tokens.c.generation).where(self.tokens.c.owner_id==owner_id)).scalar_one_or_none()
        return result if result is not None else 0

    def load(self, owner_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(self.tokens).where(self.tokens.c.owner_id==owner_id)).mappings().first()
        if row is None or row["ciphertext"] is None:
            raise GoogleStateError("GOOGLE_NOT_CONNECTED")
        try:
            return json.loads(self.cipher.decrypt(row["ciphertext"])), row["generation"], row["version"]
        except (InvalidToken, ValueError):
            raise GoogleStateError("CREDENTIAL_STORAGE_ERROR") from None

    def save(self, owner_id, payload, generation, version=None, new_authorization=False, on_saved=None):
        # Keep only necessary OAuth fields, with no identity/email/raw JSON persistence.
        allowed = {k:payload[k] for k in ("access_token","refresh_token","token_type","expires_at","scope","account_subject") if k in payload}
        encrypted = self.cipher.encrypt(json.dumps(allowed).encode())
        with self.engine.begin() as conn:
            self._ensure(conn,owner_id)
            condition = [self.tokens.c.owner_id==owner_id,self.tokens.c.generation==generation]
            if version is not None:
                condition.append(self.tokens.c.version==version)
            matched = conn.execute(update(self.tokens).where(*condition).values(ciphertext=encrypted,
                generation=self.tokens.c.generation+int(new_authorization),version=self.tokens.c.version+1)).rowcount
            if not matched:
                raise GoogleStateError("CONNECTION_CHANGED")
            if on_saved:
                on_saved(conn)

    def issue(self, actor, now, context=None):
        state, verifier = token_urlsafe(32), token_urlsafe(48)
        with self.engine.begin() as conn:
            self._ensure(conn,actor.subject_id)
            generation = conn.execute(select(self.tokens.c.generation).where(self.tokens.c.owner_id==actor.subject_id)).scalar_one()
            conn.execute(insert(self.states).values(state_hash=digest(state),owner_id=actor.subject_id,
                session_hash=digest(actor.session_binding),expires_at=now+timedelta(minutes=10),generation=generation,
                used=0,verifier=self.cipher.encrypt(json.dumps({"verifier":verifier,"context":context}).encode())))
        return state, verifier

    def consume(self, actor, state, now):
        verifier, generation, _ = self.consume_details(actor,state,now)
        return verifier, generation

    def consume_details(self, actor, state, now):
        with self.engine.begin() as conn:
            row = conn.execute(update(self.states).where(self.states.c.state_hash==digest(state),
                self.states.c.owner_id==actor.subject_id,self.states.c.session_hash==digest(actor.session_binding),
                self.states.c.used==0,self.states.c.expires_at>now).values(used=1)
                .returning(self.states.c.verifier,self.states.c.generation)).first()
        if row is None:
            raise GoogleStateError("INVALID_OAUTH_STATE")
        try:
            raw = self.cipher.decrypt(row[0]).decode()
            payload = json.loads(raw) if raw.startswith("{") else {"verifier":raw,"context":None}
            return payload["verifier"], row[1], payload["context"]
        except (InvalidToken, ValueError, KeyError):
            raise GoogleStateError("INVALID_OAUTH_STATE") from None

    def disconnect(self, owner_id, on_disconnected=None):
        with self.engine.begin() as conn:
            self._ensure(conn,owner_id)
            conn.execute(update(self.tokens).where(self.tokens.c.owner_id==owner_id).values(ciphertext=None,
                generation=self.tokens.c.generation+1,version=self.tokens.c.version+1))
            conn.execute(delete(self.states).where(self.states.c.owner_id==owner_id))
            if on_disconnected:
                on_disconnected(conn)
