"""오프라인 시험 — LLM 과 외부 API 를 가짜로 바꿔 Agent 흐름만 확인한다 (키 · 네트워크 불필요)

실행 (TripPlanner 폴더에서):  python tests/test_agent_offline.py

확인하는 것 (검증 4.2 · 4.3 · 4.7)
    S1 제약을 한 번에 충족           S2 비 예보 → 실내로 재계획 (이미 받은 결과 재사용)
    S3 재계획 상한 도달               S4 Tool 하나 실패해도 진행     S5 Tool 이 필요 없는 요청
    S6 후속 요청 — 바뀐 조건만 갱신   S7 되묻기                      S8 로깅 Agent 없이도 정상 동작
    E1 뺀 장소는 대화 내내 제외      E2 관광지 아닌 곳 거르기 · 식당 검색어 정리 · 위키백과 링크
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import ANALYSES, CALLS, FAIL_FOOD, RAIN, SAT, A, install, llm  # noqa: E402

install()
from app.agent.events import BUS  # noqa: E402
from app.agent.runner import run_agent  # noqa: E402

# ── 시나리오 ─────────────────────────────────────────────────
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def kinds(out, kind):
    return [s for s in out["steps"] if s["type"] == "decision" and s["data"].get("kind") == kind]


def tool_events(out):
    return [s["data"]["tool"] for s in out["steps"] if s["type"] == "tool_call" and not s["data"].get("flow")]


# S1 제약을 한 번에 충족
ANALYSES["s1"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="14:00", end_time="18:00", want_food=True)
o = run_agent("s1", "sess-1")
check("S1 일정 확정", o["result"].get("status") == "ok" and len(o["result"]["itinerary"]) >= 1,
      f'{[(i["name"], i["start_time"], i["end_time"]) for i in o["result"].get("itinerary", [])]}')
check("S1 Tool 순서 후보 → 날씨 → 내 일정 → 식당",
      tool_events(o) == ["search_places", "get_weather", "get_schedule", "search_nearby_food"], str(tool_events(o)))
check("S1 단계 이벤트 5종", {s["node"] for s in o["steps"] if s["type"] == "node_start"} ==
      {"analyze", "plan", "execute", "judge", "respond"})
check("S1 재계획 없음", o["replan_count"] == 0)

# S2 비 예보 → 실내로 재계획, 날씨 결과는 캐시로 재사용
RAIN[SAT] = 80
CALLS.clear()
ANALYSES["s2"] = A("plan_trip", destination="오사카", visit_date=SAT, style="outdoor")
o = run_agent("s2", "sess-2")
check("S2 재계획 1회", o["replan_count"] == 1, str(o["result"].get("replans")))
check("S2 실내 후보로 확정", o["result"].get("status") == "ok" and "박물관" in o["result"]["itinerary"][0]["name"])
check("S2 날씨는 다시 부르지 않음(캐시)", CALLS.count(("get_weather", SAT)) == 1, str(CALLS))
check("S2 제외 사유 기록", any("비 예보" in e["reason"] for e in o["result"]["excluded"]))
check("S2 해외라 식당 Tool 계획 안 함", "search_nearby_food" not in tool_events(o))

# S3 재계획 상한 — 야외만, 날짜 바꿔도 계속 비
for i in range(0, 15):
    RAIN[(date.fromisoformat(SAT) + timedelta(days=i)).isoformat()] = 90
ANALYSES["s3"] = A("plan_trip", destination="오사카", visit_date=SAT, outdoor_only=True)
o = run_agent("s3", "sess-3")
check("S3 상한 도달(3회 후 멈춤)", o["replan_count"] == 3 and o["result"].get("status") == "cap",
      f'replans={o["replan_count"]}, status={o["result"].get("status")}')
check("S3 상한 이벤트", len(kinds(o, "replan_cap")) == 1)

# S4 Tool 하나 실패해도 진행
RAIN.clear()
FAIL_FOOD["on"] = True
ANALYSES["s4"] = A("plan_trip", destination="서울 종로", visit_date=SAT, want_food=True)
o = run_agent("s4", "sess-4")
check("S4 식당 실패 기록하고 일정은 확정", o["result"].get("status") == "ok" and o["result"].get("food_error", {}).get("code") == "INVALID_KEY")
FAIL_FOOD["on"] = False

# S5 Tool 이 필요 없는 요청
ANALYSES["s5"] = A("smalltalk")
o = run_agent("s5", "sess-5")
check("S5 Tool 호출 없음 · plan 건너뜀", tool_events(o) == [] and
      "plan" not in {s["node"] for s in o["steps"] if s["type"] == "node_start"})

# S6 후속 요청 — 날짜 · 시간은 유지, 여행지만 변경
ANALYSES["s6a"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="13:00", end_time="17:00")
run_agent("s6a", "sess-6")
ANALYSES["s6b"] = A("plan_trip", reset=False, destination="오사카")
o = run_agent("s6b", "sess-6")
c = o["conditions"]
check("S6 바뀐 조건만 갱신", c["destination"] == "오사카" and c["visit_date"] == SAT and c["start_time"] == "13:00",
      str(c))
check("S6 바뀐 필드 기록", kinds(o, "intent")[0]["data"]["changed"] == ["destination"])

# S7 되묻기 — 끝나는 시각만 있음
ANALYSES["s7"] = A("plan_trip", destination="오사카", visit_date=SAT, end_time="19:00")
o = run_agent("s7", "sess-7")
check("S7 Tool 부르기 전에 되묻기", tool_events(o) == [] and "시작" in o["reply"], o["reply"])

# S8 로깅 Agent 없이 — 구독자 0개
BUS._subscribers.clear()
ANALYSES["s8"] = A("plan_trip", destination="서울 종로", visit_date=SAT)
o = run_agent("s8", "sess-8")
check("S8 구독자 없이도 정상 응답", o["ok"] and o["result"].get("status") == "ok")

# ── 심화 ─────────────────────────────────────────────────
from app import evaluator, store  # noqa: E402

store.init_db()
# A1 (F3023) 그날 약속을 피해서 일정을 짠다
store.calendar_add("친구 점심", SAT, "12:00", "14:00")
ANALYSES["a1"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="10:00", end_time="18:00")
o = run_agent("a1", "sess-a1")
starts = [i["start_time"] for i in o["result"].get("itinerary", [])]
check("A1 약속(12~14시)을 피한 빈 시간에 배치", o["result"].get("status") == "ok" and all(s >= "14:00" for s in starts),
      f'{starts}, {o["result"]["defaults"][-1]}')

# A2 (F3023) 저장한 일정 번호 하나로 캘린더 등록 — 두 번째는 ALREADY_REGISTERED
pl = store.create({"place_name": "경복궁", "city": "서울", "visit_date": SAT, "start_time": "15:00", "end_time": "17:00",
                   "lat": None, "lon": None, "memo": None})
ANALYSES["a2"] = A("register_calendar")
ANALYSES["a2"].plan_id = pl["plan_id"]
o = run_agent("a2", "sess-a2")
o2 = run_agent("a2", "sess-a2b")
check("A2 캘린더 등록 + 중복 방지", o["result"]["calendar"]["ok"] and
      o2["result"]["calendar"]["error"]["code"] == "ALREADY_REGISTERED")

# A3 (F3022) 색인이 없으면 NO_INDEX
import app.rag as rag  # noqa: E402
rag.RAG_DB_DIR = Path("/nonexistent/chroma")
ANALYSES["a3"] = A("travel_info")
o = run_agent("a3", "sess-a3")
check("A3 색인 없음 → NO_INDEX", o["result"]["docs"]["error"]["code"] == "NO_INDEX")

# A4 (F3020) 규칙 판정: 정상 · 판정 불가 · 이상(반복 호출 · 계획 밖 호출 · 과도한 재계획)
ev = lambda seq, type_, **d: {"seq": seq, "type": type_, "node": None, "data": d}
normal = [ev(1, "run_start"), ev(2, "decision", kind="plan", steps=["get_weather"]),
          ev(3, "tool_call", tool="get_weather", args={"a": 1}), ev(4, "run_end")]
check("A4 정상", evaluator.judge_events(normal)[0] == "normal")
check("A4 근거 없음 → 판정 불가", evaluator.judge_events([ev(1, "run_start")])[0] == "undecidable")
bad = normal[:3] + [ev(4, "tool_call", tool="get_weather", args={"a": 1}), ev(5, "tool_call", tool="search_places", args={}),
                    ev(6, "decision", kind="replan_cap"), ev(7, "run_end")]
verdict, warns = evaluator.judge_events(bad)
check("A4 이상: 반복 · 계획 밖 · 과도한 재계획", verdict == "anomaly" and
      {w["code"] for w in warns} == {"REPEATED_CALL", "UNPLANNED_CALL", "EXCESSIVE_REPLAN"}, str([w["code"] for w in warns]))
# 3일차 s4 · s5: 같은 Tool 연속 실패는 경고, 한 번 실패 뒤 성공은 조용
R_ = lambda seq, tool, ok_: ev(seq, "tool_result", tool=tool, ok=ok_, code=None if ok_ else "NO_DATA")
streak = [ev(1, "run_start"), ev(2, "decision", kind="plan", steps=["get_weather"]),
          ev(3, "tool_call", tool="get_weather", args={"d": 1}), R_(4, "get_weather", False),
          ev(5, "tool_call", tool="get_weather", args={"d": 2}), R_(6, "get_weather", False), ev(7, "run_end")]
once = streak[:5] + [R_(6, "get_weather", True), ev(7, "run_end")]
check("A4 연속 실패 → FAIL_STREAK, 한 번 실패 → 조용",
      [w["code"] for w in evaluator.judge_events(streak)[1]] == ["FAIL_STREAK"] and evaluator.judge_events(once)[0] == "normal")

# A5 (F3020) 정상 실행에는 LLM 진단을 부르지 않는다 · 이상이면 진단은 다른 칸
called = []
evaluator.diagnose = lambda events, warnings: called.append(1) or "계획에 없는 Tool 호출 — Planner 의 작업 목록을 점검하세요."
for e in o["steps"]:
    store.append_event(e)
r = evaluator.evaluate_run(o["run_id"], "sess-a3")
check("A5 정상 실행은 LLM 진단 0회", r["verdict"] == "normal" and not called, r["verdict"])
for e in bad:
    store.append_event({**e, "session_id": "sess-bad", "run_id": "run-bad", "ts": "2026-01-01T00:00:00"})
r = evaluator.evaluate_run("run-bad", "sess-bad")
saved = store.latest_evaluation("sess-bad")
check("A5 이상 실행만 진단 · 판정과 진단 분리 저장", called == [1] and saved["diagnosis"] and len(saved["warnings"]) == 3)

# ── 2일차 반영: LLM 이 Tool 목록을 보고 고르기 · scrub · gpt-5 ──
def choice(o):
    return [e["data"] for e in o["steps"] if e["type"] == "decision" and e["data"].get("kind") == "tool_choice"][0]


# D1 해외인데 식당 Tool 을 고름 → 코드가 빼고, 판단에 필요한 내 일정 조회는 넣는다
ANALYSES["d1"] = A("plan_trip", destination="오사카", visit_date=SAT)
ANALYSES["d1"].tools = ["search_places", "get_weather", "search_nearby_food"]
o = run_agent("d1", "sess-d1")
c = choice(o)
check("D1 LLM 선택 보정 (해외 식당 제외 · 내 일정 추가)",
      c["added"] == ["get_schedule"] and c["dropped"][0]["tool"] == "search_nearby_food" and
      "search_nearby_food" not in tool_events(o), str(c))

# D2 '식당' 말은 없었지만 LLM 이 국내 식당 Tool 을 고름 → 포함
ANALYSES["d2"] = A("plan_trip", destination="서울 종로", visit_date=SAT)
ANALYSES["d2"].tools = ["search_places", "get_weather", "get_schedule", "search_nearby_food", "book_hotel"]
o = run_agent("d2", "sess-d2")
c = choice(o)
check("D2 LLM 이 고른 국내 식당 포함 · 없는 Tool 은 제외",
      "search_nearby_food" in tool_events(o) and c["dropped"] == [{"tool": "book_hotel", "reason": "등록되지 않은 Tool"}], str(c))

# D3 분석 프롬프트에 Tool 목록(catalog)이 실제로 들어가는지 — 진짜 analyze 를 가짜 모델로
import importlib  # noqa: E402
import app.agent.llm as real_llm_mod  # noqa: E402
real = importlib.reload(real_llm_mod)
seen = {}


class _Fake:
    """GMS 대역 — Tool 목록만 받고(tool_choice 없음) Analysis 도구 호출로 답한다"""
    def bind_tools(self, tools, **kw):
        seen["bind_kw"] = kw
        return self

    def invoke(self, messages):
        seen["system"] = messages[0][1]

        class Msg:
            content = ""
            tool_calls = [{"name": "Analysis", "args": {"intent": "smalltalk", "reason": "t", "reset": True,
                                                         "changes": {}}}]
        return Msg()


real.get_llm = lambda temperature=0: _Fake()
real.analyze("안녕", {}, None, [])
check("D3 프롬프트에 Tool 목록 포함", all(n in seen["system"] for n in ("search_places", "get_weather", "search_travel_docs")))
check("D3 구조화 요청에 tool_choice · parallel_tool_calls 를 붙이지 않음 (GMS Claude 호환)", seen["bind_kw"] == {})

# D4 gpt-5 계열은 temperature 를 넘기지 않는다
from app import config as cfg  # noqa: E402
real2 = importlib.reload(real_llm_mod)
cfg.GMS_KEY, old_model = "x", cfg.GMS_MODEL
cfg.GMS_MODEL = "gpt-5.4-nano"
m5 = real2.get_llm(temperature=0.3)
cfg.GMS_MODEL = "gpt-4.1-mini"
m4 = real2.get_llm(temperature=0.3)
cfg.GMS_MODEL = "claude-haiku-5-5"
mc = real2.get_llm(temperature=0.3)
cfg.GMS_MODEL = old_model
check("D4 gpt-5 · claude 는 temperature 생략, gpt-4.1 은 전달",
      m5.temperature is None and mc.temperature is None and m4.temperature == 0.3,
      f"gpt-5={m5.temperature}, claude={mc.temperature}, gpt-4.1={m4.temperature}")
install()   # 가짜 LLM 다시 끼우기 (reload 로 풀렸다)

# D5 실행 이벤트의 오류 문구에서 키를 가린다 (2일차 scrub)
import os  # noqa: E402
from app.agent.events import RunEmitter  # noqa: E402
os.environ["KAKAO_REST_KEY"] = "kakao-secret-123"
ev = RunEmitter("sess-d5").emit("error", None, message="401 for url https://x/?key=AAA KakaoAK kakao-secret-123")
check("D5 이벤트 문구 키 가리기", "kakao-secret-123" not in ev["data"]["message"] and "AAA" not in ev["data"]["message"],
      ev["data"]["message"])

# E1 사용자가 뺀 장소는 빼고, 후속 요청에서도 계속 뺀다
ANALYSES["e1"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="14:00", end_time="18:00",
                   exclude=["공원"])
o = run_agent("e1", "sess-e1")
names = [i["name"] for i in o["result"].get("itinerary", [])]
check("E1 '공원 빼' → 공원 후보 제외 · 사유", "공원 A" not in names and
      any("요청에 따라 제외" in e["reason"] for e in o["result"].get("excluded", [])), str(names))
ANALYSES["e1b"] = A("plan_trip", reset=False, start_time="13:00")
o = run_agent("e1b", "sess-e1")
check("E1 후속 요청에도 계속 제외", "공원 A" not in [i["name"] for i in o["result"].get("itinerary", [])]
      and o["conditions"].get("exclude") == ["공원"], str(o["conditions"].get("exclude")))

# E2 관광지가 아닌 곳 거르기 · 식당 검색어 정리 (tools.py 순수 함수)
import tools as T  # noqa: E402
check("E2 병원 · 학교 · '…터' · 옛 건물은 후보에서 뺀다",
      not any(T.is_sight(n, []) for n in ["이화여자대학교 동대문병원(1887~2008)", "전차 차고 터", "동대문 주차장"])
      and all(T.is_sight(n, []) for n in ["흥인지문", "국립민속박물관", "세종문화회관 아트센터"]))
check("E2 '예쁜 카페 위주' → 카페 · 분류 CE7", T.food_query("예쁜 카페 위주") == ("카페", "CE7")
      and T.food_query("맛있는 라멘") == ("라멘", "FD6"))
check("E2 위키백과 링크", T.wiki_link({"wikipedia": "ko:흥인지문"})["url"].endswith("/wiki/흥인지문"))

# S9 LLM 키 없음 → 설정 안내
from app import config  # noqa: E402
key, config.GMS_KEY = config.GMS_KEY, None
llm.analyze = lambda *a: (_ for _ in ()).throw(llm.LLMUnavailable("GMS_KEY"))
o = run_agent("s9", "sess-9")
check("S9 키 없음 안내", o["error"]["code"] == "LLM_KEY_MISSING", o["reply"][:40])

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
