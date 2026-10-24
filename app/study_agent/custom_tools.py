"""The four external read capabilities. Helpers/validation are never model tools."""
import re
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app import matching_core, profile_core, schedule_core, store
from app.scheduling.time import parse_instant
from .tools import StudyTools, digest, missing, profile_brief, schema


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Empty(Arguments):
    pass


class MatchArguments(Arguments):
    exclude_candidate_ids: list[str] = Field(default_factory=list, max_length=5)
    not_before: str | None = Field(default=None,max_length=40)


class PlaceArguments(Arguments):
    candidate_ids: list[str] = Field(min_length=1, max_length=3)
    starts_at: str = Field(max_length=40)
    place_type: Literal["coffee", "library", "outdoor"] | None = None


class BookingArguments(Arguments):
    place_id: str = Field(min_length=1, max_length=64)


class SafeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileOutput(SafeOutput):
    course_ids: list[str]
    study_style: Literal["quiet","talk","drill"]
    group_size: Literal[2,3,4]
    place_types: list[Literal["coffee","library","outdoor"]]


class SafeSlot(SafeOutput):
    start: str
    end: str


class DataVersion(SafeOutput):
    schedule: int
    calendar: int | None
    verification: str


class CandidateOutput(SafeOutput):
    id: str
    first_name: str
    year: int
    major: str
    shared_course: str
    score: float
    score_parts: dict[str,float]
    slots: list[SafeSlot]
    data_version: DataVersion


class MatchOutput(SafeOutput):
    candidates: list[CandidateOutput]
    group_size: Literal[2,3,4]
    duration_minutes: int
    source: Literal["saved_sanitized_schedules"]
    status: Literal["ok","no_match"]
    request_revision: int
    request_version: str
    alternatives: list["AlternativeOutput"] = Field(default_factory=list)
    next: str | None = None


class AlternativeOutput(SafeOutput):
    candidate_id: str
    first_name: str
    starts_at: str
    ends_at: str
    duration_minutes: int
    requires_user_permission: Literal[True]


class PlaceOutput(SafeOutput):
    id: str
    name: str
    address: str
    kind: Literal["coffee","library","outdoor"]
    hours: str
    rating: str
    map_url: str
    walk_minutes: int
    source: Literal["seeded_fixture"]
    live_verified: Literal[False]
    hours_verification: Literal["synthetic","unknown"]


class PlacesOutput(SafeOutput):
    status: Literal["ok","no_feasible_place"]
    places: list[PlaceOutput]
    hours_verification: Literal["synthetic","unknown"]
    source: Literal["seeded_fixture"]
    live_verified: Literal[False]
    request_version: str
    notice: str
    next: str


class BookingOutput(SafeOutput):
    status: Literal["unsupported"]
    place_id: str
    reserved: Literal[False]
    reason: str


OUTPUT_MODELS = {"get_profile":ProfileOutput,"find_matches":MatchOutput,
                 "suggest_places":PlacesOutput,"check_booking":BookingOutput}


INPUT_MODELS = {"get_profile":Empty, "find_matches":MatchArguments,
                "suggest_places":PlaceArguments, "check_booking":BookingArguments}
LIMITS = {"get_profile":1, "find_matches":2, "suggest_places":2, "check_booking":1}
TIMEOUTS = {"get_profile":1, "find_matches":2, "suggest_places":1, "check_booking":1}
DESCRIPTIONS = {
    "get_profile":"Read the current authenticated student's allowed course and study preferences. No target user parameter; no private fields.",
    "find_matches":"Read David's deterministic top five candidates, scores, full-duration slots and versions inside the saved request. Second call must change exclusions using previously returned IDs; never broadens constraints. No-match is a normal result; stale calendars are unknown.",
    "suggest_places":"Read synthetic seed places for a scored group and a full-duration slot. All radii, opening hours and allowed types are hard filters. Alternative type requires prior student permission. No live Places data; no_feasible_place is normal. Private coordinates stay server-side.",
    "check_booking":"Read booking capability for a place returned by this request. Always unsupported in v1; never reserves anything."}
CUSTOM_TOOLS = {name:{"type":"custom", "description":DESCRIPTIONS[name],
    "permission":{"mode":"always_allow"}, "input_schema":model.model_json_schema()}
    for name,model in INPUT_MODELS.items()}


def safe_label(value):
    # Student-controlled names/majors are data; never echo contact info/instructions.
    value = str(value)
    return value if re.fullmatch(r"[\w .'-]{1,64}", value) and "@" not in value else "Student"


