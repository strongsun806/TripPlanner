"""요청 1건 실행 — API(/api/chat)가 부르는 입구

그래프를 돌리고, 결과와 함께 이번 실행의 단계 목록(steps)을 돌려준다.
steps 는 메인 Agent 가 스스로 모은 것이다. 로깅 Agent 가 꺼져 있어도 화면은 실행 과정을 보여 줄 수 있다.
"""
import uuid

from .. import config
from . import llm, memory
from .events import RunEmitter, Timer

KEY_GUIDE = ("LLM 키(GMS_KEY) 또는 주소(GMS_BASE_URL)가 설정되지 않았어요. TripPlanner 폴더의 .env 에 "
             "GMS_KEY=발급받은_키 와 GMS_BASE_URL=안내받은_주소 를 넣고 서버를 다시 켜 주세요.")


def run_agent(message: str, session_id: str | None = None) -> dict:
    session_id = session_id or uuid.uuid4().hex[:12]
    em = RunEmitter(session_id)
    em.emit("run_start", None, message=message, mode=config.AGENT_MODE)
    try:
        from .. import store
        last_eval = store.latest_evaluation(session_id)    # 직전 실행의 평가 (심화 F3020)
    except Exception:
        last_eval = None
    state = {"emitter": em, "session_id": session_id, "message": message, "memory": memory.load(session_id),
             "last_evaluation": last_eval}
    error, out = None, {}
    with Timer() as t:
        try:
            if config.AGENT_MODE == "planner_worker":
                from .planner_worker import run as pw_run
                out = pw_run(state)
            else:
                from .graph import GRAPH
                out = GRAPH.invoke(state, config={"recursion_limit": 60})
        except llm.LLMUnavailable:
            error = {"code": "LLM_KEY_MISSING", "message": KEY_GUIDE}
        except llm.LLMError as e:
            reason = str(e)
            em.emit("error", "llm", error=reason, message=getattr(e, "detail", ""),
                    structured_mode=llm.STRUCTURED.get("mode"))           # 원문(키 가림)은 로그에만
            if "RateLimit" in reason:
                error = {"code": "LLM_RATE_LIMIT", "message": "GMS 사용 한도(요청 수 · 토큰)에 걸렸어요. 1~2분 뒤 다시 보내거나, "
                         ".env 의 GMS_MODEL · GMS_BASE_URL 이 지금 쓸 수 있는 모델인지 확인하세요."}
            elif "Authentication" in reason or "PermissionDenied" in reason:
                error = {"code": "LLM_AUTH", "message": "GMS 키가 맞지 않거나 이 모델을 쓸 권한이 없어요. .env 의 GMS_KEY · GMS_MODEL 을 확인하세요."}
            elif "NotFound" in reason or "BadRequest" in reason:
                error = {"code": "LLM_MODEL", "message": "GMS 가 요청을 받지 않았어요(모델 이름 · 주소). .env 의 GMS_MODEL · GMS_BASE_URL 을 확인하세요."}
            else:
                error = {"code": "LLM_ERROR", "message": f"LLM 호출에 실패했어요({reason}). 잠시 후 다시 시도해 주세요."}
        except Exception as e:                          # 어떤 실패도 서비스 전체를 멈추지 않는다 (NF3003)
            error = {"code": "AGENT_ERROR", "message": "요청을 처리하는 중 오류가 났어요. 실행 로그에서 단계를 확인할 수 있어요."}
            em.emit("error", None, error=type(e).__name__, message=str(e)[:200])
    if error:
        em.emit("error", None, **error)
    em.emit("run_end", None, ok=error is None, ms=t.ms, replans=out.get("replan_count", 0),
            intent=out.get("intent"), mode=config.AGENT_MODE,
            llm_calls=sum(1 for e in em.steps if e["type"] == "llm_call"))
    return {"session_id": session_id, "run_id": em.run_id, "ok": error is None,
            "reply": out.get("reply") or (error or {}).get("message", ""),
            "intent": out.get("intent"), "conditions": out.get("conditions", {}),
            "result": out.get("result", {}), "replan_count": out.get("replan_count", 0),
            "error": error, "steps": em.steps, "ms": t.ms}
