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


# ── 대화 (F3017) ─────────────────────────────────────────────
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=500, examples=["다음 주 토요일 오사카, 오후에 야외 위주로. 비 오면 곤란해"])
    session_id: str | None = Field(default=None, max_length=40, description="이어서 대화하려면 직전 응답의 session_id")


class ChatError(BaseModel):
    code: str                      # LLM_KEY_MISSING · LLM_RATE_LIMIT · LLM_AUTH · LLM_MODEL · LLM_ERROR · AGENT_ERROR
    message: str


class ChatOut(BaseModel):
    session_id: str
    run_id: str
    ok: bool
    reply: str                     # 사용자에게 보일 답변
    intent: str | None
    conditions: dict               # 지금까지 확정된 조건 (Memory)
    result: dict                   # 화면이 그릴 구조화된 결과 (일정 · 날씨 · 식당 · 제외 · 재계획 · 근거)
    replan_count: int
    error: ChatError | None
    steps: list[dict]              # 이번 실행의 단계 · Tool 호출 · 판단 이벤트 (실행 순서)
    ms: int


# ── 실행 로그 (F3014) ────────────────────────────────────────
class SessionSummary(BaseModel):
    session_id: str
    first_ts: str
    last_ts: str
    runs: int
    events: int
    first_message: str | None


class LogEvent(BaseModel):
    id: int
    session_id: str
    run_id: str
    seq: int
    ts: str
    type: str
    node: str | None
    data: dict


class SessionLog(BaseModel):
    session_id: str
    events: list[LogEvent]         # 실행 순서대로
    evaluations: list[dict]        # 심화 F3020 — 판정(warnings)과 진단(diagnosis)이 다른 칸
