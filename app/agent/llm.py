"""LLM 호출 (GMS · OpenAI 호환) — 메인 Agent 가 LLM 을 쓰는 곳은 두 군데뿐이다

    analyze()   사용자 요청 → 의도 + 조건 (F3005). 흐름을 바꾸는 판단이라 이유(reason)를 함께 받는다
    respond()   확정된 사실(JSON) → 사용자에게 보일 답변 (F3011)

계획 수립 · Tool 인자 채우기 · 제약 계산 · 재계획 판단은 규칙으로 할 수 있으므로 LLM 을 쓰지 않는다.
"""
import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from .. import config

WEEKDAYS = "월화수목금토일"


class LLMUnavailable(Exception):
    """키가 없어 LLM 을 부를 수 없다 — 화면에 설정 안내를 띄운다 (F3019)"""


class LLMError(Exception):
    """LLM 호출이 실패했다 (시간 초과 · 서버 오류 · 형식 오류).
    str(e) 는 오류 종류(클래스 이름)만 — 화면용. detail 에 원문(키는 가림)을 담아 실행 로그에만 남긴다."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind)
        self.detail = detail


def _fail(e: Exception) -> LLMError:
    from tools import scrub
    return LLMError(type(e).__name__, scrub(str(e))[:400])


# ── 구조화 출력 — GMS 를 거친 Claude 에서도 되게 ─────────────────
# LangChain 의 with_structured_output(function_calling) 은 tool_choice 강제 · parallel_tool_calls=false 를 붙이는데
# GMS(anthropic) 가 이를 거절한 일이 있었다. 그래서 llm_check.py 에서 통하는 모양(Tool 목록만)으로 보내고,
# 그것도 거절되면 JSON 평문으로 받아 직접 검증한다. 한 번 거절되면 다음부터는 바로 JSON 경로로 간다.
STRUCTURED = {"mode": "tools"}          # tools | json — llm_check.py ③ 과 실행 로그에서 확인할 수 있다


def _extract_json(text: str) -> str:
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        raise ValueError("JSON 을 찾지 못함")
    return text[s:e + 1]


def structured(schema, messages, temperature: float = 0):
    """schema(Pydantic) 모양의 답을 받는다. (객체, 쓴 경로)"""
    llm = get_llm(temperature)
    name = schema.__name__
    hint = f"\n\n반드시 {name} 도구를 호출해 답한다. 다른 문장은 쓰지 않는다."
    if STRUCTURED["mode"] == "tools":
        try:
            msgs = [(messages[0][0], messages[0][1] + hint)] + list(messages[1:])
            out = llm.bind_tools([schema]).invoke(msgs)                # tool_choice · parallel_tool_calls 없이
            calls = [c for c in (out.tool_calls or []) if c["name"] == name]
            if calls:
                return schema.model_validate(calls[0]["args"]), "tools"
            text = out.content if isinstance(out.content, str) else str(out.content)
            if "{" in text:                                            # 도구 대신 JSON 을 글로 준 경우
                return schema.model_validate_json(_extract_json(text)), "tools-text"
        except Exception as e:
            if type(e).__name__ in ("RateLimitError", "AuthenticationError", "PermissionDeniedError",
                                    "APITimeoutError", "APIConnectionError"):
                raise _fail(e) from e                                  # 경로를 바꿔도 같은 실패
            STRUCTURED["mode"] = "json"
            STRUCTURED["last_error"] = _fail(e).detail
    schema_txt = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    msgs = [(messages[0][0], messages[0][1] + f"\n\n아래 JSON 스키마에 맞는 JSON 하나만 출력한다. 설명 · 코드블록 없이.\n{schema_txt}")] \
        + list(messages[1:])
    try:
        out = llm.invoke(msgs)
        text = out.content if isinstance(out.content, str) else str(out.content)
        return schema.model_validate_json(_extract_json(text)), "json"
    except Exception as e:
        raise _fail(e) from e


def get_llm(temperature: float = 0):
    if not config.GMS_KEY:
        raise LLMUnavailable("GMS_KEY")
    from langchain_openai import ChatOpenAI
    # temperature 는 받는 모델에만 넘긴다. gpt-5 · o 계열(2일차 gpt-5.4-nano)과 claude-haiku-5-5 는
    # 'temperature is deprecated for this model' 로 400 을 낸다 (실제로 GMS 에서 확인)
    extra = {"temperature": temperature} if config.GMS_MODEL.startswith(("gpt-4", "gpt-3.5")) else {}
    return ChatOpenAI(model=config.GMS_MODEL, api_key=config.GMS_KEY, base_url=config.GMS_BASE_URL,
                      default_headers=config.gms_headers(),          # Claude 는 x-api-key 헤더가 필요하다
                      timeout=config.LLM_TIMEOUT, max_retries=config.LLM_RETRIES, **extra)


# ── analyze: 요청 분석 (F3005) ────────────────────────────────
Intent = Literal["plan_trip", "weather", "food", "travel_info", "register_calendar", "smalltalk"]


class Conditions(BaseModel):
    """이번 요청에서 새로 말했거나 바꾼 조건만 채운다. 말하지 않은 것은 null."""
    destination: str | None = Field(None, description="여행지(도시·동네). 예: 오사카, 서울 종로")
    visit_date: str | None = Field(None, description="YYYY-MM-DD. '다음 주 토요일' 같은 상대 날짜는 오늘 기준으로 바꾼다")
    date_fixed: bool | None = Field(None, description="'그날만', '토요일에만'처럼 날짜를 바꿀 수 없다고 했으면 true")
    start_time: str | None = Field(None, description="HH:MM. 일정을 시작할 수 있는 가장 이른 시각 (기준 조건)")
    end_time: str | None = Field(None, description="HH:MM. 끝나야 하는 시각 (제약 조건)")
    style: Literal["outdoor", "indoor"] | None = Field(None, description="야외/실내 선호")
    outdoor_only: bool | None = Field(None, description="'무조건 야외', '실내는 싫어'처럼 실내로 바꾸면 안 되면 true")
    want_food: bool | None = Field(None, description="식당·맛집·카페도 알려 달라고 했으면 true")
    food_keyword: str | None = Field(None, description="음식 종류를 말했으면 그 말. 예: 라멘, 카페")


class Analysis(BaseModel):
    """사용자 요청을 의도 · 조건 · 부를 Tool 로 나눈 분석 결과"""
    intent: Intent = Field(description=(
        "plan_trip=하루 일정 추천 / weather=날씨만 / food=식당만 / travel_info=여행지에 대한 지식 질문 / "
        "register_calendar=저장한 일정을 캘린더에 등록 / smalltalk=인사·사용법 등 Tool 이 필요 없는 대화"))
    reason: str = Field(description="이 의도로 분류한 이유 한 문장")
    reset: bool = Field(description="이전 대화와 다른 새 여행 요청이면 true, 이어지는 요청(조건 일부 변경)이면 false")
    changes: Conditions
    assumptions: list[str] = Field(default_factory=list,
                                   description="모호한 말을 해석한 내용. 예: \"'오후'를 13:00 시작으로 해석\"")
    tools: list[str] = Field(default_factory=list, description=(
        "이 요청에 부를 Tool 이름을 순서대로 (아래 Tool 목록에 있는 이름만). Tool 이 필요 없으면 []. "
        "인자는 채우지 않는다 — 코드가 조건으로 채운다"))
    plan_id: int | None = Field(None, description="register_calendar 일 때 일정 번호")
    question: str | None = Field(None, description="travel_info 일 때 검색할 질문")


ANALYZE_SYSTEM = """너는 여행 플래너의 요청 분석기다. 사용자의 요청을 의도와 조건으로 나눈다.
오늘은 {today} ({weekday}요일)이다.