class CustomStudyTools(StudyTools):
    def __init__(self, run):
        if run.get("owner") != store.OWNER_ID:
            raise ValueError("SYNTHETIC_OWNER_ONLY: an isolated production profile adapter is required")
        super().__init__(run)
        evidence = run.get("tool_evidence", {})
        self.candidates = evidence.get("candidates", [])
        self.places = evidence.get("places", [])
        self.selections = evidence.get("selections", {})
        self.last_match = evidence.get("last_match")

    def remember(self):
        self.run["tool_evidence"] = {"candidates":self.candidates,"places":self.places,
            "selections":self.selections,"last_match":self.last_match}

    def call(self, name, args):
        if name not in INPUT_MODELS or not isinstance(args, dict):
            raise ValueError("TOOL_NOT_ALLOWED")
        values = INPUT_MODELS[name].model_validate(args).model_dump()
        if missing(self.goal):
            raise ValueError("CLARIFICATION_REQUIRED")
        # Explicit registry; no import/eval/getattr dispatch.
        registry = {"get_profile":self.get_profile, "find_matches":self.find_matches,
                    "suggest_places":self.suggest_places, "check_booking":self.check_booking}
        output = OUTPUT_MODELS[name].model_validate(registry[name](**values)).model_dump(exclude_none=True)
        self.remember()
        return output

    def find_matches(self, exclude_candidate_ids=None, not_before=None):
        excludes = exclude_candidate_ids or []
        if not set(excludes) <= {c["id"] for c in self.candidates}:
            raise ValueError("UNKNOWN_EXCLUSION")
        result = super().find_matches()
        result["candidates"] = self.candidates = [c for c in self.candidates if c["id"] not in excludes]
        self.selections, self.places = {}, []
        if not_before:
            lower = parse_instant(not_before)
            if not parse_instant(self.goal["window_start"]) <= lower < parse_instant(self.goal["window_end"]):
                raise ValueError("QUERY_OUTSIDE_REQUEST")
            for c in self.candidates:
                c["slots"] = [{"start":max(lower,parse_instant(s["start"])).isoformat(),"end":s["end"]}
                    for s in c["slots"] if parse_instant(s["end"])-max(lower,parse_instant(s["start"])) >= timedelta(minutes=self.goal["duration_minutes"])]
            self.candidates = result["candidates"] = [c for c in self.candidates if c["slots"]]
        for c in self.candidates:
            c["first_name"] = safe_label(c["first_name"])
            p = profile_core.public_profile_of(c["id"])
            c.update(year=p.year, major=safe_label(p.major), shared_course=self.goal["course_id"])
            snapshot = schedule_core.effective_availability(c["id"],self.goal["window_start"],self.goal["window_end"],self.goal["duration_minutes"])
            c["data_version"] = {"schedule":snapshot.schedule_version,"calendar":snapshot.calendar.snapshot_version,
                                 "verification":snapshot.calendar.verification_level}
        # Alternatives outside the window are suggestions requiring a new user decision.
        result.update(status="ok" if self.candidates else "no_match", request_revision=self.run["revision"],
                      request_version=digest(self.goal), alternatives=[{**a,"first_name":safe_label(a["first_name"])} for a in getattr(self,"nearest_alternatives",[])])
        if not self.candidates:
            result["next"] = "No full-duration common slot in the authorized window. Ask before changing the window or duration."
        self.last_match = result
        return result

    def selection(self, candidate_ids, starts_at):
        start, end = super().selection(candidate_ids, starts_at)
        # Being scored somewhere in the window is insufficient: this exact slot must be evidenced.
        for sid in candidate_ids:
            c = next(c for c in self.candidates if c["id"] == sid)
            if not any(parse_instant(s["start"]) <= start and parse_instant(s["end"]) >= end for s in c["slots"]):
                raise ValueError("SLOT_NOT_IN_SCORE_RESULT")
        return start,end

    def suggest_places(self, candidate_ids, starts_at, place_type=None):
        start,end = self.selection(candidate_ids,starts_at)
        chosen = place_type or self.goal["place_type"] or profile_brief()["place_types"][0]
        permitted = {self.goal["place_type"] or profile_brief()["place_types"][0],*self.goal.get("allowed_alternatives",[])}
        if chosen not in permitted or chosen in self.goal["avoid"]:
            raise ValueError("USER_PERMISSION_REQUIRED_FOR_PLACE_TYPE")
        profiles = [profile_core.public_profile(),*(profile_core.public_profile_of(s) for s in candidate_ids)]
        options = matching_core.suggest_places(chosen,profiles,start,end,set(self.goal["avoid"]),profiles[0].place_types)
        rows = []
        for p in options:
            known = bool(p.get("opens") and p.get("closes"))
            if not p["inside"] or not p["open"] or not known or p["kind"] != chosen:
                continue
            row = {"id":digest(p["name"])[:16],**{k:p[k] for k in ("name","address","kind","hours","rating","map_url","walk_minutes")},
                   "source":"seeded_fixture","live_verified":False,"hours_verification":"synthetic"}
            rows.append(row)
            self.selections[digest([candidate_ids,start.isoformat(),row["id"]])] = row
        self.places = list({p["id"]:p for p in [*self.places,*rows]}.values())
        return {"status":"ok" if rows else "no_feasible_place", "places":rows,
                "hours_verification":"synthetic" if all(p.get("opens") and p.get("closes") for p in options) else "unknown",
                "source":"seeded_fixture", "live_verified":False,
                "request_version":digest(self.goal), "notice":"Synthetic hours and ratings; no live Places lookup.",
                "next":"Choose only a previously allowed alternative type; otherwise ask the student before changing constraints." if not rows else "Validate the final selection on the backend."}

    def check_booking(self, place_id):
        if place_id not in {p["id"] for p in self.places}:
            raise ValueError("PLACE_NOT_FROM_REQUEST")
        return {"status":"unsupported","place_id":place_id,"reserved":False,"reason":"No booking service exists in v1."}

    def validate_selection(self, candidate_ids, starts_at, place_id):
        """Independent validator; no model text is trusted or forwarded to executors."""
        start,_ = self.selection(candidate_ids,starts_at)
        key = digest([candidate_ids,start.isoformat(),place_id])
        prior = self.selections.get(key)
        if prior is None:
            raise ValueError("PLACE_NOT_VERIFIED_FOR_SELECTION")
        availability = self.check_availability(candidate_ids,starts_at)
        if not availability["available"]:
            return {"valid":False,"code":"AVAILABILITY_CHANGED"}
        saved = list(self.places)
        options = self.suggest_places(candidate_ids,starts_at,prior["kind"])["places"]
        current = next((p for p in options if p["id"]==place_id),None)
        if current != prior:
            return {"valid":False,"code":"PLACE_EVIDENCE_CHANGED"}
        # Build canonical invitation/reason with the existing server validator. Its place
        # selection must be restricted to the explicitly allowed alternative, too.
        original = self.run["goal"]
        try:
            self.run["goal"] = {**original,"place_type":prior["kind"]}
            proposal = super().validate_proposal(candidate_ids,starts_at,place_id)
        finally:
            self.run["goal"] = original
            self.places = saved
        if not proposal.get("valid"):
            return proposal
        # A refresh of identical busy content must not force an endless approve /
        # refresh loop. Bind to actual sanitized blocks + schedule/profile content;
        # still recheck freshness and record the new snapshot version on every gate.
        subjects = [store.OWNER_ID,*candidate_ids]
        content = [{"id":sid,"blocks":[b.model_dump() for b in schedule_core.list_busy_for_match(sid)],
                    "schedule_version":row["schedule_version"]}
                   for sid,row in zip(subjects,availability["evidence"])]
        profiles = [profile_core.public_profile(),*(profile_core.public_profile_of(s) for s in candidate_ids)]
        proposal["evidence_id"] = digest([original,self.run["revision"],candidate_ids,start.isoformat(),place_id,current,
                                          content,[p.model_dump() for p in profiles]])
        proposal.update(request_version=digest(original),request_revision=self.run["revision"],booking="unsupported")
        selected = [c for c in self.candidates if c["id"] in candidate_ids]
        ranked = sorted(self.candidates,key=lambda c:(-c["score"],c["id"]))
        differences = []
        for c in selected:
            others = [o for o in ranked if o["id"] not in candidate_ids]
            if others:
                other = others[0]
                edges = [k for k,v in c["score_parts"].items() if v > other["score_parts"].get(k,0)]
                differences.append({"selected_id":c["id"],"other_id":other["id"],"score_gap":round(c["score"]-other["score"],2),"stronger_factors":edges})
        proposal["comparison"] = differences
        proposal["reason"] += " " + ("Score evidence: "+"; ".join(f"{c['first_name']} {c['score']} ({', '.join(f'{k}={v}' for k,v in c['score_parts'].items())})" for c in selected)+".")
        if prior["kind"] != (original["place_type"] or profile_brief()["place_types"][0]):
            proposal["reason"] += " Uses your explicitly permitted alternative place type."
        # Final copy contains only canonical facts; arbitrary model draft/reason fields
        # are rejected by the decision schema before this validator is called.
        import json
        encoded = json.dumps(proposal)
        if re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",encoded):
            raise ValueError("PRIVATE_CONTACT_IN_PROPOSAL")
        private_address = store.owner().get("street_address","")
        if private_address and private_address in encoded:
            raise ValueError("PRIVATE_ADDRESS_IN_PROPOSAL")
        self.validated = proposal
        return proposal
