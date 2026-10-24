"""Local-only account setup and synthetic Omnara advice demonstration."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .api import authenticated_user
from .omnara import OmnaraConnection, OmnaraError, StudyAdviceRunner


class AdviceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    viewer: Literal["alex", "bella"] = "alex"
    duration: Literal[30, 60, 90] = 60
    scenario: Literal["normal", "new_conflict"] = "normal"


def mount_omnara_demo(app, view_data, connection=None):
    app.state.omnara = connection or OmnaraConnection()
    app.state.omnara_requests = {}

    def local_browser(request: Request, user_id=Depends(authenticated_user)):
        if request.url.hostname not in ("127.0.0.1", "localhost"):
            raise HTTPException(403, "This setup endpoint is local-only")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Use the local StudyMate page")
        return user_id

    @app.exception_handler(OmnaraError)
    async def provider_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"code": exc.code,
                            "detail": "Omnara request was not completed; no invitation or calendar action occurred"})

    @app.get("/demo/omnara/status", dependencies=[Depends(local_browser)])
    def status():
        return app.state.omnara.status()

    @app.post("/demo/omnara/connect", dependencies=[Depends(local_browser)])
    def connect(request: Request):
        nonce = request.cookies.get("studymate_omnara_flow") or secrets.token_urlsafe(32)
        data = app.state.omnara.start(nonce)
        result = JSONResponse(data)
        result.set_cookie("studymate_omnara_flow", nonce, httponly=True, samesite="strict",
                          path="/demo/omnara", max_age=900)
        return result

    @app.post("/demo/omnara/poll", dependencies=[Depends(local_browser)])
    def poll(request: Request):
        return app.state.omnara.poll(request.cookies.get("studymate_omnara_flow", ""))

    @app.get("/demo/omnara/resources", dependencies=[Depends(local_browser)])
    def resources(org_id: str, project_id: str | None = None):
        return app.state.omnara.resources(org_id, project_id)

    @app.post("/demo/omnara/advise", dependencies=[Depends(local_browser)])
    def advise(body: AdviceInput):
        connection = app.state.omnara
        resource = connection.resources(body.org_id, body.project_id)
        model = next((x for x in resource["models"] if x["id"] == body.model_id), None)
        if model is None:
            raise HTTPException(422, "Choose a model already granted to this project")
        data = view_data(body.viewer, body.duration, body.scenario)
        owner = next(x for x in data["people"] if x["is_self"])
        peer = next(x for x in data["people"] if not x["is_self"])
        slots = [{"start": slot["start"],
                  "end": (datetime.fromisoformat(slot["start"])+timedelta(minutes=body.duration)).isoformat()}
                 for slot in data["common"]]
        # Build a new minimal projection. Never send own_conflicts or source schedule rows.
        tool_results = {
            "get_profile": {"id": owner["id"], "name": owner["name"], "courses": ["STAT153"], "synthetic": True},
            "find_matches": {"course": "STAT153", "duration_minutes": body.duration, "timezone": data["timezone"],
                "candidates": [{"id": peer["id"], "name": peer["name"], "slots": slots}] if slots else [],
                "synthetic": True},
            "suggest_places": {"places": [{"id": "demo-library", "name": "Demo Study Library",
                "kind": "library", "synthetic": True, "booking_verified": False}]},
            "check_booking": {"supported": False, "status": "unsupported", "synthetic": True},
        }
        fingerprint = hashlib.sha256(json.dumps({"request": body.model_dump(), "tools": tool_results},
                                               sort_keys=True).encode()).hexdigest()
        records = app.state.omnara_requests
        with connection.lock:
            old = records.get(body.request_id)
            if old and old["fingerprint"] != fingerprint:
                raise HTTPException(409, "Request changed; use a new request id")
            if old and old.get("result"):
                return old["result"]
            if old and old.get("running"):
                raise HTTPException(409, "This request is already running")
            if len(records) >= 100 and body.request_id not in records:
                raise HTTPException(429, "Local demo request limit reached; restart the demo")
            records[body.request_id] = {"fingerprint": fingerprint, "running": True}
        try:
            result = StudyAdviceRunner(connection.client).run(body.org_id, body.project_id, model, tool_results, body.request_id)
            result.update(provider="omnara", real_model_call=True, data_mode="synthetic", model=model["name"])
            records[body.request_id]["result"] = result
            return result
        finally:
            records[body.request_id]["running"] = False
