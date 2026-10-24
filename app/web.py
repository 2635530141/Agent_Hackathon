from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")


def render(request: Request, name: str, **context):
    user = None
    if "session" in request.scope:
        user = request.session.get("user")
    payload = {"request": request, "nav": context.pop("nav", ""), "user": user,
        "private_calendar":bool(getattr(request.app.state,"private_calendar_host",None)),
        "csrf_token":request.session.get("calendar_editor_csrf","") if "session" in request.scope else "", **context}
    try:
        return templates.TemplateResponse(request, name, payload)
    except TypeError:
        return templates.TemplateResponse(name, payload)
