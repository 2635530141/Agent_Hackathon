from fastapi import APIRouter, Request

from app.web import render

router = APIRouter()


@router.get("/")
def home(request: Request):
    return render(request, "home.html", nav="home")


@router.get("/style")
def style_guide(request: Request):
    return render(request, "styleguide.html", nav="style")


@router.get("/api/health")
def health():
    return {"ok": True}
