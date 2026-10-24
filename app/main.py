from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.routers import matching, pages, profile, schedule, session
from app.routers import calendar_google
from app.routers import calendar_exports
from app.routers import calendar_editor
from app.web import BASE

app = FastAPI(title="StudyMate")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.include_router(pages.router)
app.include_router(profile.router)
app.include_router(schedule.router)
app.include_router(matching.router)
app.include_router(session.router)
app.include_router(calendar_google.router)
app.include_router(calendar_exports.router)
app.include_router(calendar_editor.router)
app.include_router(calendar_editor.form_router)

_OPEN = {"/", "/login", "/register", "/api/health"}


@app.middleware("http")
async def require_login(request, call_next):
    path = request.url.path
    if path in _OPEN or path.startswith("/static") or request.method == "POST" and path in {"/login", "/register", "/logout"}:
        return await call_next(request)
    if request.session.get("user"):
        return await call_next(request)
    return RedirectResponse("/login", status_code=303)


app.add_middleware(SessionMiddleware, secret_key="studymate-demo", same_site="lax", https_only=False)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", reload=True)
