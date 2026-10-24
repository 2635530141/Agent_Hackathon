"""Clarify -> bounded tools -> grounded proposal -> recheck/replan, with no sends."""
import time
from uuid import uuid4

from app import store
from app.scheduling.core.schemas import Actor, ExceptionInput
from app.scheduling.demo import FIXTURE_HORIZON
from app.scheduling.runtime import get_runtime
from .planner import DemoPlanner, OmnaraPlanner, PlannerError, demo_extract
from .state import RunRepository, StaleRun, event
from .tools import StudyTools, checked_goal, missing


class Cancelled(Exception):
    pass


class AgentService:
    def __init__(self, repository=None, planner_factory=None):
        self.repository = repository or RunRepository()
        self.planner_factory = planner_factory or (lambda mode: DemoPlanner() if mode == "demo" else OmnaraPlanner())

    def create(self, owner, sentence, mode="demo", goal=None):
        if mode not in {"demo", "omnara"}:
            raise ValueError("Choose Local rule demo or Omnara.")
        if not sentence.strip() or len(sentence) > 1000:
            raise ValueError("Describe the study request in 1 to 1000 characters.")
        raw = goal or {}
        if mode == "demo":
            raw = demo_extract(sentence, raw)
        run = self.repository.create(owner, sentence, mode, checked_goal(raw))
        run["state"] = "ready"
        event(run, "created", "Request saved. No invitations or calendar writes are available.")
        self.repository.save(run)
        return run

    def refresh_synthetic(self):
        # Explicit demo action only, outside the model's capabilities. No Google I/O.
        runtime = get_runtime()
        for sid in [store.OWNER_ID, *[p["id"] for p in store.others()]]:
            runtime.service.refresh_calendar(Actor(subject_id=sid), FIXTURE_HORIZON)

    def advance(self, run):
        run.update(state="planning", question="", attempt_id=uuid4().hex, planning_deadline=time.time()+30)
        run["revision"] += 1
        event(run, "planning", "Planning against saved schedules and the current requirements.")
        self.repository.save(run)
        tools, started, calls, counted = StudyTools(run), time.monotonic(), 0, False

        def call(name, args):
            nonlocal calls, counted
            current = self.repository.get(run["id"], run["owner"])
            if current["state"] == "cancelled" or current.get("attempt_id") != run["attempt_id"]:
                raise Cancelled()
            calls += 1
            if calls > 6 or time.monotonic()-started > 20:
                raise PlannerError("Planning budget reached. Edit the requirements or retry explicitly.")
            if not counted and name != "set_requirements" and not missing(run["goal"]):
                counted = True
                run["planning_attempts"] = run.get("planning_attempts",0)+1
                if run["planning_attempts"] > 4:
                    raise PlannerError("Request planning limit reached. Start a new request.")
            before = time.monotonic()
            try:
                result = tools.call(name, args)
                outcome = "ok"
            except (ValueError, TypeError, KeyError):
                # Return an error as evidence; never echo provider/student strings in errors.
                result, outcome = {"error":"INVALID_OR_STALE_TOOL_INPUT", "next":"Clarify or choose from current tool results."}, "rejected"
            event(run, "tool", f"{name}: {outcome}", tool=name, outcome=outcome,
                  elapsed_ms=round((time.monotonic()-before)*1000), result=result)
            self.repository.save(run)
            return result

        try:
            run["goal"] = checked_goal(run["goal"])
            if run.get("clarifications",0) >= 8:
                raise PlannerError("Clarification limit reached. Start a new request.")
            # Demo mode asks before any read tool. Omnara may first extract missing requirements.
            if run["mode"] == "demo" and missing(run["goal"]):
                decision = {"action":"clarify"}
            else:
                planner = self.planner_factory(run["mode"])
                if isinstance(planner, OmnaraPlanner):
                    planner.is_cancelled = lambda: self.repository.get(run["id"],run["owner"])["state"] == "cancelled"
                decision = planner.run(run, call)
            if not isinstance(decision, dict):
                raise PlannerError("Planner returned an invalid decision.")
            if missing(run["goal"]):
                run["clarifications"] = run.get("clarifications",0)+1
                run.update(state="clarifying", question=missing(run["goal"]))
                event(run, "clarification", run["question"])
            elif decision.get("action") == "propose":
                proposal = tools.validated
                if not proposal or decision.get("evidence_id") != proposal["evidence_id"]:
                    raise PlannerError("An unvalidated proposal was rejected. No person or place was invented.")
                run.update(state="proposed", proposal=proposal, approved_revision=None)
                event(run, "proposal", "Validated draft ready for review; approval is required for this revision.")
            elif decision.get("action") == "no_match":
                run.update(state="no_match", proposal=None, approved_revision=None,
                           question="No complete plan fits these requirements. Change the time window, duration or place preference.")
                event(run, "no_match", run["question"])
            else:
                raise PlannerError("Planner did not produce a grounded proposal or a missing-field question.")
        except (Cancelled, StaleRun):
            return self.repository.get(run["id"], run["owner"])
        except (PlannerError, ValueError, TypeError, KeyError) as exc:
            run.update(state="error", proposal=None, approved_revision=None,
                       notice=str(exc) if isinstance(exc, PlannerError) else "Planning could not complete. Check the requirements and retry.")
            event(run, "error", run["notice"])
        except Exception:
            run.update(state="error", proposal=None, approved_revision=None,
                       notice="An internal tool failed. No external action was taken; retry explicitly or edit the request.")
            event(run, "error", run["notice"])
        current = self.repository.get(run["id"], run["owner"])
        if current["state"] == "cancelled" or current.get("attempt_id") != run["attempt_id"]:
            return current
        event(run, "finished", f"Planning finished: {run['state']}.", elapsed_ms=round((time.monotonic()-started)*1000), tool_calls=calls)
        try:
            self.repository.save(run)
        except StaleRun:
            return self.repository.get(run["id"],run["owner"])
        return run

    def act(self, run_id, owner, expected, key, action, patch=None):
        run = self.repository.get(run_id, owner)
        if key in run["processed"]:
            return run
        recovering = action == "retry" and run["state"] == "planning" and run.get("planning_deadline",0) < time.time()
        if run["version"] != expected or (run["state"] == "planning" and not recovering):
            raise StaleRun("Request changed. Reload before acting.")
        if recovering:
            run["state"] = "error"
            event(run,"recovered","Previous planning lease expired. Explicit retry starts a fresh bounded attempt.")
        if run["state"] == "cancelled":
            raise ValueError("This request is cancelled; start another request.")
        if action == "answer":
            run["goal"] = checked_goal({**run["goal"], **(patch or {})})
            run.update(proposal=None, approved_revision=None, lock_goal=False, notice="Requirements updated; previous approval cleared.")
        elif action == "approve":
            old = run.get("proposal")
            if old is None or run["state"] not in {"proposed", "approved"}:
                raise ValueError("There is no current proposal to approve.")
            tools = StudyTools(run)
            try:
                tools.find_matches()
                current = tools.validate_proposal(old["candidate_ids"], old["starts_at"], old["place_id"])
            except (ValueError, TypeError, KeyError):
                current = {"valid":False}
            event(run, "recheck", "Re-read current scoring, full-duration availability and place evidence.")
            if current["valid"] and current["evidence_id"] == old["evidence_id"]:
                run.update(state="approved", approved_revision=run["revision"],
                           notice="This revision passed the approval recheck. No invitation was sent and no calendar was written.")
                run["processed"].append(key)
                event(run, "approved", "Approved this exact preview revision; delivery remains outside the agent.")
                self.repository.save(run)
                return run
            run.update(proposal=None, approved_revision=None, lock_goal=True,
                       notice="Availability or proposal evidence changed. The old plan and approval were invalidated; replanning within the original requirements.")
            event(run, "invalidated", run["notice"])
        elif action == "simulate":
            if run["mode"] != "demo" or not run.get("proposal") or run["demo_block"]:
                raise ValueError("A single simulated conflict requires a current local-demo proposal.")
            # Claim and retain cleanup information before changing synthetic data.
            proposal = run["proposal"]
            sid, record_id = proposal["candidate_ids"][0], f"agent-demo-{run['id']}"
            run["demo_block"] = {"subject": sid, "id":record_id}
            run.update(state="proposed", approved_revision=None,
                       notice="A simulated conflict was added. The previous approval is cleared; recheck this plan to trigger replanning.")
            run["processed"].append(key)
            self.repository.save(run)
            get_runtime().repository.create_record(sid, "exceptions", ExceptionInput(
                start=proposal["starts_at"], end=proposal["ends_at"], kind="unavailable"), record_id=record_id)
            event(run, "demo_conflict", "A clearly labeled synthetic busy block was added to the selected candidate. Approve/recheck to see replanning.")
            self.repository.save(run)
            return run
        elif action == "reset_conflict":
            block = run["demo_block"]
            if block:
                get_runtime().repository.delete_record(block["subject"], "exceptions", block["id"])
                run["demo_block"] = None
                run.update(proposal=None, approved_revision=None, lock_goal=True)
                event(run, "demo_reset", "Removed this request's simulated busy block; approval cleared.")
        elif action == "retry":
            if run["state"] not in {"ready", "error", "no_match", "clarifying"}:
                raise ValueError("Use recheck on an existing proposal.")
        else:
            raise ValueError("Unknown request action.")
        run["processed"] = [*run["processed"], key][-40:]
        return self.advance(run)

    def cancel(self, run_id, owner):
        run = self.repository.get(run_id, owner)
        if run["demo_block"]:
            block = run["demo_block"]
            get_runtime().repository.delete_record(block["subject"],"exceptions",block["id"])
            run["demo_block"] = None
        run.update(state="cancelled", proposal=None, approved_revision=None, notice="Request cancelled. No external action was taken.")
        event(run, "cancelled", run["notice"])
        self.repository.save(run)
        return run
