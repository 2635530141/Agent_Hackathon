from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import matching, pages, profile, schedule, session
from app.web import BASE

app = FastAPI(title="Study mate")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.include_router(pages.router)
app.include_router(profile.router)
app.include_router(schedule.router)
app.include_router(matching.router)
app.include_router(session.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", reload=True)
