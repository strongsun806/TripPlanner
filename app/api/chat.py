"""대화 라우터 (F3017)

    POST   /api/chat               요청 1건 실행 → 답변 + 구조화된 결과 + 실행 단계
    DELETE /api/chat/{session_id}  대화 맥락(Memory) 지우기 — 새 대화
"""
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from ..agent import memory
from ..agent.runner import run_agent
from ..schemas import ChatIn, ChatOut

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatOut, summary="여행 플래너에게 요청")
async def chat(body: ChatIn):
    # Agent 는 외부 API · LLM 을 기다리는 동안 막히므로 스레드에서 돌린다
    return await run_in_threadpool(run_agent, body.message.strip(), body.session_id)


@router.delete("/{session_id}", status_code=204, summary="대화 맥락 지우기")
def reset(session_id: str):
    memory.forget(session_id)
