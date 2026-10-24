from fastapi import FastAPI
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.routers import matching, pages, profile, schedule, session
from app.routers import calendar_google
from app.routers import agent
from app.routers import calendar_exports
from app.routers import calendar_editor
from app.web import BASE

_OPEN = {"/", "/login", "/register", "/api/health"}


def create_app(*, calendar_host=None):
    app = FastAPI(title="StudyMate")
    app.state.private_calendar_host = calendar_host
    if calendar_host:
        app.state.google_calendar_integration = calendar_host.active
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    for router in (pages.router, profile.router, schedule.router, matching.router, session.router,
                   calendar_google.router, agent.router, calendar_exports.router, calendar_editor.router,
                   calendar_editor.form_router):
        app.include_router(router)

    @app.middleware("http")
    async def require_login(request, call_next):
        from secrets import token_urlsafe, compare_digest
        from .scheduling.context import calendar_context
        path = request.url.path
        token = None
        if calendar_host:
            # Explicitly private preview, never a public production deployment.
            if request.url.netloc != calendar_host.origin.split("://",1)[1] or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
                return JSONResponse({"detail":"PRIVATE_LOOPBACK_HOST_REQUIRED"},403)
            request.session.setdefault("calendar_editor_csrf", token_urlsafe(32))
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                csrf = request.headers.get("x-csrf-token", "")
                if not csrf and request.headers.get("content-type", "").split(";",1)[0] in {"application/x-www-form-urlencoded", "multipart/form-data"}:
                    await request.body()  # Cache before form parsing for downstream handlers.
                    csrf = str((await request.form()).get("csrf_token", ""))
                if (origin and origin != calendar_host.origin) or not compare_digest(csrf.encode(), request.session["calendar_editor_csrf"].encode()):
                    return JSONResponse({"detail":"CSRF_REQUIRED"},403)
            session_user = request.session.get("user") or {}
            user = calendar_host.accounts.get(session_user.get("id"))
            if user and request.session.get("login_binding"):
                context = calendar_host.context(user, request.session)
                request.state.calendar_actor = context.actor
                token = calendar_context.set(context)
            elif session_user:
                request.session.pop("user",None)
            # Fixture Profile/Matching/Session must never operate on real calendars.
            allowed = path in _OPEN | {"/logout", "/home"} or path.startswith(("/static/", "/schedule", "/api/schedule", "/calendar/google/"))
            if not allowed:
                if token is not None: calendar_context.reset(token)
                return JSONResponse({"detail":"TEAM_ADAPTER_REQUIRED", "message":"This private calendar preview has no live Profile, Matching or Session adapter."},409)
        try:
            if path in _OPEN or path.startswith("/static") or path == "/logout" and request.method == "POST":
                return await call_next(request)
            if request.session.get("user"):
                if calendar_host and path == "/home":
                    return RedirectResponse("/schedule",303)
                return await call_next(request)
            return RedirectResponse("/login",status_code=303)
        finally:
            if token is not None: calendar_context.reset(token)

    app.add_middleware(SessionMiddleware,
        secret_key=calendar_host.session_secret if calendar_host else "studymate-demo",
        session_cookie="studymate_private" if calendar_host else "session",
        same_site="lax", https_only=False, max_age=8*60*60)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", reload=True)