규칙
- changes 에는 이번 요청에서 새로 말했거나 바꾼 조건만 넣는다. 말하지 않은 조건은 null 로 둔다. 추측해서 채우지 않는다.
- 상대 날짜('내일', '다음 주 토요일', '이번 주말')는 오늘 기준 YYYY-MM-DD 로 바꾼다. '이번 주말'은 다가오는 토요일이다.
- '3시 이후' → start_time 15:00, '7시 전에 끝나야' → end_time 19:00 처럼 오후 시각은 맥락으로 판단한다.
- '오후', '저녁'처럼 시각이 없는 말을 시각으로 바꿨다면 그 해석을 assumptions 에 적는다.
- 이전 조건이 있을 때 '같은 날 다른 곳', '시간만 바꿔서'처럼 일부만 바꾸면 reset=false 로 하고 바뀐 것만 넣는다.
- 인사, 사용법 질문, 감사 인사는 smalltalk 이다.
- tools 에는 아래 Tool 목록을 보고 이 요청에 필요한 Tool 이름만 순서대로 넣는다 (2일차 Plan 과 같다).

Tool 목록:
{catalog}

이전 조건: {previous}
직전 추천: {last_summary}"""


def analyze(message: str, previous: dict, last_summary: dict | None, turns: list[dict]) -> Analysis:
    today = date.today()
    from .registry import catalog
    tool_lines = "\n".join(f'- {t["name"]}: {t["description"]}' for t in catalog())
    messages = [("system", ANALYZE_SYSTEM.format(
        today=today.isoformat(), weekday=WEEKDAYS[today.weekday()], catalog=tool_lines,
        previous=json.dumps(previous, ensure_ascii=False) or "{}",
        last_summary=json.dumps(last_summary, ensure_ascii=False) if last_summary else "없음"))]
    for t in turns:
        messages += [("human", t["user"]), ("ai", t["assistant"])]
    messages.append(("human", message))
    out, _ = structured(Analysis, messages)
    return out


# ── respond: 최종 응답 (F3011) ────────────────────────────────
RESPOND_SYSTEM = """너는 여행 플래너다. 아래 '사실'만 써서 한국어로 답한다. 사실에 없는 정보(영업시간, 가격, 상영·공연 시간,
실제 소요 시간 등)는 지어내지 않는다. 확인하지 못한 것은 확인하지 못했다고 말한다.

