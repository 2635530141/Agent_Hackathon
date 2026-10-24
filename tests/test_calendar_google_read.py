"""M2 HTTP/OAuth simulations only. No real credentials or Google network calls."""
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlsplit
import json
import logging
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import store
from app.main import app as team_app
from app.routers.calendar_google import router
from app.scheduling.core.schemas import Actor, BusyBlock, ExamInput, Horizon, PrivateEvent, ProviderSnapshot, TrustedMatchContext
from app.scheduling.core.service import ScheduleService
from app.scheduling.google_config import GoogleActor, GoogleConfig, READ_SCOPE, TOKEN_URL
from app.scheduling.google_integration import GoogleCalendarIntegration
from app.scheduling.google_oauth import GoogleOAuth
from app.scheduling.google_provider import GoogleCalendarProvider, google_window, normalize_event
from app.scheduling.google_storage import GoogleStateError, GoogleVault
from app.scheduling.runtime import get_runtime

NOW = datetime(2026,10,24,19,tzinfo=timezone.utc)
ZONE = "America/Los_Angeles"


class NoReservations:
    def confirmed_reservations(self, owner, horizon):
        return []


@pytest.fixture
def setup_google():
    repo = get_runtime().repository
    vault = GoogleVault(repo.engine,Fernet.generate_key())
    vault.migrate()
    actor = GoogleActor("auth-test-owner",ZONE,token_urlsafe(24),token_urlsafe(24))
    config = GoogleConfig("synthetic-client",token_urlsafe(24))
    return repo,vault,actor,config


def token_payload(**changes):
    return dict(access_token=token_urlsafe(24),refresh_token=token_urlsafe(24),token_type="Bearer",
                expires_at=NOW.timestamp()+3600,scope=READ_SCOPE) | changes


def event(event_id="event", **changes):
    return {"id":event_id,"summary":"SYNTHETIC_PRIVATE_TITLE", "status":"confirmed",
        "start":{"dateTime":"2026-10-26T14:00:00-07:00"},
        "end":{"dateTime":"2026-10-26T15:00:00-07:00"},**changes}


def state_from(url):
    return parse_qs(urlsplit(url).query)["state"][0]


def test_oauth_read_scope_pkce_session_binding_and_encryption(setup_google,caplog):
    repo,vault,actor,config = setup_google
    payload = token_payload()
    calls = []
    def exchange(request):
        calls.append(request)
        assert request.method=="POST" and str(request.url)==TOKEN_URL
        fields = parse_qs(request.content.decode())
        assert fields["grant_type"]==["authorization_code"]
        assert len(fields["code_verifier"][0])>=43
        return httpx.Response(200,json=payload)
    oauth = GoogleOAuth(config,vault,clock=lambda:NOW,transport=httpx.MockTransport(exchange))
    url = oauth.begin(actor)
    query = parse_qs(urlsplit(url).query)
    assert query["scope"]==[READ_SCOPE] and query["code_challenge_method"]==["S256"]
    assert query["redirect_uri"]==[config.redirect_uri] and query["access_type"]==["offline"]
    state = state_from(url)
    oauth.complete(actor,"synthetic-authorization-code",state)
    assert len(calls)==1
    stored,_,_ = vault.load(actor.subject_id)
    assert bool(stored["refresh_token"]==payload["refresh_token"])
    with repo.engine.connect() as conn:
        ciphertext = conn.execute(vault.tokens.select()).mappings().first()["ciphertext"]
        assert bool(payload["access_token"].encode() not in ciphertext)
        assert bool(state.encode() not in ciphertext)
    assert all(bool(secret not in caplog.text) for secret in (payload["access_token"],payload["refresh_token"],state,config.client_secret))
    with pytest.raises(GoogleStateError,match="INVALID_OAUTH_STATE"):
        oauth.complete(actor,"synthetic-authorization-code",state)


@pytest.mark.parametrize("wrong", ["owner","session","expired","unknown"])
def test_oauth_state_rejected_before_exchange(setup_google,wrong):
    repo,vault,actor,config = setup_google
    calls = []
    oauth = GoogleOAuth(config,vault,clock=lambda:NOW,transport=httpx.MockTransport(lambda req:calls.append(req)))
    state = state_from(oauth.begin(actor))
    if wrong=="owner":
        actor = GoogleActor("another-owner",ZONE,actor.session_binding,actor.csrf_token)
    elif wrong=="session":
        actor = GoogleActor(actor.subject_id,ZONE,token_urlsafe(24),actor.csrf_token)
    elif wrong=="expired":
        oauth.clock = lambda:NOW+timedelta(minutes=11)
    else:
        state = token_urlsafe(32)
    with pytest.raises(GoogleStateError,match="INVALID_OAUTH_STATE"):
        oauth.complete(actor,"synthetic-code",state)
    assert calls==[]


