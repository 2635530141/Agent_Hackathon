from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from schedule_service.oauth import CredentialVault, READ_SCOPE, WRITE_SCOPE
from schedule_service.storage import CalendarCredential, Store


class FakeOAuth:
    def __init__(self):
        self.state = None
        self.verifier = None
        self.write = False
        self.exchanges = 0

    def authorization_url(self, state, write=False, code_verifier=None):
        self.state, self.verifier, self.write = state, code_verifier, write
        return "https://accounts.google.com/o/oauth2/v2/auth?state=" + state

    def exchange_code(self, code, code_verifier=None):
        assert code_verifier == self.verifier
        self.exchanges += 1
        return {"access_token": "access-secret", "refresh_token": "refresh-secret", "expires_in": 3600,
                "scope": READ_SCOPE + (" " + WRITE_SCOPE if self.write else "")}

    def refresh_token(self, value):
        assert value == "refresh-secret"
        return {"access_token": "new-secret", "expires_in": 3600}


def vault():
    store, oauth = Store("sqlite://"), FakeOAuth()
    current = [datetime(2026, 10, 24, tzinfo=timezone.utc)]
    value = CredentialVault(store, Fernet.generate_key().decode(), oauth, lambda: current[0])
    return value, store, oauth, current


def test_state_bound_to_user_consumed_once_and_credentials_encrypted():
    value, store, oauth, _ = vault()
    value.begin("alice", browser_nonce="same-browser")
    assert value.finish(oauth.state, "fake-code", browser_nonce="same-browser") == "alice"
    with store.transaction() as db:
        ciphertext = db.get(CalendarCredential, "alice").encrypted_payload
    assert "access-secret" not in ciphertext and "refresh-secret" not in ciphertext
    assert value.token("alice") == "access-secret"
    with pytest.raises(ValueError):
        value.finish(oauth.state, "replayed", browser_nonce="same-browser")
    assert oauth.exchanges == 1


def test_expired_and_forged_state_do_not_exchange_codes():
    value, _, oauth, current = vault()
    value.begin("alice", browser_nonce="same-browser")
    current[0] += timedelta(minutes=11)
    for state in (oauth.state, "forged"):
        with pytest.raises(ValueError):
            value.finish(state, "code", browser_nonce="same-browser")
    assert oauth.exchanges == 0


def test_readonly_token_cannot_be_used_for_write():
    value, _, oauth, _ = vault()
    value.begin("alice", browser_nonce="same-browser")
    value.finish(oauth.state, "code", browser_nonce="same-browser")
    with pytest.raises(ValueError, match="separate explicit"):
        value.token("alice", write=True)


def test_incremental_write_and_refresh_preserve_permissions_and_refresh_token():
    value, store, oauth, current = vault()
    value.begin("alice", write=True, browser_nonce="same-browser")
    value.finish(oauth.state, "code", browser_nonce="same-browser")
    assert value.token("alice", write=True) == "access-secret"
    current[0] += timedelta(hours=2)
    assert value.token("alice", write=True) == "new-secret"
    current[0] += timedelta(hours=2)
    assert value.token("alice") == "new-secret"
    store.delete_calendar("alice")
    with pytest.raises(ValueError, match="Connect"):
        value.token("alice")


def test_forwarded_authorization_url_cannot_attach_another_browsers_calendar():
    value, _, oauth, _ = vault()
    value.begin("alice", browser_nonce="originating-browser")
    for nonce in ("", "different-browser"):
        with pytest.raises(ValueError, match="same browser"):
            value.finish(oauth.state, "code", browser_nonce=nonce)
    assert oauth.exchanges == 0
    assert value.finish(oauth.state, "code", browser_nonce="originating-browser") == "alice"


def test_new_google_authorization_does_not_reuse_another_accounts_refresh_token():
    value, _, _, current = vault()
    value.save("alice", {"access_token": "account-A", "refresh_token": "refresh-A", "expires_in": 3600})
    value.save("alice", {"access_token": "account-B", "expires_in": 3600})
    assert value.token("alice") == "account-B"
    current[0] += timedelta(hours=2)
    with pytest.raises(ValueError, match="reconnect"):
        value.token("alice")


def test_disconnect_during_code_exchange_cannot_recreate_credentials():
    value, store, oauth, _ = vault()
    value.begin("alice", browser_nonce="same-browser")
    original = oauth.exchange_code
    def disconnect_during_exchange(code, code_verifier=None):
        store.delete_calendar("alice")
        return original(code, code_verifier=code_verifier)
    oauth.exchange_code = disconnect_during_exchange
    with pytest.raises(ValueError, match="canceled"):
        value.finish(oauth.state, "code", browser_nonce="same-browser")
    with store.transaction() as db:
        assert db.get(CalendarCredential, "alice") is None


def test_disconnect_during_refresh_cannot_recreate_credentials():
    value, store, oauth, current = vault()
    value.save("alice", {"access_token": "old", "refresh_token": "refresh-secret", "expires_in": 1})
    original = oauth.refresh_token
    def disconnect_during_refresh(token):
        store.delete_calendar("alice")
        return original(token)
    oauth.refresh_token = disconnect_during_refresh
    current[0] += timedelta(minutes=2)
    with pytest.raises(ValueError, match="changed"):
        value.token("alice")
    with store.transaction() as db:
        assert db.get(CalendarCredential, "alice") is None