답변 구성 (해당하는 것만)
1. 한 줄 결론
2. 추천 일정: 시각, 장소 이름, 머무는 시간, 다음 장소까지 도보 시간
3. 판단 기준: 강수확률과 기준값, 시간창, 계산 방법(직선거리 × 우회계수 ÷ 도보 속도) 한 줄
4. 보조 정보(식당)가 있으면 이름과 링크
5. 제외한 후보와 사유, 재계획했다면 무엇을 바꿨는지
6. 기본값을 썼거나 해석한 내용(assumptions)이 있으면 밝힌다
마크다운 목록을 써도 된다. 12줄 안팎으로 간결하게."""


def respond(user_message: str, facts: dict) -> str:
    llm = get_llm(temperature=0.3)
    try:
        out = llm.invoke([("system", RESPOND_SYSTEM),
                          ("human", f"사용자 요청: {user_message}\n\n사실(JSON):\n"
                                    f"{json.dumps(facts, ensure_ascii=False, default=str)}")])
    except LLMUnavailable:
        raise
    except Exception as e:
        raise _fail(e) from e
    return out.content.strip()


def smalltalk(user_message: str, turns: list[dict]) -> str:
    llm = get_llm(temperature=0.5)
    messages = [("system", "너는 국내·해외 여행 하루 일정을 짜 주는 TripPlanner 다. 여행지·날짜·시간·선호(야외/실내)를 "
                           "말하면 후보 장소와 날씨를 확인해 일정을 제안하고, 국내라면 주변 식당도 알려 준다. "
                           "짧고 친절하게 답한다. 할 수 없는 일은 할 수 없다고 말한다.")]
    for t in turns:
        messages += [("human", t["user"]), ("ai", t["assistant"])]
    messages.append(("human", user_message))
    try:
        return llm.invoke(messages).content.strip()
    except LLMUnavailable:
        raise
    except Exception as e:
        raise _fail(e) from e


# ── Worker 판단 (심화 F3021) ─────────────────────────────────
class WorkerDecision(BaseModel):
    """실패한 Tool 호출에 대한 Worker 의 판단"""
    action: Literal["retry", "skip", "replan_request"] = Field(description=(
        "retry=인자를 고쳐 같은 Tool 을 다시 부른다 / skip=이 작업은 건너뛴다 / "
        "replan_request=인자를 고쳐서는 해결할 수 없으니 Planner 에게 계획 변경을 요청한다"))
    new_args: dict = Field(default_factory=dict, description="retry 일 때 바꿀 인자만. 고정 인자는 넣지 않는다")
    reason: str = Field(description="이 판단의 이유 한 문장")


WORKER_SYSTEM = """너는 여행 플래너의 Worker 다. Tool 호출이 실패했다. 인자를 고치면 결과가 달라질 수 있는지 판단한다.
- 고정 인자(fixed)는 사용자가 정한 조건이라 절대 바꾸지 않는다.
- 고칠 수 있는 인자만 new_args 에 넣는다 (예: 검색 반경을 넓히기, 검색어를 더 일반적인 말로).
- 고칠 인자가 없거나 고쳐도 소용없으면 skip 또는 replan_request 를 고른다."""


def worker_decide(tool: str, spec: dict, args: dict, fixed: list[str], error: dict, attempt: int) -> WorkerDecision:
    out, _ = structured(WorkerDecision, [("system", WORKER_SYSTEM),
                                        ("human", json.dumps({"tool": tool, "parameters": spec, "args": args, "fixed": fixed,
                                                              "error": error, "attempt": attempt}, ensure_ascii=False))])
    return out
