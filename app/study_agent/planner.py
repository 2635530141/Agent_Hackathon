"""Explicit local demo policy and a native Omnara custom-tool adapter."""
import json
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app import store
from app.scheduling.time import display_zone
from .state import event
from .tools import TOOL_SCHEMAS, group_options, missing, profile_brief, digest


class PlannerError(Exception):
    pass


def demo_extract(text, goal):
    """Deliberately small parser. Ambiguous dates/clocks are asked in the form."""
    updated = dict(goal)
    compact = re.sub(r"\s+", "", text.lower())
    for course in store.courses():
        if course["code"].lower().replace(" ", "") in compact:
            updated["course_id"] = course["id"]
    instants = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?", text)
    if len(instants) == 2:
        updated.update(window_start=instants[0], window_end=instants[1])
    duration = re.search(r"\b(\d{2,3})\s*(?:minutes?|mins?)\b", text, re.I)
    if duration:
        updated["duration_minutes"] = int(duration[1])
    # This tiny parser establishes explicit permissions; the model cannot grant them.
    if re.search(r"(?:or|alternatively|也可以|或者)\s*(?:a )?library|图书馆也可以", text, re.I):
        updated["allowed_alternatives"] = ["library"]
    if re.search(r"not (?:a )?library|no librar|不要图书馆|不去图书馆", text, re.I):
        updated.update(place_type="coffee", avoid=["library"])
    elif re.search(r"coffee|cafe|咖啡", text, re.I):
        updated["place_type"] = "coffee"
    elif re.search(r"library|图书馆", text, re.I):
        updated["place_type"] = "library"
    elif re.search(r"coffee|cafe|咖啡", text, re.I):
        updated["place_type"] = "coffee"
    elif re.search(r"outdoor|户外", text, re.I):
        updated["place_type"] = "outdoor"
    return updated


class DemoPlanner:
    """Rule policy, NOT a model. Uses the same tools and validation as live mode."""
    def run(self, run, call):
        if missing(run["goal"]):
            return {"action": "clarify"}
        call("get_profile", {})
        result = call("find_matches", {})
        for ids, start in group_options(result["candidates"], result["group_size"], result["duration_minutes"]):
            args = {"candidate_ids": ids, "starts_at": start}
            if not call("check_availability", args)["available"]:
                continue
            places = call("suggest_places", args)["places"]
            if not places:
                continue
            validation = call("validate_proposal", {**args, "place_id": places[0]["id"]})
            if validation["valid"]:
                return {"action": "propose", "evidence_id": validation["evidence_id"]}
        return {"action": "no_match"}


class BasicPlanner:
    """Explicit deterministic fallback; selects from the same four read tools."""
    def run(self, run, call):
        if missing(run["goal"]):
            return {"action":"clarify"}
        if not run["budget"]["counts"].get("get_profile"):
            call("get_profile",{})
        prior = run.get("repair_reason",{})
        args = {"not_before":prior["previous_end"]} if prior.get("previous_end") else {}
        result = call("find_matches",args)
        options = list(group_options(result["candidates"],result["group_size"],result["duration_minutes"]))
        if not options:
            return {"action":"no_match"}
        ids,start = options[0]
        g = run["goal"]
        types = [g["place_type"] or profile_brief()["place_types"][0],*g.get("allowed_alternatives",[])]
        for kind in dict.fromkeys(types):
            places = call("suggest_places",{"candidate_ids":ids,"starts_at":start,"place_type":kind})["places"]
            if places:
                return {"action":"propose","candidate_ids":ids,"starts_at":start,"place_id":places[0]["id"]}
        return {"action":"ask_change","change":"place_type"}


from .runner import INSTRUCTION


def settings():
    keys = ("OMNARA_TOKEN", "OMNARA_ORG", "OMNARA_PROJECT", "OMNARA_MODEL_PROVIDER", "OMNARA_MODEL")
    values = {k: os.getenv(k, "") for k in keys}
    if not all(values.values()):
        raise PlannerError("Omnara tool mode is not configured. Select Local rule demo or configure the server.")
    for key in ("OMNARA_ORG", "OMNARA_PROJECT"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", values[key]):
            raise PlannerError("Invalid Omnara project configuration.")
    return values


class OmnaraPlanner:
    def __init__(self, values=None, client=None, clock=time.monotonic, sleep=time.sleep):
        self.values = values or settings()
        self.client = client or httpx.Client(follow_redirects=False)
        self.owns_client = client is None
        self.clock, self.sleep = clock, sleep
        self.is_cancelled = lambda: False

    def run(self, run, call):
        from .runner import drive
        return drive(self, run, call)

    def stop(self, agent_id):
        """Cancel current remote work; never archive/delete external resources."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",agent_id):
            raise PlannerError("Invalid isolated agent id.")
        base = f"https://api.omnara.com/v1/orgs/{self.values['OMNARA_ORG']}/projects/{self.values['OMNARA_PROJECT']}/agents"
        try:
            response = self.client.post(f"{base}/{agent_id}/cancel",json={},
                headers={"Authorization":f"Bearer {self.values['OMNARA_TOKEN']}"},timeout=2)
            response.raise_for_status()
        except httpx.HTTPError:
            raise PlannerError("Remote stop could not be confirmed.") from None
        finally:
            if self.owns_client:
                self.client.close()
