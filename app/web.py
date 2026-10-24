from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")


def render(request: Request, name: str, **context):
    payload = {"request": request, "nav": context.pop("nav", ""), **context}
    try:
        return templates.TemplateResponse(request, name, payload)
    except TypeError:
        return templates.TemplateResponse(name, payload)
