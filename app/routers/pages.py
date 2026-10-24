from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app import accounts
from app.web import render

router = APIRouter()


def _landing(request: Request, mode: str, error: str = "", full_name: str = "", email: str = ""):
    if request.session.get("user"):
        return RedirectResponse("/home", status_code=303)
    return render(
        request,
        "landing.html",
        nav="register" if mode == "register" else "login",
        mode=mode,
        error=error,
        full_name=full_name,
        email=email,
        test_email=accounts.TEST_EMAIL if not getattr(request.app.state,"private_calendar_host",None) else "",
        test_password=accounts.TEST_PASSWORD if not getattr(request.app.state,"private_calendar_host",None) else "",
    )


@router.get("/")
def landing(request: Request):
    return _landing(request, "login")


@router.get("/login")
def login_page(request: Request):
    return _landing(request, "login")


@router.get("/register")
def register_page(request: Request):
    return _landing(request, "register")


@router.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form("")):
    host = getattr(request.app.state,"private_calendar_host",None)
    user = (host.accounts if host else accounts).authenticate(email, password)
    if user is None:
        return _landing(request, "login", "That email and password do not match.", email=email)
    _signed_in(request, user)
    return RedirectResponse("/home", status_code=303)


@router.post("/register")
def register(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
):
    try:
        host = getattr(request.app.state,"private_calendar_host",None)
        user = (host.accounts if host else accounts).register(name, email, password)
    except ValueError as exc:
        return _landing(request, "register", str(exc), full_name=name, email=email)
    _signed_in(request, user)
    return RedirectResponse("/home", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


def _signed_in(request, user):
    from secrets import token_urlsafe
    request.session.clear()
    request.session["user"] = {"id":user["id"], "name":user["name"]}
    request.session["login_binding"] = token_urlsafe(32)
    request.session["calendar_editor_csrf"] = token_urlsafe(32)


@router.get("/home")
def home(request: Request):
    return render(request, "home.html", nav="home")


@router.get("/api/health")
def health():
    return {"ok": True}