def test_refresh_preserves_omitted_refresh_token_and_rejects_new_account_carryover(setup_google):
    repo,vault,actor,config = setup_google
    old = token_payload(expires_at=NOW.timestamp()-10)
    vault.save(actor.subject_id,old,0)
    new = token_payload()
    new.pop("refresh_token")
    oauth = GoogleOAuth(config,vault,clock=lambda:NOW,transport=httpx.MockTransport(lambda req:httpx.Response(200,json=new)))
    assert bool(oauth.access_token(actor.subject_id)==new["access_token"])
    assert bool(vault.load(actor.subject_id)[0]["refresh_token"]==old["refresh_token"])
    state = state_from(oauth.begin(actor))
    with pytest.raises(GoogleStateError,match="REFRESH_CONSENT_REQUIRED"):
        oauth.complete(actor,"synthetic-new-account-code",state)
    assert bool(vault.load(actor.subject_id)[0]["refresh_token"]==old["refresh_token"])


def test_disconnect_during_refresh_cannot_restore_credentials(setup_google):
    repo,vault,actor,config = setup_google
    vault.save(actor.subject_id,token_payload(expires_at=NOW.timestamp()-10),0)
    def exchange(request):
        vault.disconnect(actor.subject_id)
        return httpx.Response(200,json=token_payload())
    oauth = GoogleOAuth(config,vault,clock=lambda:NOW,transport=httpx.MockTransport(exchange))
    with pytest.raises(GoogleStateError,match="CONNECTION_CHANGED"):
        oauth.access_token(actor.subject_id)
    with pytest.raises(GoogleStateError,match="GOOGLE_NOT_CONNECTED"):
        vault.load(actor.subject_id)


def test_disconnect_invalidates_pending_authorization(setup_google):
    repo,vault,actor,config = setup_google
    oauth = GoogleOAuth(config,vault,clock=lambda:NOW)
    state = state_from(oauth.begin(actor))
    vault.disconnect(actor.subject_id)
    with pytest.raises(GoogleStateError,match="INVALID_OAUTH_STATE"):
        oauth.complete(actor,"synthetic-code",state)


def test_google_window_uses_14_local_days_across_dst():
    window = google_window(NOW,ZONE)
    assert window.end-window.start==timedelta(hours=337)
    assert window.start.isoformat()=="2026-10-24T07:00:00+00:00"
    assert window.end.isoformat()=="2026-11-07T08:00:00+00:00"


@pytest.mark.parametrize("changes", [{"status":"cancelled"},{"transparency":"transparent"},
    {"eventType":"workingLocation"},{"attendees":[{"self":True,"responseStatus":"declined"}]}])
def test_nonblocking_event_types(changes):
    assert normalize_event("owner",event(**changes),ZONE) is None


@pytest.mark.parametrize("response", ["accepted","tentative","needsAction"])
def test_opaque_event_blocks_conservatively(response):
    assert normalize_event("owner",event(attendees=[{"self":True,"responseStatus":response}]),ZONE) is not None


def test_all_day_uses_calendar_zone_and_exclusive_end():
    result = normalize_event("owner",event(start={"date":"2026-10-26"},end={"date":"2026-10-27"}),"America/New_York")
    assert result.start.isoformat()=="2026-10-26T04:00:00+00:00"
    assert result.end.isoformat()=="2026-10-27T04:00:00+00:00"


