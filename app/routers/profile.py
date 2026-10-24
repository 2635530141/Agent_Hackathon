from urllib.parse import quote

from fastapi import APIRouter, Body, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import profile_core, store
from app.web import render

router = APIRouter()


def _form_data(
    study_style: str,
    street_address: str,
    radius_meters: str,
    anchor: str,
    group_size: str,
    major: str,
    year: str,
    gender_self: str,
    gender_preference: str,
    place_types: list[str] | None,
    course_ids: list[str] | None,
    under_18: str,
) -> dict:
    return {
        "study_style": study_style,
        "street_address": street_address,
        "radius_meters": radius_meters,
        "anchor": anchor,
        "group_size": group_size,
        "major": major,
        "year": year,
        "gender_self": gender_self,
        "gender_preference": gender_preference,
        "place_types": place_types or [],
        "course_ids": course_ids or [],
        "under_18": under_18 == "1",
    }


@router.get("/profile")
def profile_page(request: Request, error: str = "", saved: int = 0):
    profile = profile_core.private_profile()
    public = profile_core.public_profile()
    return render(
        request,
        "profile.html",
        nav="profile",
        profile=profile,
        public=public,
        courses=store.courses(),
        style_labels=profile_core.STYLE_LABELS,
        place_labels=profile_core.PLACE_LABELS,
        error=error,
        saved=bool(saved),
    )


@router.post("/profile")
def profile_save(
    study_style: str = Form(...),
    street_address: str = Form(""),
    radius_meters: str = Form(...),
    anchor: str = Form(...),
    group_size: str = Form(...),
    major: str = Form(...),
    year: str = Form(...),
    gender_self: str = Form(""),
    gender_preference: str = Form(""),
    place_types: list[str] | None = Form(None),
    course_ids: list[str] | None = Form(None),
    under_18: str = Form(""),
):
    try:
        profile_core.update_profile(
            _form_data(
                study_style,
                street_address,
                radius_meters,
                anchor,
                group_size,
                major,
                year,
                gender_self,
                gender_preference,
                place_types,
                course_ids,
                under_18,
            )
        )
    except ValueError as exc:
        return RedirectResponse(f"/profile?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/profile?saved=1", status_code=303)


@router.get("/api/profile/me")
def api_public_profile():
    return profile_core.public_profile()


@router.get("/api/profile/me/private")
def api_private_profile():
    return profile_core.private_profile()


@router.put("/api/profile/me")
def api_update_profile(body: dict = Body(...)):
    try:
        return profile_core.update_profile(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
