"""로컬 서버 API 툴 — 이 서비스가 가진 기능을 감싼 Tool (심화 F3022 · F3023)

외부 API 툴(tools.py)과 같은 약속을 지킨다: ok/fail 로 답하고, 예외를 밖으로 던지지 않는다.

    get_schedule        그날 내 일정(캘린더) 조회 → 빈 시간 판단에 반영           F3023
    register_schedule   저장한 여행 일정 번호 하나로 캘린더 등록 (제목 · 시각은 서버가 읽는다)  F3023
    search_travel_docs  2회차 Vector DB 에서 여행지 지식 검색 — 근거 문서와 출처를 함께   F3022

실패 코드 (공통 코드 외)
    NO_INDEX            색인이 아직 없다 → '아직 모으지 못했다'고 답한다
    ALREADY_REGISTERED  이미 캘린더에 등록한 일정
"""
from datetime import date

from tools import Tool, fail, ok

from .. import rag, store
from ..calendar_backend import CALENDAR


def get_schedule(visit_date: str):
    try:
        day = date.fromisoformat(visit_date)
    except (TypeError, ValueError):
        return fail("BAD_ARGUMENT", "visit_date는 YYYY-MM-DD입니다.")
    try:
        events = CALENDAR.list_events(day.isoformat())
    except Exception:
        return fail("UPSTREAM_ERROR", "캘린더를 읽지 못했습니다.")
    return ok({"date": day.isoformat(), "backend": CALENDAR.name, "events": events})


def register_schedule(plan_id: int):
    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        return fail("BAD_ARGUMENT", "plan_id는 정수입니다.")
    plan = store.get(plan_id)                       # 제목 · 날짜 · 시각은 Tool 인자가 아니라 저장소에서 읽는다
    if plan is None:
        return fail("NOT_FOUND", f"{plan_id}번 일정이 없습니다.")
    try:
        if CALENDAR.find_plan(plan_id):
            return fail("ALREADY_REGISTERED", f"{plan_id}번 일정은 이미 캘린더에 있습니다.")
        title = f'[여행] {plan["place_name"]} ({plan["city"]})'
        ev = CALENDAR.add_event(title, plan["visit_date"], plan["start_time"], plan["end_time"], plan_id=plan_id)
    except Exception:
        return fail("UPSTREAM_ERROR", "캘린더에 등록하지 못했습니다.")
    return ok({"plan_id": plan_id, "title": title, "date": plan["visit_date"],
               "start_time": plan["start_time"][:5], "end_time": plan["end_time"][:5],
               "backend": CALENDAR.name, "event": ev})


def search_travel_docs(query: str, k: int = 4):
    if not query or not query.strip():
        return fail("BAD_ARGUMENT", "query는 필수입니다.")
    if not 1 <= int(k) <= 8:
        return fail("BAD_ARGUMENT", "k는 1~8입니다.")
    try:
        res = rag.search(query.strip(), int(k))
    except ImportError:
        return fail("NO_INDEX", "chromadb 가 설치되지 않아 문서를 검색할 수 없습니다.")
    except Exception:
        return fail("UPSTREAM_ERROR", "문서 색인을 읽지 못했습니다.")
    if not res["indexed"]:
        return fail("NO_INDEX", "아직 여행 문서를 모으지 못했습니다. python rag_index.py 로 색인을 만드세요.")
    if not res["hits"]:
        return fail("NO_DATA", "관련 문서가 없습니다.")
    return ok(res)


TOOLS = {t.name: t for t in [
    Tool(name="get_schedule",
         description="여행 날짜가 정해졌을 때 그날 사용자의 다른 약속이 있는지 확인해야 할 때 부른다. 빈 시간에 맞춰 일정을 짠다.",
         parameters={"type": "object", "properties": {"visit_date": {"type": "string", "format": "date"}},
                     "required": ["visit_date"]},
         func=get_schedule),
    Tool(name="register_schedule",
         description="사용자가 저장한 여행 일정을 캘린더에 넣어 달라고 할 때 부른다. 일정 번호 하나만 받는다.",
         parameters={"type": "object", "properties": {"plan_id": {"type": "integer"}}, "required": ["plan_id"]},
         func=register_schedule),
    Tool(name="search_travel_docs",
         description="여행지의 특징 · 베스트 시즌 · 역사처럼 외부 API 로 확인할 수 없는 지식을 물었을 때 부른다. "
                     "근거 문서와 출처를 함께 돌려준다.",
         parameters={"type": "object", "properties": {"query": {"type": "string"},
                                                      "k": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4}},
                     "required": ["query"]},
         func=search_travel_docs),
]}