def test_paginated_primary_read_and_recurring_instances():
    calls = []
    page = token_urlsafe(12)
    def reply(request):
        calls.append(request)
        assert request.url.path=="/calendar/v3/calendars/primary/events"
        assert request.url.params["singleEvents"]=="true"
        assert all(k not in request.url.params["fields"] for k in ("email","description","organizer"))
        if "pageToken" not in request.url.params:
            return httpx.Response(200,json={"timeZone":ZONE,"nextPageToken":page,"items":[event("instance-1")]})
        assert bool(request.url.params["pageToken"]==page)
        return httpx.Response(200,json={"timeZone":ZONE,"items":[event("instance-2",start={"dateTime":"2026-11-01T15:00-08:00"},end={"dateTime":"2026-11-01T16:00-08:00"}),event("free",transparency="transparent")]})
    provider = GoogleCalendarProvider(lambda owner:token_urlsafe(24),lambda owner:ZONE,clock=lambda:NOW,
        transport=httpx.MockTransport(reply),pause=lambda _:None)
    result = provider.read_snapshot("owner",google_window(NOW,ZONE))
    assert len(calls)==2 and len(result.busy)==2 and result.provider_kind=="google"
    assert result.busy[1].start.hour==23
    assert "SYNTHETIC_PRIVATE_TITLE" not in repr(result)
    assert not hasattr(provider,"create_event") and not hasattr(provider,"delete_event")


def test_page_two_failure_atomic_and_successful_retry():
    repo = get_runtime().repository
    span = google_window(NOW,ZONE)
    original = ProviderSnapshot(coverage=span,busy=[BusyBlock(id="old",start="2026-10-26T21:00Z",end="2026-10-26T22:00Z")])
    ticket = repo.begin_refresh(store.OWNER_ID)
    repo.finish_refresh(store.OWNER_ID,ticket,original,NOW)
    fail = [True]
    attempts = []
    def reply(request):
        attempts.append(request.url.params.get("pageToken"))
        if "pageToken" not in request.url.params:
            return httpx.Response(200,json={"timeZone":ZONE,"items":[event()],"nextPageToken":"synthetic-page"})
        if fail[0]:
            return httpx.Response(503,json={"error":"synthetic timeout"})
        return httpx.Response(200,json={"timeZone":ZONE,"items":[]})
    provider = GoogleCalendarProvider(lambda owner:token_urlsafe(24),lambda owner:ZONE,clock=lambda:NOW,
        transport=httpx.MockTransport(reply),pause=lambda _:None)
    service = ScheduleService(repo,provider,NoReservations(),clock=lambda:NOW)
    status = service.refresh_calendar(Actor(subject_id=store.OWNER_ID),span)
    assert status.verification_level=="unknown"
    assert [b.id for b in repo.read_bundle(store.OWNER_ID)["busy"]]==["old"]
    assert attempts.count("synthetic-page")==3
    fail[0]=False
    status = service.refresh_calendar(Actor(subject_id=store.OWNER_ID),span)
    assert status.verification_level=="google_current" and status.provider_kind=="google"
    assert [b.id for b in repo.read_bundle(store.OWNER_ID)["busy"]]!=["old"]
    # Matching is saved-data only and cannot select private titles.
    repo.private_events = lambda *args: (_ for _ in ()).throw(AssertionError("private read"))
    safe = service.effective_availability(Actor(subject_id=store.OWNER_ID),store.OWNER_ID,span)
    assert all(k not in safe.model_dump_json() for k in ("private_title","SYNTHETIC_PRIVATE_TITLE","token"))


@pytest.mark.parametrize("status", [429,500,401])
def test_transient_or_auth_retry_is_bounded(status):
    count, forced = [], []
    def source(owner,force=False):
        forced.append(force)
        return token_urlsafe(24)
    def reply(request):
        count.append(1)
        return httpx.Response(status if len(count)==1 else 200,json={"timeZone":ZONE,"items":[]})
    provider = GoogleCalendarProvider(source,lambda owner:ZONE,clock=lambda:NOW,transport=httpx.MockTransport(reply),pause=lambda _:None)
    assert provider.read_snapshot("owner",google_window(NOW,ZONE)).busy==[]
    assert len(count)==2
    assert forced==([False,True] if status==401 else [False])


def test_out_of_coverage_makes_no_network_or_token_call():
    calls = []
    provider = GoogleCalendarProvider(lambda owner:calls.append(owner),lambda owner:ZONE,clock=lambda:NOW)
    span = google_window(NOW,ZONE)
    with pytest.raises(GoogleStateError,match="OUTSIDE_GOOGLE"):
        provider.read_snapshot("owner",Horizon(start=span.start,end=span.end+timedelta(days=1)))
    assert calls==[]


def test_malformed_page_and_repeated_pagination_fail_closed():
    for body in ({"items":[]},{"timeZone":ZONE,"items":[event(end={"dateTime":"2026-10-26T13:00-07:00"})]},
                 {"timeZone":ZONE,"items":[],"nextPageToken":"same"}):
        provider = GoogleCalendarProvider(lambda owner:token_urlsafe(24),lambda owner:ZONE,clock=lambda:NOW,
            transport=httpx.MockTransport(lambda req:httpx.Response(200,json=body)),pause=lambda _:None)
        with pytest.raises(GoogleStateError):
            provider.read_snapshot("owner",google_window(NOW,ZONE))


