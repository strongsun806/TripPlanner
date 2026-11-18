"""여행 일정 라우터 (F3015 · F3016) — 1일차 step5 CRUD 를 여행 일정용으로 바꿨다

    POST   /api/plans              저장  → 201 {plan, overlaps}
    GET    /api/plans?visit_date=  목록  → 200 (날짜 · 시작 시각 순)
    GET    /api/plans/{plan_id}    상세  → 200 | 404
    PUT    /api/plans/{plan_id}    수정  → 200 {plan, overlaps} | 404
    DELETE /api/plans/{plan_id}    삭제  → 204 | 404
"""
from datetime import date

from fastapi import APIRouter, HTTPException

from .. import store
from ..schemas import PlanIn, PlanOut, PlanSaved

router = APIRouter(prefix="/api/plans", tags=["plans"])
NOT_FOUND = "그런 여행 일정이 없습니다."


@router.post("", response_model=PlanSaved, status_code=201, summary="여행 일정 저장")
def create_plan(plan: PlanIn):
    # 겹치는 일정을 먼저 찾고(자기 자신이 섞이지 않게), 저장은 막지 않는다
    found = store.overlaps(plan.visit_date, plan.start_time, plan.end_time)
    saved = store.create(plan.model_dump())
    return {"plan": saved, "overlaps": found}


@router.get("", response_model=list[PlanOut], summary="여행 일정 목록")
def list_plans(visit_date: date | None = None):
    return store.list_all(str(visit_date) if visit_date else None)


@router.get("/{plan_id}", response_model=PlanOut, summary="여행 일정 상세")
def get_plan(plan_id: int):
    plan = store.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return plan


@router.put("/{plan_id}", response_model=PlanSaved, summary="여행 일정 수정")
def update_plan(plan_id: int, plan: PlanIn):
    updated = store.update(plan_id, plan.model_dump())
    if updated is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    found = store.overlaps(plan.visit_date, plan.start_time, plan.end_time, exclude_id=plan_id)
    return {"plan": updated, "overlaps": found}


@router.delete("/{plan_id}", status_code=204, summary="여행 일정 삭제")
def delete_plan(plan_id: int):
    if not store.delete(plan_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
