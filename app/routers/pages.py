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
        test_email=accounts.TEST_EMAIL,
        test_password=accounts.TEST_PASSWORD,
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
    user = accounts.authenticate(email, password)
    if user is None:
        return _landing(request, "login", "That email and password do not match.", email=email)
    request.session["user"] = user
    return RedirectResponse("/home", status_code=303)


@router.post("/register")
def register(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
):
    try:
        user = accounts.register(name, email, password)
    except ValueError as exc:
        return _landing(request, "register", str(exc), full_name=name, email=email)
    request.session["user"] = user
    return RedirectResponse("/home", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/home")
def home(request: Request):
    return render(request, "home.html", nav="home")


@router.get("/style")
def style_guide(request: Request):
    return render(request, "styleguide.html", nav="style")


@router.get("/api/health")
def health():
    return {"ok": True}