def test_default_host_rejects_fake_identity_and_forged_headers():
    with TestClient(team_app) as client:
        for path in ("connect","callback?code=synthetic&state=synthetic"):
            response = client.get("/calendar/google/"+path,headers={"X-User-ID":store.OWNER_ID,"Authorization":"Bearer synthetic"})
            assert response.status_code==409
        assert client.post("/calendar/google/refresh",json={"trusted":True,"owner_id":store.OWNER_ID}).status_code==409


def test_authenticated_mock_http_flow_csrf_callback_disconnect_and_redacted_logs(setup_google,caplog):
    repo,vault,actor,config = setup_google
    caplog.set_level(logging.DEBUG)
    tokens = token_payload()
    def reply(request):
        if str(request.url)==TOKEN_URL:
            return httpx.Response(200,json=tokens)
        return httpx.Response(200,json={"timeZone":ZONE,"items":[event()]})
    transport = httpx.MockTransport(reply)
    oauth = GoogleOAuth(config,vault,clock=lambda:NOW,transport=transport)
    provider = GoogleCalendarProvider(oauth.access_token,lambda owner:ZONE,clock=lambda:NOW,
        generation_for_owner=vault.generation,transport=transport,pause=lambda _:None)
    active = GoogleCalendarIntegration(repo,vault,oauth,provider,NoReservations())
    app = FastAPI()
    app.state.google_calendar_integration = active
    @app.middleware("http")
    async def test_auth(request,call_next):
        request.state.calendar_actor = actor  # explicit server-auth test double
        return await call_next(request)
    app.include_router(router)
    repo.create_record(actor.subject_id,"exams",ExamInput(
        course_id="test-course",start="2026-10-26T23:00Z",end="2026-10-27T00:00Z"))
    with TestClient(app,base_url="http://127.0.0.1:8011") as client:
        begin = client.get("/calendar/google/connect",follow_redirects=False)
        assert begin.status_code==303
        state = state_from(begin.headers["location"])
        code = token_urlsafe(24)
        callback = client.get("/calendar/google/callback",params={"state":state,"code":code},follow_redirects=False)
        assert callback.status_code==303
        assert repo.read_bundle(actor.subject_id)["state"]["connection_status"]=="stale"
        assert client.post("/calendar/google/refresh").status_code==403
        assert client.post("/calendar/google/refresh",headers={"X-CSRF-Token":actor.csrf_token,"Origin":"https://unexpected.invalid"}).status_code==403
        refreshed = client.post("/calendar/google/refresh",headers={"X-CSRF-Token":actor.csrf_token})
        assert refreshed.status_code==200 and refreshed.json()["verification_level"]=="google_current"
        assert len(repo.private_events(actor.subject_id))==1
        assert client.get("/calendar/google/callback",params={"state":state,"code":code}).status_code==400
        removed = client.post("/calendar/google/disconnect",headers={"X-CSRF-Token":actor.csrf_token})
        assert removed.status_code==200 and repo.private_events(actor.subject_id)==[]
        assert len(repo.list_records(actor.subject_id,"exams"))==1
        with pytest.raises(GoogleStateError):
            vault.load(actor.subject_id)
    assert all(bool(value not in caplog.text) for value in (tokens["access_token"],tokens["refresh_token"],config.client_secret,state,code,"SYNTHETIC_PRIVATE_TITLE"))


def test_new_consent_clears_previous_account_cache_atomically(setup_google):
    repo,vault,actor,config = setup_google
    span = google_window(NOW,ZONE)
    ticket = repo.begin_refresh(actor.subject_id)
    repo.finish_refresh(actor.subject_id,ticket,ProviderSnapshot(coverage=span,
        busy=[BusyBlock(id="old-event",start="2026-10-26T21:00Z",end="2026-10-26T22:00Z")],
        private_events=[PrivateEvent(id="old-event",start="2026-10-26T21:00Z",end="2026-10-26T22:00Z",private_title="SYNTHETIC_OLD_ACCOUNT")]),NOW)
    oauth = GoogleOAuth(config,vault,clock=lambda:NOW,transport=httpx.MockTransport(lambda req:httpx.Response(200,json=token_payload())))
    active = GoogleCalendarIntegration(repo,vault,oauth,None,NoReservations())
    active.complete(actor,"synthetic-code",state_from(oauth.begin(actor)))
    bundle = repo.read_bundle(actor.subject_id)
    assert bundle["busy"]==[] and repo.private_events(actor.subject_id)==[]
    assert bundle["state"]["provider_kind"]=="google" and bundle["state"]["connection_status"]=="stale"


