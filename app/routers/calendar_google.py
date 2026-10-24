"""Read-only OAuth routes. Disabled unless team auth AND live composition are injected."""
from secrets import compare_digest
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from app.scheduling.google_config import GoogleActor
from app.scheduling.google_integration import GoogleCalendarIntegration
from app.scheduling.google_storage import GoogleStateError

router = APIRouter(prefix="/calendar/google",tags=["calendar-read-only"])


def authenticated_calendar_owner(request: Request):
    # Only trusted server middleware may assign this state. No fake owner, query ID,
    # X-User header, bearer string, or browser 'trusted=true' establishes identity.
    actor = getattr(request.state,"calendar_actor",None)
    if not isinstance(actor,GoogleActor):
        raise HTTPException(409,"TEAM_AUTH_INTEGRATION_REQUIRED")
    if request.method not in {"GET","HEAD"}:
        csrf = request.headers.get("x-csrf-token","")
        origin = request.headers.get("origin")
        if (not compare_digest(csrf.encode(),actor.csrf_token.encode()) or
            (origin and origin!=f"{request.url.scheme}://{request.url.netloc}")):
            raise HTTPException(403,"CSRF_REQUIRED")
    return actor


def integration(request: Request):
    configured = getattr(request.app.state,"google_calendar_integration",None)
    if not isinstance(configured,GoogleCalendarIntegration):
        raise HTTPException(409,"GOOGLE_NOT_CONFIGURED")
    return configured


@router.get("/connect")
def connect(request: Request, actor=Depends(authenticated_calendar_owner)):
    active = integration(request)
    return RedirectResponse(active.oauth.begin(actor),status_code=303)


@router.get("/callback")
def callback(request: Request, actor=Depends(authenticated_calendar_owner)):
    active = integration(request)
    actual = f"{request.url.scheme}://{request.url.netloc}{request.url.path}"
    if actual != active.oauth.config.redirect_uri:
        raise HTTPException(400,"CALLBACK_URI_MISMATCH")
    state, code = request.query_params.get("state"),request.query_params.get("code")
    if not state:
        raise HTTPException(400,"INVALID_OAUTH_STATE")
    try:
        if not code or request.query_params.get("error"):
            active.vault.consume(actor,state,active.oauth.clock())
            raise GoogleStateError("OAUTH_NOT_COMPLETED")
        context = active.complete(actor,code,state)
    except GoogleStateError:
        raise HTTPException(400,"OAUTH_NOT_COMPLETED_OR_STATE_INVALID") from None
    if context and context.get("purpose")=="write":
        return RedirectResponse("/session?notice=Calendar+permission+updated.+Review+the+current+session+and+confirm+separately.",status_code=303)
    return RedirectResponse("/schedule?saved=google-read-consent",status_code=303)


@router.post("/refresh")
def refresh(request: Request, actor=Depends(authenticated_calendar_owner)):
    try:
        return integration(request).refresh(actor)
    except GoogleStateError:
        raise HTTPException(409,"GOOGLE_RECONNECT_REQUIRED") from None


@router.post("/disconnect")
def disconnect(request: Request, actor=Depends(authenticated_calendar_owner)):
    integration(request).disconnect(actor)
    return {"connection_status":"disconnected","verification_level":"manual_only"}
