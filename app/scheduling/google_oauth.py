"""Read-only Web OAuth via Authlib, bound to verified team session context."""
import logging
from datetime import datetime, timezone
from threading import Lock, RLock
import httpx
from authlib.integrations.httpx_client import OAuth2Client
from .google_config import AUTH_URL, TOKEN_URL, READ_SCOPE
from .google_storage import GoogleStateError


class _GoogleLogFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if any(host in message for host in ("googleapis.com","accounts.google.com","/calendar/google/callback")):
            record.msg, record.args = "Google Calendar HTTP request (details redacted)", ()
        return True


def protect_provider_logs():
    for name in ("httpx","httpx2","httpcore","uvicorn.access"):
        logger = logging.getLogger(name)
        if not any(isinstance(f,_GoogleLogFilter) for f in logger.filters):
            logger.addFilter(_GoogleLogFilter())
    # Authlib can emit token dictionaries at DEBUG. Never route them to shared logs.
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("authlib"):
            logging.getLogger(name).disabled = True


class GoogleOAuth:
    def __init__(self, config, vault, clock=None, transport=None):
        self.config, self.vault = config, vault
        self.clock = clock or (lambda:datetime.now(timezone.utc))
        self.transport = transport
        self._locks, self._guard = {}, Lock()
        protect_provider_logs()

    def owner_lock(self, owner_id):
        with self._guard:
            return self._locks.setdefault(owner_id,RLock())

    def client(self, **kwargs):
        return OAuth2Client(client_id=self.config.client_id,client_secret=self.config.client_secret,
            scope=READ_SCOPE,redirect_uri=self.config.redirect_uri,token_endpoint_auth_method="client_secret_post",
            code_challenge_method="S256",timeout=10,transport=self.transport,**kwargs)

    def begin(self, actor):
        state, verifier = self.vault.issue(actor,self.clock())
        with self.client() as client:
            url, _ = client.create_authorization_url(AUTH_URL,state=state,code_verifier=verifier,
                access_type="offline",prompt="consent")
        return url

    def _validate_token(self, token, previous=None):
        token = dict(token)
        if not token.get("access_token") or str(token.get("token_type","")).lower() != "bearer":
            raise GoogleStateError("INVALID_TOKEN_RESPONSE")
        token["scope"] = token.get("scope") or (previous or {}).get("scope") or READ_SCOPE
        if READ_SCOPE not in token["scope"].split():
            raise GoogleStateError("READ_SCOPE_REQUIRED")
        if previous and not token.get("refresh_token"):
            token["refresh_token"] = previous.get("refresh_token")
        if not token.get("refresh_token"):
            # No identity scope is requested, so a new grant cannot prove an old
            # refresh token belongs to this Google account. Require fresh consent.
            raise GoogleStateError("REFRESH_CONSENT_REQUIRED")
        if not token.get("expires_at"):
            raise GoogleStateError("TOKEN_EXPIRATION_REQUIRED")
        return token

    def complete(self, actor, code, state, after_save=None):
        verifier, generation = self.vault.consume(actor,state,self.clock())
        try:
            with self.client() as client:
                token = client.fetch_token(TOKEN_URL,code=code,grant_type="authorization_code",code_verifier=verifier)
            token = self._validate_token(token)
            self.vault.save(actor.subject_id,token,generation,new_authorization=True,on_saved=after_save)
        except GoogleStateError:
            raise
        except Exception:
            raise GoogleStateError("OAUTH_EXCHANGE_FAILED") from None

    def access_token(self, owner_id, force=False):
        with self.owner_lock(owner_id):
            old, generation, version = self.vault.load(owner_id)
            if not force and float(old.get("expires_at",0)) > self.clock().timestamp()+30:
                return old["access_token"]
            try:
                with self.client(token=old) as client:
                    response = client.refresh_token(TOKEN_URL,refresh_token=old["refresh_token"])
                refreshed = self._validate_token(response,previous=old)
                self.vault.save(owner_id,refreshed,generation,version=version)
            except GoogleStateError:
                raise
            except Exception:
                raise GoogleStateError("RECONNECT_REQUIRED") from None
            return refreshed["access_token"]
