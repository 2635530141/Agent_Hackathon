"""Small synthetic-data smoke. --live explicitly enables bounded Omnara HTTP."""
import argparse
import json
from uuid import uuid4

from app import store
from .planner import PlannerError, settings
from .service import AgentService


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--start",default="2026-11-05T18:00")
    parser.add_argument("--end",default="2026-11-05T21:00")
    args=parser.parse_args()
    if args.live:
        try:
            settings()
        except PlannerError:
            print(json.dumps({"status":"not_configured","live_model_verified":False}))
            return 2
    service=AgentService()
    service.refresh_synthetic()
    run=service.create(store.OWNER_ID,"Synthetic CS 61A smoke test",mode="omnara" if args.live else "basic",
        goal={"course_id":"cs61a","window_start":args.start,"window_end":args.end,
              "duration_minutes":90,"place_type":"library"})
    run=service.act(run["id"],run["owner"],run["version"],uuid4().hex,"retry")
    print(json.dumps({"status":run["state"],"mode":run["mode"],"business_data":"synthetic",
        "tools":[e["tool"] for e in run["events"] if e["kind"]=="tool"],"budget":run["budget"],
        "live_model_verified":bool(args.live and run["state"]=="proposed"),"google_operations":0}))
    return 0 if run["state"]=="proposed" else 1


if __name__=="__main__":
    raise SystemExit(main())