def test_old_snapshot_ticket_cannot_overwrite_new_read(setup_google):
    repo,vault,actor,config = setup_google
    span = google_window(NOW,ZONE)
    older,newer = repo.begin_refresh(actor.subject_id),repo.begin_refresh(actor.subject_id)
    latest = ProviderSnapshot(provider_kind="google",coverage=span,busy=[])
    assert repo.finish_refresh(actor.subject_id,newer,latest,NOW)
    assert not repo.finish_refresh(actor.subject_id,older,ProviderSnapshot(coverage=span,busy=[BusyBlock(id="old",start="2026-10-26T21:00Z",end="2026-10-26T22:00Z")]),NOW)
    assert repo.read_bundle(actor.subject_id)["busy"]==[]


def test_credential_change_during_calendar_read_fails_closed():
    generation = [0]
    def reply(request):
        generation[0]+=1
        return httpx.Response(200,json={"timeZone":ZONE,"items":[event()]})
    provider = GoogleCalendarProvider(lambda owner:token_urlsafe(24),lambda owner:ZONE,clock=lambda:NOW,
        transport=httpx.MockTransport(reply),generation_for_owner=lambda owner:generation[0])
    with pytest.raises(GoogleStateError,match="CONNECTION_CHANGED"):
        provider.read_snapshot("owner",google_window(NOW,ZONE))


def test_live_factory_rejects_synthetic_repository_before_reading_files():
    from app.scheduling.google_setup import build_google_integration
    with pytest.raises(ValueError,match="isolated authenticated"):
        build_google_integration(get_runtime().repository,NoReservations(),lambda owner:ZONE,
            client_file="not-a-real-config.json",encryption_key_file="not-a-real-key")


def test_refresh_coordinator_is_trusted_only_and_manual_mode_distinct(setup_google):
    repo,vault,actor,config = setup_google
    oauth = GoogleOAuth(config,vault,clock=lambda:NOW)
    active = GoogleCalendarIntegration(repo,vault,oauth,None,NoReservations())
    from app.scheduling.core.service import AuthorizationError
    with pytest.raises(AuthorizationError):
        active.refresh_calendars({"trusted":True},[actor.subject_id])
    with pytest.raises(AuthorizationError):
        active.refresh_calendars(TrustedMatchContext(eligible_subject_ids=frozenset()),[actor.subject_id])
    assert active.refresh_calendars(TrustedMatchContext(eligible_subject_ids=frozenset({actor.subject_id})),[actor.subject_id])=={actor.subject_id:"manual_only"}


def test_schema_v1_upgrade_preserves_existing_busy_and_utc(tmp_path):
    import sqlite3
    from pathlib import Path
    from app.scheduling.core.storage import SQLiteScheduleRepository
    database = tmp_path/"upgrade.db"
    connection = sqlite3.connect(database)
    source = (Path(__file__).parents[1]/"app/scheduling/core/migrations/001_initial.sql").read_text(encoding="utf-8")
    connection.executescript(source)
    connection.execute("INSERT INTO smcal_schema_version VALUES (1)")
    connection.execute("INSERT INTO smcal_schedule_state(owner_id,schedule_version,snapshot_version,refresh_generation,connection_status) VALUES (?,?,?,?,?)",("owner",7,1,0,"synced"))
    connection.execute("INSERT INTO smcal_busy_intervals VALUES (?,?,?,?)",("owner","kept","2026-10-26 21:00:00.000000","2026-10-26 22:00:00.000000"))
    connection.commit()
    connection.close()
    repository = SQLiteScheduleRepository(f"sqlite+pysqlite:///{database.as_posix()}")
    try:
        repository.migrate()
        repository.migrate()  # idempotent upgrade
        bundle = repository.read_bundle("owner")
        assert bundle["state"]["schedule_version"]==7 and bundle["state"]["provider_kind"]=="fake"
        assert bundle["busy"][0].start.utcoffset()==timedelta(0)
    finally:
        repository.close()
