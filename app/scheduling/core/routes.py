"""Mountable owner router; the host supplies its authenticated actor dependency."""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from .schemas import (Actor, AvailabilitySnapshot, Exam, ExamInput, ExceptionInput,
                      Horizon, OwnerConflictDecision, OwnerScheduleView,
                      ScheduleException, SlotCheck, WeeklyRule, WeeklyRuleInput)
from .service import ScheduleService


def create_router(service: ScheduleService, actor_dependency) -> APIRouter:
    router = APIRouter(prefix="/calendar/me", tags=["calendar-owner"])
    actor_dep = Depends(actor_dependency)

    def horizon_query(start: str, end: str):
        try:
            return Horizon(start=start, end=end)
        except ValidationError:
            raise HTTPException(422, "INVALID_HORIZON") from None

    @router.get("/availability", response_model=AvailabilitySnapshot)
    def availability(start: str, end: str, duration_minutes: Annotated[int, Query(gt=0)] = 1,
                     actor: Actor = actor_dep):
        try:
            return service.effective_availability(actor, actor.subject_id, horizon_query(start, end), duration_minutes)
        except ValueError:
            raise HTTPException(422, "INVALID_DURATION_OR_HORIZON") from None

    @router.get("/schedule", response_model=OwnerScheduleView)
    def schedule(start: str, end: str, actor: Actor = actor_dep):
        return service.owner_schedule(actor, horizon_query(start, end))

    @router.post("/check-slot", response_model=OwnerConflictDecision)
    def check_slot(body: SlotCheck, actor: Actor = actor_dep):
        return service.check_slot(actor, body.slot, body.horizon)

    def register(kind, input_model, output_model):
        def list_items(actor: Actor = actor_dep):
            return service.repository.list_records(actor.subject_id, kind)

        # Explicit annotations are assigned before route registration so OpenAPI and
        # runtime validation both use the correct per-resource input/output model.
        def create_item(body, actor: Actor = actor_dep):
            return service.repository.create_record(actor.subject_id, kind, body)
        create_item.__annotations__["body"] = input_model

        def patch_item(record_id: str, body: dict, actor: Actor = actor_dep):
            try:
                result = service.repository.patch_record(actor.subject_id, kind, record_id, body)
            except ValidationError:
                raise HTTPException(422, "INVALID_SCHEDULE_INPUT") from None
            if result is None:
                raise HTTPException(404, "RECORD_NOT_FOUND")
            return result

        def delete_item(record_id: str, actor: Actor = actor_dep):
            if not service.repository.delete_record(actor.subject_id, kind, record_id):
                raise HTTPException(404, "RECORD_NOT_FOUND")
            return Response(status_code=204)

        router.add_api_route(f"/{kind}", list_items, methods=["GET"], response_model=list[output_model], name=f"list-{kind}")
        router.add_api_route(f"/{kind}", create_item, methods=["POST"], response_model=output_model, status_code=201, name=f"create-{kind}")
        router.add_api_route(f"/{kind}/{{record_id}}", patch_item, methods=["PATCH"], response_model=output_model, name=f"patch-{kind}")
        router.add_api_route(f"/{kind}/{{record_id}}", delete_item, methods=["DELETE"], status_code=204, name=f"delete-{kind}")

    register("weekly-rules", WeeklyRuleInput, WeeklyRule)
    register("exceptions", ExceptionInput, ScheduleException)
    register("exams", ExamInput, Exam)
    return router
