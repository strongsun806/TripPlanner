"""내 캘린더 라우터 (심화 F3023) — Agent 가 일정을 짤 때 피해 갈 약속을 넣고 본다

    GET    /api/calendar?event_date=   약속 목록
    POST   /api/calendar               약속 추가 (직접 입력)
    DELETE /api/calendar/{event_id}    약속 삭제 (로컬 구현체)

Agent 는 Tool(get_schedule · register_schedule)로 같은 저장소를 쓴다.
"""
from datetime import date, time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from .. import store
from ..calendar_backend import CALENDAR

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=100, examples=["친구 저녁 약속"])
    event_date: date
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time은 start_time보다 늦어야 합니다.")
        return self


@router.get("", summary="내 캘린더 약속 목록")
def list_events(event_date: date | None = None):
    if CALENDAR.name != "local":
        return CALENDAR.list_events(str(event_date)) if event_date else []
    return store.calendar_events(str(event_date)) if event_date else store.calendar_all()


@router.post("", status_code=201, summary="약속 추가")
def add_event(ev: EventIn):
    return CALENDAR.add_event(ev.title, ev.event_date, ev.start_time, ev.end_time)


@router.delete("/{event_id}", status_code=204, summary="약속 삭제 (로컬)")
def delete_event(event_id: int):
    if not store.calendar_delete(event_id):
        raise HTTPException(status_code=404, detail="그런 약속이 없습니다.")
