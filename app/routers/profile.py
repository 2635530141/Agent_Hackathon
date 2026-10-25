from urllib.parse import quote

from fastapi import APIRouter, Body, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import profile_core
from app.web import render

router = APIRouter()


@router.get("/profile")
def profile_page(request: Request, error: str = "", saved: int = 0):
    profile = profile_core.private_profile()
    return render(
        request,
        "profile.html",
        nav="profile",
        profile=profile,
        course_text=profile_core.course_codes(profile.course_ids),
        anchor_map=profile_core.maps_url(profile.anchor_lat, profile.anchor_lng),
        style_labels={key: profile_core.STYLE_LABELS[key] for key in profile_core.STUDY_STYLES},
        place_labels=profile_core.PLACE_LABELS,
        gender_labels=profile_core.GENDER_LABELS,
        group_sizes=list(profile_core.GROUP_SIZES),
        error=error,
        saved=bool(saved),
    )


@router.post("/profile")
def profile_save(
    study_styles: list[str] | None = Form(None),
    study_style_note: str = Form(""),
    street_address: str = Form(""),
    radius_meters: str = Form(...),
    group_min: str = Form(...),
    group_max: str = Form(...),
    place_types: list[str] | None = Form(None),
    place_note: str = Form(""),
    major: str = Form(...),
    year: str = Form(...),
    courses: str = Form(""),
    gender_self: str = Form(""),
    gender_preference: list[str] | None = Form(None),
    under_18: str = Form(""),
):
    try:
        profile_core.update_profile(
            {
                "study_styles": study_styles or [],
                "study_style_note": study_style_note,
                "street_address": street_address,
                "radius_meters": radius_meters,
                "group_min": group_min,
                "group_max": group_max,
                "place_types": place_types or [],
                "place_note": place_note,
                "major": major,
                "year": year,
                "courses": courses,
                "gender_self": gender_self,
                "gender_preference": gender_preference or [],
                "under_18": under_18 == "1",
            }
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
