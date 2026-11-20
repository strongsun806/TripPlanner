"""실행 로그 라우터 (F3014) — 목록 · 상세 · 삭제만 제공한다

수정 API 는 일부러 두지 않는다. 기록을 고칠 수 있으면 관찰의 의미가 사라지기 때문이다.

    GET    /api/logs               세션 목록 (최근 순)
    GET    /api/logs/{session_id}  세션의 이벤트 (실행 순서대로) + 평가
    DELETE /api/logs/{session_id}  세션 로그 삭제
"""
from fastapi import APIRouter, HTTPException

from .. import store
from ..schemas import SessionLog, SessionSummary

router = APIRouter(prefix="/api/logs", tags=["logs"])
NOT_FOUND = "그런 세션의 실행 로그가 없습니다."


@router.get("", response_model=list[SessionSummary], summary="실행 로그 세션 목록")
def list_logs():
    return store.list_sessions()


@router.get("/{session_id}", response_model=SessionLog, summary="세션 실행 로그 상세")
def get_log(session_id: str):
    events = store.session_events(session_id)
    if not events:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return {"session_id": session_id, "events": events, "evaluations": store.evaluations(session_id)}


@router.delete("/{session_id}", status_code=204, summary="세션 실행 로그 삭제")
def delete_log(session_id: str):
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
