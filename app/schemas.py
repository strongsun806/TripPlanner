"""요청 · 응답 형식 (F3017). 라우터가 가져다 쓴다."""
from datetime import date, datetime, time

from pydantic import BaseModel, Field, model_validator


class PlanIn(BaseModel):
    """저장 · 수정할 때 받는 값 — 여행 일정 하나"""
    place_name: str = Field(min_length=1, max_length=100, examples=["오사카성"])
    city: str = Field(min_length=1, max_length=50, examples=["오사카"])
    visit_date: date = Field(examples=["2026-11-28"])
    start_time: time = Field(examples=["14:00"])
    end_time: time = Field(examples=["16:00"])
    lat: float | None = Field(default=None, ge=-90, le=90)      # Agent 가 저장할 때 채운다. 모르면 비워 둔다
    lon: float | None = Field(default=None, ge=-180, le=180)
    memo: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time은 start_time보다 늦어야 합니다.")
        return self


class PlanOut(PlanIn):
    plan_id: int
    created_at: datetime


class PlanSaved(BaseModel):
    """저장 · 수정 결과 — 겹치는 일정이 있어도 저장은 하고 함께 알린다 (F3015)"""
    plan: PlanOut
    overlaps: list[PlanOut]
