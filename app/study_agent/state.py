"""Persistent request state with optimistic concurrency, separate from team sessions."""
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: str | None = Field(default=None, max_length=80)
    window_start: str | None = Field(default=None, max_length=40)
    window_end: str | None = Field(default=None, max_length=40)
    duration_minutes: int | None = Field(default=None, ge=15, le=240)
    place_type: Literal["coffee", "library", "outdoor"] | None = None
    avoid: list[Literal["coffee", "library", "outdoor"]] = Field(default_factory=list, max_length=3)
    allowed_alternatives: list[Literal["coffee", "library", "outdoor"]] = Field(default_factory=list, max_length=3)


class StaleRun(Exception):
    pass


class RunRepository:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("STUDYMATE_AGENT_DB", "data/agent-synthetic.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS smagent_runs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS smagent_calls (
                agent TEXT NOT NULL, call_id TEXT NOT NULL, run_id TEXT NOT NULL, owner TEXT NOT NULL,
                signature TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                PRIMARY KEY(agent,call_id))""")

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def create(self, owner, sentence, mode, goal=None):
        run = {"id": uuid4().hex, "owner": owner, "version": 0, "revision": 0,
               "mode": mode, "state": "planning", "input": sentence[:1000],
               "goal": Goal.model_validate(goal or {}).model_dump(), "events": [],
               "proposal": None, "approved_revision": None, "question": "", "notice": "",
               "cloud_id": None, "cursor": 0, "processed": [], "demo_block": None}
        run.update(planning_attempts=0, clarifications=0)
        run.update(budget={"tool_rounds":0,"model_calls":0,"elapsed_seconds":0,"counts":{},"queries":[]},
                   tool_evidence={}, pending_input=None, rejected_proposals=0)
        with self.connect() as db:
            db.execute("INSERT INTO smagent_runs VALUES (?, ?, ?, ?)",
                       (run["id"], owner, 0, json.dumps(run)))
        return run

    def get(self, run_id, owner):
        with self.connect() as db:
            row = db.execute("SELECT body FROM smagent_runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
        if row is None:
            raise KeyError("REQUEST_NOT_FOUND")
        return json.loads(row[0])

    def save(self, run):
        expected = run["version"]
        updated = {**run, "version": expected + 1}
        with self.connect() as db:
            changed = db.execute("UPDATE smagent_runs SET version=?, body=? WHERE id=? AND owner=? AND version=?",
                                 (updated["version"], json.dumps(updated), run["id"], run["owner"], expected)).rowcount
            if changed != 1:
                raise StaleRun("REQUEST_CHANGED")
        run.update(updated)

    def claim_call(self, run, call_id, signature):
        """Durable receipt before execution. An interrupted read is failed, never rerun silently."""
        with self.connect() as db:
            inserted = db.execute("INSERT OR IGNORE INTO smagent_calls VALUES (?,?,?,?,?,'executing',NULL)",
                (run["cloud_id"],call_id,run["id"],run["owner"],signature)).rowcount
            row = db.execute("SELECT run_id,owner,signature,status,result FROM smagent_calls WHERE agent=? AND call_id=?",
                (run["cloud_id"],call_id)).fetchone()
        if row[:3] != (run["id"],run["owner"],signature):
            raise ValueError("TOOL_MAPPING_MISMATCH")
        return bool(inserted), row[3], json.loads(row[4]) if row[4] else None

    def record_call(self, run, call_id, result, status="executed"):
        with self.connect() as db:
            db.execute("UPDATE smagent_calls SET status=?, result=? WHERE agent=? AND call_id=? AND run_id=? AND owner=?",
                (status,json.dumps(result),run["cloud_id"],call_id,run["id"],run["owner"]))


def event(run, kind, summary, **metadata):
    run["events"].append({"kind": kind, "summary": summary, "revision": run["revision"], **metadata})
    run["events"] = run["events"][-100:]
