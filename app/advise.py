"""Advise: one model call that chooses among the time-filtered candidates.

The model sees SYSTEM_PROMPT and build_matching_message(); its answer must pass
validate_match(). One retry carries the error list; a second failure ends Advise
and the page shows the manual pick. The model has no tools other than submit_match.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from app.matching_prompt import PROPOSAL_SCHEMA, SYSTEM_PROMPT, build_matching_message, validate_match

# Requested sampling temperature. Omnara's agent config has no temperature field yet,
# so OmnaraAdviseClient cannot pass it on; a client that can must use it.
ADVISE_TEMPERATURE = 0.2
DEADLINE_SECONDS = 20
SETTINGS = ("OMNARA_TOKEN", "OMNARA_ORG", "OMNARA_PROJECT", "OMNARA_MODEL_PROVIDER", "OMNARA_MODEL")


class AdviseUnavailable(Exception):
    """No model can be called: not configured. Advise stops without a retry."""


class AdviseError(Exception):
    """One model call failed. It counts as an attempt and is retried once."""


class ModelClient(Protocol):
    def complete(self, system: str, user: str, schema: dict, temperature: float) -> dict | str: ...


@dataclass
class Advice:
    output: dict | None
    errors: list[str] = field(default_factory=list)
    ranking: list[dict] = field(default_factory=list)
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.output is not None and not self.errors


def parse_output(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _ranking(parsed: dict | None, candidates: list[dict]) -> list[dict]:
    """The model's ranking, kept for the manual pick even when the answer failed validation."""
    ids = {cand["id"] for cand in candidates}
    rows = parsed.get("ranking") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("id") in ids]


def advise(request: dict, requester: dict, candidates: list[dict], client: ModelClient) -> Advice:
    message = build_matching_message(request, requester, candidates)
    user = message
    parsed = None
    errors: list[str] = []
    for attempt in (1, 2):
        try:
            raw = client.complete(SYSTEM_PROMPT, user, PROPOSAL_SCHEMA, ADVISE_TEMPERATURE)
            current = parse_output(raw)
            errors = ["output is not a JSON object"] if current is None else validate_match(current, request, candidates)
            parsed = current or parsed
        except AdviseUnavailable as exc:
            return Advice(None, [str(exc)], _ranking(parsed, candidates), attempt)
        except AdviseError as exc:
            errors = [str(exc)]
        if not errors:
            return Advice(parsed, [], _ranking(parsed, candidates), attempt)
        user = message + "\n\nYour previous answer was rejected for these reasons. Answer again and fix them:\n- " + "\n- ".join(errors)
    return Advice(None, errors, _ranking(parsed, candidates), 2)


def _settings() -> dict[str, str]:
    values = {}
    path = Path(__file__).resolve().parent.parent / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    values.update({key: os.environ[key] for key in SETTINGS if os.environ.get(key)})
    if not all(values.get(key) for key in SETTINGS):
        raise AdviseUnavailable("The matching assistant is not configured.")
    return values


class OmnaraAdviseClient:
    """One short-lived Omnara agent per call: SYSTEM_PROMPT as its instruction and one
    custom tool, submit_match, whose input_schema is PROPOSAL_SCHEMA."""

    def __init__(self, values: dict | None = None, client: httpx.Client | None = None, clock=time.monotonic, sleep=time.sleep):
        self.values = values
        self.client = client
        self.clock, self.sleep = clock, sleep

    def complete(self, system: str, user: str, schema: dict, temperature: float) -> dict | str:
        values = self.values or _settings()
        client = self.client or httpx.Client(follow_redirects=False)
        base = f"https://api.omnara.com/v1/orgs/{values['OMNARA_ORG']}/projects/{values['OMNARA_PROJECT']}/agents"
        headers = {"Authorization": f"Bearer {values['OMNARA_TOKEN']}", "Accept": "application/json"}
        deadline = self.clock() + DEADLINE_SECONDS

        def call(method: str, url: str, body: dict | None = None) -> dict:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise AdviseError("the model did not answer in time")
            try:
                response = client.request(method, url, json=body, headers=headers, timeout=min(5, remaining))
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError):
                raise AdviseError("the model service could not be reached") from None
            if not isinstance(data, dict):
                raise AdviseError("the model service returned an unreadable response")
            return data

        config = {
            "version": "v1",
            "instruction": system,
            "model": {
                "provider_config": values["OMNARA_MODEL_PROVIDER"],
                "name": values["OMNARA_MODEL"],
                "default_max_output_tokens": 2048,
            },
            "tools": {
                "submit_match": {
                    "type": "custom",
                    "description": "Submit the matching decision. Call it exactly once.",
                    "input_schema": schema,
                }
            },
        }
        agent = ""
        try:
            created = call("POST", base, {
                "name": "StudyMate advise",
                "config_source": {"source": json.dumps(config), "source_format": "json"},
                "message": user,
            })
            agent = created.get("agent", {}).get("id", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", agent):
                raise AdviseError("the model service did not start an agent")
            cursor = 0
            while self.clock() < deadline:
                ready = call("GET", f"{base}/{agent}/tool-calls?state=ready&type=custom")
                for item in ready.get("data", []):
                    if item.get("name") == "submit_match" and isinstance(item.get("input"), dict):
                        call("POST", f"{base}/{agent}/tool-calls/{item['id']}/result", {
                            "outcome": "succeeded", "content_blocks": [{"type": "text", "text": "received"}]})
                        return item["input"]
                page = call("GET", f"{base}/{agent}/events?after_sequence={cursor}&limit=100")
                for item in page.get("data", []):
                    cursor = max(cursor, int(item.get("sequence", 0)))
                    if item.get("event_kind") == "model_output" and item.get("stop_reason") == "end_turn":
                        text = "\n".join(b.get("text", "") for b in item.get("content_blocks", []) if b.get("type") == "text")
                        if text.strip():
                            return text
                if not page.get("has_more"):
                    self.sleep(min(0.5, max(0, deadline - self.clock())))
            raise AdviseError("the model did not answer in time")
        finally:
            if agent:
                try:
                    client.request("POST", f"{base}/{agent}/cancel", headers=headers, timeout=2)
                except httpx.HTTPError:
                    pass
            if self.client is None:
                client.close()
