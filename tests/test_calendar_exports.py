"""M3 uses generated credentials, a server Session double and HTTP MockTransport only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from threading import RLock
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from app.routers.calendar_exports import router
from app.scheduling.core.schemas import Reservation, WeeklyRuleInput, ExamInput, ExceptionInput
from app.scheduling.core.storage import SQLiteScheduleRepository
from app.scheduling.export_models import ExportError, ExportSession
from app.scheduling.export_storage import ExportRepository
from app.scheduling.export_service import CalendarExportService
from app.scheduling.google_config import GoogleActor, GoogleConfig, READ_SCOPE, WRITE_SCOPE, TOKEN_URL
from app.scheduling.google_storage import GoogleVault, GoogleStateError
from app.scheduling.google_oauth import GoogleOAuth
from app.scheduling.google_provider import GoogleCalendarProvider, google_window
from app.scheduling.google_writer import GoogleEventWriter
from app.scheduling.google_integration import GoogleCalendarIntegration
from app.scheduling.google_identity import GoogleIdentityVerifier

NOW = datetime(2026,10,24,16,tzinfo=timezone.utc)
ZONE = "America/Los_Angeles"


class SessionDouble:
    authoritative = True  # Explicit simulated team server authority, never the demo store.
    def __init__(self, snapshot):
        self.snapshot, self.lock = snapshot,RLock()
    def read_session(self, session_id):
        return self.snapshot if session_id==self.snapshot.session_id else None
    def guard(self, session_id):
        return self.lock
    def confirmed_reservations(self, owner_id, span):
        s=self.snapshot
        return [Reservation(session_id=s.session_id,start=s.start,end=s.end)] if owner_id in s.participant_ids else []


class GoogleDouble:
    def __init__(self):
        self.events,self.posts,self.deletes = {},0,0
        self.drop_insert,self.drop_delete,self.fail_read = False,False,False
        self.lock=RLock()
    def reply(self, request):
        import json
        with self.lock:
            path=request.url.path
            if path.endswith("/events") and request.method=="GET":
                if self.fail_read: return httpx.Response(503)
                return httpx.Response(200,json={"timeZone":ZONE,"items":list(self.events.values())})
            if path.endswith("/events") and request.method=="POST":
                body=json.loads(request.content)
                assert "attendees" not in body and "conferenceData" not in body
                assert request.url.params["sendUpdates"]=="none"
                if body["id"] in self.events: return httpx.Response(409)
                self.posts+=1
                item={**body,"htmlLink":"https://www.google.com/calendar/event?eid=synthetic","etag":'"test-v1"'}
                self.events[body["id"]]=item
                if self.drop_insert:
                    self.drop_insert=False
                    raise httpx.ReadTimeout("Synthetic dropped response",request=request)
                return httpx.Response(201,json=item)
            event_id=path.rsplit("/",1)[-1]
            if request.method=="GET":
                return httpx.Response(200,json=self.events[event_id]) if event_id in self.events else httpx.Response(404)
            if request.method=="DELETE":
                assert request.headers["If-Match"]=='"test-v1"' and request.url.params["sendUpdates"]=="none"
                self.deletes+=1; self.events.pop(event_id,None)
                if self.drop_delete:
                    self.drop_delete=False
                    raise httpx.ReadTimeout("Synthetic dropped response",request=request)
                return httpx.Response(204)
            raise AssertionError("Unexpected synthetic method")


@pytest.fixture
def flow(tmp_path):
    url="sqlite+pysqlite:///"+(tmp_path/"export.db").as_posix()
    repo=SQLiteScheduleRepository(url);repo.migrate()
    s=ExportSession(session_id="session-verified-double",organizer_id="owner",
        participant_ids={"owner","candidate"},accepted_ids={"owner","candidate"},revision="r1",
        start="2026-10-26T20:00Z",end="2026-10-26T21:00Z",timezone=ZONE,
        course="Synthetic course",place="Synthetic campus venue",status="confirmed",authoritative=True)
    gateway=SessionDouble(s)
    for owner in s.participant_ids:
        repo.create_record(owner,"weekly-rules",WeeklyRuleInput(weekday=0,start_local="13:00",end_local="18:00",timezone=ZONE))
    vault=GoogleVault(repo.engine,Fernet.generate_key());vault.migrate()
    subject=token_urlsafe(20)
    payload={"access_token":token_urlsafe(24),"refresh_token":token_urlsafe(32),"token_type":"Bearer",
        "scope":READ_SCOPE+" "+WRITE_SCOPE+" openid","expires_at":(NOW+timedelta(hours=1)).timestamp(),
        "account_subject":subject}
    vault.save("owner",payload,0);repo.mark_connected("owner")
    remote=GoogleDouble();transport=httpx.MockTransport(remote.reply)
    actor=GoogleActor("owner",ZONE,token_urlsafe(24),token_urlsafe(24))
    oauth=GoogleOAuth(GoogleConfig("synthetic-client",token_urlsafe(24)),vault,clock=lambda:NOW,transport=transport)
    provider=GoogleCalendarProvider(oauth.access_token,lambda _:ZONE,clock=lambda:NOW,transport=transport,pause=lambda _:None)
    active=GoogleCalendarIntegration(repo,vault,oauth,provider,gateway)
    operations=ExportRepository(repo.engine);operations.migrate()
    writer=GoogleEventWriter(oauth,transport=transport)
    service=CalendarExportService(operations,gateway,writer,active.service,active.refresh_calendars,lambda:NOW)
    yield SimpleNamespace(**locals())
    repo.close()


def approval(f):
    return f.service.approve(f.actor,f.s.session_id,f.gateway.snapshot.fingerprint())


def test_acceptance_and_independent_confirmation_are_required(flow):
    f=flow
    assert f.service.preview(f.actor,f.s.session_id)["status"]=="ready" and f.remote.posts==0
    with pytest.raises(ExportError,match="APPROVAL_REQUIRED"):
        f.service.export(f.actor,f.s.session_id,token_urlsafe(24))
    f.gateway.snapshot=f.s.model_copy(update={"accepted_ids":frozenset({"owner"}),"status":"pending"})
    with pytest.raises(ExportError,match="WAITING"):
        approval(f)
    assert f.remote.posts==0


@pytest.mark.parametrize("change",[
    {"status":"cancelled"},{"status":"expired"},
    {"participant_ids":frozenset({"owner","candidate","third"}),"accepted_ids":frozenset({"owner","candidate","third"})},
    {"authoritative":False}])
def test_inactive_group_and_synthetic_sessions_never_write(flow,change):
    f=flow;f.gateway.snapshot=f.s.model_copy(update=change)
    with pytest.raises(ExportError): approval(f)
    assert f.remote.posts==0


@pytest.mark.parametrize("change",[{"revision":"r2"},{"place":"Another public venue"},
    {"end":datetime(2026,10,26,22,tzinfo=timezone.utc)},
    {"participant_ids":frozenset({"owner","other"}),"accepted_ids":frozenset({"owner","other"})}])
def test_old_approval_does_not_authorize_changed_content(flow,change):
    f=flow;token=approval(f);f.gateway.snapshot=f.s.model_copy(update=change)
    with pytest.raises(ExportError,match="APPROVAL_REQUIRED"): f.service.export(f.actor,f.s.session_id,token)
    assert f.remote.posts==0


def test_nonorganizer_and_unknown_session_fail_before_provider(flow):
    f=flow
    other=GoogleActor("candidate",ZONE,token_urlsafe(24),token_urlsafe(24))
    with pytest.raises(ExportError,match="ORGANIZER_ONLY"): f.service.preview(other,f.s.session_id)
    with pytest.raises(ExportError,match="SESSION_NOT_FOUND"): f.service.preview(f.actor,"forged")
    token=approval(f);f.service.export(f.actor,f.s.session_id,token)
    with pytest.raises(ExportError,match="ORGANIZER_ONLY"): f.service.delete(other,f.s.session_id,token)
    assert f.remote.deletes==0


def test_duplicate_concurrent_clicks_and_restart_keep_single_event(flow):
    f=flow;token=approval(f)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:f.service.export(f.actor,f.s.session_id,token),range(4)))
    assert f.remote.posts==1 and all(r["status"] in {"added","adding"} for r in results)
    repo2=SQLiteScheduleRepository(f.url);repo2.migrate()
    try:
        operations=ExportRepository(repo2.engine);operations.migrate()
        restarted=CalendarExportService(operations,f.gateway,f.writer,f.active.service,f.active.refresh_calendars,lambda:NOW)
        result=restarted.export(f.actor,f.s.session_id,token)
        assert result["status"]=="added" and f.remote.posts==1
        assert operations.get("owner",f.s.session_id,NOW)["event_id"] in f.remote.events
    finally: repo2.close()


def test_google_success_lost_response_reconciles_same_id(flow):
    f=flow;token=approval(f);f.remote.drop_insert=True
    with pytest.raises(ExportError,match="PROVIDER_RESULT_UNKNOWN"): f.service.export(f.actor,f.s.session_id,token)
    assert f.operations.get("owner",f.s.session_id,NOW)["status"]=="unknown"
    result=f.service.export(f.actor,f.s.session_id,token)
    assert result["status"]=="added" and f.remote.posts==1


@pytest.mark.parametrize("owner,kind",[("owner","exam"),("candidate","exam"),("owner","busy"),("candidate","busy")])
def test_hard_conflicts_block_export_for_every_participant(flow,owner,kind):
    f=flow
    if kind=="exam":
        f.repo.create_record(owner,"exams",ExamInput(course_id="course",start=f.s.start,end=f.s.end))
    else:
        f.repo.create_record(owner,"exceptions",ExceptionInput(kind="unavailable",start=f.s.start,end=f.s.end))
    with pytest.raises(ExportError,match="BUSY_CONFLICT"): f.service.export(f.actor,f.s.session_id,approval(f))
    assert f.remote.posts==0


def test_self_reservation_and_verified_own_export_excluded_but_other_event_blocks(flow):
    f=flow;token=approval(f);f.service.export(f.actor,f.s.session_id,token)
    op=f.operations.get("owner",f.s.session_id,NOW)
    f.service.preflight(f.s,op,f.remote.events[op["event_id"]])
    other=deepcopy(f.remote.events[op["event_id"]]);other["id"]="external-other";other.pop("extendedProperties")
    f.remote.events[other["id"]]=other
    with pytest.raises(ExportError,match="BUSY_CONFLICT"):
        f.service.preflight(f.s,op,f.remote.events[op["event_id"]])
    assert f.remote.posts==1


def test_failed_refresh_stays_unknown_and_never_writes(flow):
    f=flow;f.remote.fail_read=True
    with pytest.raises(ExportError,match="CALENDAR_UNVERIFIED"): f.service.export(f.actor,f.s.session_id,approval(f))
    assert f.remote.posts==0 and f.repo.read_bundle("owner")["state"]["connection_status"]=="stale"


def test_final_version_and_account_generation_rechecked_after_refresh(flow):
    f=flow;token=approval(f);refresh=f.service.refresh
    def change_after_read(*args):
        result=refresh(*args);f.gateway.snapshot=f.s.model_copy(update={"revision":"changed-after-refresh"})
        return result
    f.service.refresh=change_after_read
    with pytest.raises(ExportError,match="PREVIEW_CHANGED"): f.service.export(f.actor,f.s.session_id,token)
    assert f.remote.posts==0


def test_delete_is_explicit_linked_repeatable_and_never_recreates(flow):
    f=flow;token=approval(f);f.service.export(f.actor,f.s.session_id,token)
    with pytest.raises(ExportError,match="APPROVAL_REQUIRED"): f.service.delete(f.actor,f.s.session_id,token)
    deletion=f.service.approve(f.actor,f.s.session_id,f.s.fingerprint(),"delete")
    f.remote.drop_delete=True
    with pytest.raises(ExportError,match="PROVIDER_RESULT_UNKNOWN"): f.service.delete(f.actor,f.s.session_id,deletion)
    with pytest.raises(ExportError,match="EXPORT_REMOVED"): f.service.export(f.actor,f.s.session_id,token)
    assert f.service.delete(f.actor,f.s.session_id,deletion)=={"status":"removed"}
    assert f.service.delete(f.actor,f.s.session_id,deletion)=={"status":"removed"}
    assert f.remote.posts==f.remote.deletes==1 and f.gateway.snapshot.status=="confirmed"


def test_unrelated_or_modified_remote_event_is_not_deleted(flow):
    f=flow;token=approval(f);f.service.export(f.actor,f.s.session_id,token)
    op=f.operations.get("owner",f.s.session_id,NOW);f.remote.events[op["event_id"]]["location"]="Changed elsewhere"
    deletion=f.service.approve(f.actor,f.s.session_id,f.s.fingerprint(),"delete")
    with pytest.raises(ExportError,match="REMOTE_EVENT_CHANGED"): f.service.delete(f.actor,f.s.session_id,deletion)
    assert f.remote.deletes==0


def test_account_reauthorization_invalidates_old_approval(flow):
    f=flow;token=approval(f)
    old,g,v=f.vault.load("owner");f.vault.save("owner",old,g,version=v,new_authorization=True)
    with pytest.raises(ExportError,match="APPROVAL_REQUIRED"): f.service.export(f.actor,f.s.session_id,token)
    assert f.remote.posts==0


def test_http_csrf_extra_identity_and_event_parameters_cannot_bypass(flow):
    f=flow;app=FastAPI()
    app.state.calendar_export_service=f.service
    @app.middleware("http")
    async def verified_test_session(request,call_next):
        request.state.calendar_actor=f.actor
        return await call_next(request)
    app.include_router(router)
    base="/calendar/sessions/"+f.s.session_id
    headers={"X-CSRF-Token":f.actor.csrf_token}
    with TestClient(app) as client:
        assert client.get(base+"/preview").json()["status"]=="ready"
        body={"fingerprint":f.s.fingerprint()}
        assert client.post(base+"/approval",json=body).status_code==403
        for field in ("accepted","organizer_id","calendar_id","event_id","session_id"):
            assert client.post(base+"/approval",json={**body,field:"forged"},headers=headers).status_code==422
        assert client.post(base+"/approval",json=body,headers={**headers,"Origin":"https://wrong.invalid"}).status_code==403
        token=client.post(base+"/approval",json=body,headers=headers).json()
        assert f.remote.posts==0
        assert client.post(base+"/export",json=token,headers=headers).json()["status"]=="added"


def test_http_ambiguous_result_is_explicitly_unknown_not_success(flow):
    f=flow;app=FastAPI();app.state.calendar_export_service=f.service
    @app.middleware("http")
    async def verified_test_session(request,call_next):
        request.state.calendar_actor=f.actor
        return await call_next(request)
    app.include_router(router);f.remote.drop_insert=True
    with TestClient(app) as client:
        response=client.post("/calendar/sessions/"+f.s.session_id+"/export",
            json={"approval_ref":approval(f)},headers={"X-CSRF-Token":f.actor.csrf_token})
        assert response.status_code==202 and response.json()["status"]=="unknown"


def test_write_scope_upgrade_is_incremental_denial_preserves_read_and_callback_never_exports(flow):
    f=flow
    old,g,v=f.vault.load("owner");old["scope"]=READ_SCOPE
    f.vault.save("owner",old,g,version=v)
    begin=f.oauth.begin_write(f.actor);query=parse_qs(urlsplit(begin).query)
    assert set(query["scope"][0].split())=={READ_SCOPE,WRITE_SCOPE,"openid"}
    assert "email" not in query["scope"][0] and query["include_granted_scopes"]==["true"]
    f.vault.consume(f.actor,query["state"][0],NOW)  # Simulated consent denial consumes state only.
    assert f.vault.load("owner")[0]["scope"]==READ_SCOPE
    assert f.active.refresh(f.actor).verification_level=="google_current" and f.remote.posts==0


def test_signed_google_identity_verification_checks_signature_audience_expiry_and_nonce(flow):
    f=flow;key=RSAKey.generate_key(2048,parameters={"kid":"synthetic-key"})
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"keys":[key.as_dict()]}))
    verifier=GoogleIdentityVerifier("client",lambda:NOW,transport)
    claims={"iss":"https://accounts.google.com","aud":"client","sub":token_urlsafe(24),
        "iat":NOW.timestamp(),"exp":NOW.timestamp()+3600,"nonce":token_urlsafe(24)}
    encoded=jwt.encode({"alg":"RS256","kid":"synthetic-key"},claims,key)
    assert verifier(encoded,claims["nonce"])==claims["sub"]
    for changed in ({"aud":"wrong"},{"exp":NOW.timestamp()-1},{"nonce":"wrong"},{"iss":"https://wrong.invalid"}):
        invalid=jwt.encode({"alg":"RS256","kid":"synthetic-key"},{**claims,**changed},key)
        with pytest.raises(GoogleStateError,match="NOT_VERIFIED"): verifier(invalid,claims["nonce"])


def test_incremental_oauth_callback_verifies_account_and_never_executes_export(flow):
    from app.routers.calendar_google import router as google_router
    f=flow;key=RSAKey.generate_key(2048,parameters={"kid":"callback-test"})
    authorization=f.oauth.begin_write(f.actor);query=parse_qs(urlsplit(authorization).query)
    identity=jwt.encode({"alg":"RS256","kid":"callback-test"},{
        "iss":"https://accounts.google.com","aud":f.oauth.config.client_id,"sub":f.subject,
        "iat":NOW.timestamp(),"exp":NOW.timestamp()+3600,"nonce":query["nonce"][0]},key)
    access,refresh=token_urlsafe(24),token_urlsafe(32)
    def callback_http(request):
        if str(request.url)==TOKEN_URL:
            return httpx.Response(200,json={"access_token":access,"refresh_token":refresh,"token_type":"Bearer",
                "expires_at":NOW.timestamp()+3600,"scope":READ_SCOPE+" "+WRITE_SCOPE+" openid","id_token":identity})
        return httpx.Response(200,json={"keys":[key.as_dict()]})
    transport=httpx.MockTransport(callback_http);f.oauth.transport=transport
    f.oauth.identity_verifier=GoogleIdentityVerifier(f.oauth.config.client_id,lambda:NOW,transport)
    app=FastAPI();app.state.google_calendar_integration=f.active
    @app.middleware("http")
    async def test_auth(request,call_next):
        request.state.calendar_actor=f.actor
        return await call_next(request)
    app.include_router(google_router)
    with TestClient(app,base_url="http://127.0.0.1:8011") as client:
        response=client.get("/calendar/google/callback",params={"state":query["state"][0],
            "code":token_urlsafe(24)},follow_redirects=False)
        assert response.status_code==303 and response.headers["location"].startswith("/session?")
    stored=f.vault.load("owner")[0]
    assert stored["account_subject"]==f.subject and "id_token" not in stored
    assert f.remote.posts==0 and f.operations.get("owner",f.s.session_id,NOW) is None


def test_failed_upgrade_keeps_previous_credentials_and_refresh_preserves_account(flow):
    f=flow
    old,g,v=f.vault.load("owner")
    begin=f.oauth.begin_write(f.actor);state=parse_qs(urlsplit(begin).query)["state"][0]
    f.oauth.identity_verifier=lambda encoded,nonce:token_urlsafe(24)  # Simulated different verified account.
    token={**old,"id_token":"synthetic-not-a-real-token"}
    f.oauth.transport=httpx.MockTransport(lambda request:httpx.Response(200,json=token))
    with pytest.raises(GoogleStateError,match="BINDING_CHANGED"):
        f.active.complete(f.actor,token_urlsafe(24),state)
    assert f.vault.load("owner")[0]==old and f.remote.posts==0
    old["expires_at"]=NOW.timestamp()-1;_,g,v=f.vault.load("owner");f.vault.save("owner",old,g,version=v)
    seen=[]
    def refresh(request):
        seen.append(parse_qs(request.content.decode()))
        return httpx.Response(200,json={"access_token":token_urlsafe(24),"token_type":"Bearer",
            "expires_at":NOW.timestamp()+3600,"scope":old["scope"]})
    f.oauth.transport=httpx.MockTransport(refresh)
    f.oauth.access_token("owner")
    assert WRITE_SCOPE in seen[0]["scope"][0]
    assert f.vault.load("owner")[0]["account_subject"]==f.subject


def test_export_credentials_and_private_titles_do_not_enter_matching_or_logs(flow,caplog):
    import logging
    from app.scheduling.core.schemas import TrustedMatchContext, Horizon
    f=flow;caplog.set_level(logging.DEBUG)
    f.remote.events["ordinary-private"]={"id":"ordinary-private","summary":"SYNTHETIC_PRIVATE_TITLE",
        "start":{"dateTime":"2026-10-27T15:00-07:00"},"end":{"dateTime":"2026-10-27T16:00-07:00"}}
    f.service.export(f.actor,f.s.session_id,approval(f))
    result=f.active.service.batch_availability(TrustedMatchContext(eligible_subject_ids=f.s.participant_ids),
        list(f.s.participant_ids),Horizon(start=f.s.start,end=f.s.end),30)
    safe="".join(r.model_dump_json() for r in result)
    assert all(value not in safe+caplog.text for value in
        ("SYNTHETIC_PRIVATE_TITLE",f.payload["access_token"],f.payload["refresh_token"],f.oauth.config.client_secret))
    assert all(word not in safe for word in ("private_title","email","token"))


def test_real_refresh_hook_requires_server_candidate_eligibility(flow):
    from starlette.requests import Request
    from app.scheduling.session_hooks import refresh_before_request
    from app.scheduling.core.schemas import TrustedMatchContext
    f=flow;app=FastAPI();app.state.google_calendar_integration=f.active
    request=Request({"type":"http","app":app,"state":{"calendar_actor":f.actor}})
    with pytest.raises(ValueError,match="eligibility"): refresh_before_request(request)
    app.state.calendar_match_context_for_actor=lambda actor:TrustedMatchContext(eligible_subject_ids=f.s.participant_ids)
    statuses=refresh_before_request(request)
    assert statuses=={"owner":"google_current","candidate":"manual_only"}


def test_existing_export_still_requires_write_permission_for_remove(flow):
    f=flow;f.service.export(f.actor,f.s.session_id,approval(f))
    old,g,v=f.vault.load("owner");old["scope"]=READ_SCOPE
    f.vault.save("owner",old,g,version=v)
    view=f.service.preview(f.actor,f.s.session_id)
    assert view["status"]=="WRITE_SCOPE_REQUIRED" and view["html_link"]


def test_switching_google_account_never_moves_existing_export(flow):
    f=flow;f.service.export(f.actor,f.s.session_id,approval(f))
    old,g,v=f.vault.load("owner");old["account_subject"]=token_urlsafe(24)
    f.vault.save("owner",old,g,version=v,new_authorization=True)
    assert f.service.preview(f.actor,f.s.session_id)["status"]=="GOOGLE_ACCOUNT_BINDING_CHANGED"
    with pytest.raises(ExportError,match="BINDING_CHANGED"): approval(f)
    assert f.remote.posts==1 and f.remote.deletes==0
