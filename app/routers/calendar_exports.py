"""Server-only calendar executor routes; injected auth, CSRF and real session gateway."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from app.routers.calendar_google import authenticated_calendar_owner
from app.scheduling.export_service import CalendarExportService
from app.scheduling.export_models import ExportError
from app.scheduling.google_storage import GoogleStateError

router = APIRouter(prefix="/calendar/sessions",tags=["calendar-exports"])


class PreviewConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: str = Field(default="export",pattern=r"^(export|delete)$")


class ExecuteConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_ref: str = Field(min_length=20,max_length=128)


def executor(request: Request):
    active = getattr(request.app.state,"calendar_export_service",None)
    if not isinstance(active,CalendarExportService):
        raise HTTPException(409,"AUTHORITATIVE_SESSION_INTEGRATION_REQUIRED")
    return active


def perform(call):
    try:
        return call()
    except ExportError as exc:
        if exc.status==202:
            return JSONResponse({"status":"unknown","code":exc.code},status_code=202)
        raise HTTPException(exc.status,exc.code) from None
    except GoogleStateError:
        raise HTTPException(409,"GOOGLE_RECONNECT_REQUIRED") from None


@router.get("/{session_id}/preview")
def preview(session_id: str, request: Request, actor=Depends(authenticated_calendar_owner)):
    return perform(lambda:executor(request).preview(actor,session_id))


@router.post("/{session_id}/write-permission")
def write_permission(session_id: str, request: Request, actor=Depends(authenticated_calendar_owner)):
    active = executor(request)
    perform(lambda:active.session(actor,session_id))
    return {"authorization_url":perform(lambda:active.writer.oauth.begin_write(actor))}


@router.post("/{session_id}/approval")
def approval(session_id: str, body: PreviewConfirmation, request: Request,
             actor=Depends(authenticated_calendar_owner)):
    token = perform(lambda:executor(request).approve(actor,session_id,body.fingerprint,body.kind))
    return {"approval_ref":token}


@router.post("/{session_id}/export")
def export(session_id: str, body: ExecuteConfirmation, request: Request,
           actor=Depends(authenticated_calendar_owner)):
    return perform(lambda:executor(request).export(actor,session_id,body.approval_ref))


@router.delete("/{session_id}/export")
def delete(session_id: str, body: ExecuteConfirmation, request: Request,
           actor=Depends(authenticated_calendar_owner)):
    return perform(lambda:executor(request).delete(actor,session_id,body.approval_ref))
