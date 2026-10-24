"""Compatibility interpreter: isolated request, dynamic date, no shared agent history."""
from app import store
from app.study_agent.planner import OmnaraPlanner, PlannerError, demo_extract
from app.study_agent.state import RunRepository
from app.study_agent.tools import checked_goal

OmnaraError = PlannerError


def interpret_request(sentence: str, courses: list[dict]) -> dict:
    repository = RunRepository()
    run = repository.create(store.OWNER_ID,sentence,"omnara",checked_goal(demo_extract(sentence,{})))
    planner = OmnaraPlanner()
    planner.repository = repository
    planner.interpret_only = True
    decision = planner.run(run,lambda *_:{"error":"INTERPRET_HAS_NO_TOOL_PERMISSION"})
    repository.save(run)
    goal = decision.get("requirements",run["goal"])
    return {"course_id":goal.get("course_id"),"starts_at":goal.get("window_start"),
            "ends_at":goal.get("window_end"),"place_type":goal.get("place_type") or "coffee","note":""}
