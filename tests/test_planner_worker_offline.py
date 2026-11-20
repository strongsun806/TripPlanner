"""오프라인 시험 — Planner / Worker 구조 (심화 F3021)

실행 (TripPlanner 폴더에서):  python tests/test_planner_worker_offline.py
"""
import os
import sys
from pathlib import Path

os.environ["AGENT_MODE"] = "planner_worker"
os.environ["DB_PATH"] = str(Path(__file__).resolve().parent.parent / "data" / "test_pw.db")
Path(os.environ["DB_PATH"]).unlink(missing_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import ANALYSES, FAIL_FOOD, FOOD_EMPTY, PLACES_EMPTY_UNDER, RAIN, SAT, A, install, llm  # noqa: E402

install()
from app import config, store  # noqa: E402
from app.agent.llm import WorkerDecision  # noqa: E402
from app.agent.runner import run_agent  # noqa: E402

store.init_db()
results = []
DECIDE = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def fake_decide(tool, spec, args, fixed, error, attempt):
    DECIDE.append((tool, dict(args), list(fixed)))
    if tool == "search_nearby_food":   # 검색어를 더 일반적인 말로 + 고정 인자(lat)도 바꾸려 해 본다 → 무시돼야 한다
        return WorkerDecision(action="retry", new_args={"keyword": "식당", "lat": 0.0}, reason="검색어를 넓힘")
    return WorkerDecision(action="replan_request", reason="반경을 Worker 가 정할 일이 아님 — Planner 에게 요청")


llm.worker_decide = fake_decide
llm_calls = lambda o, node=None: [e for e in o["steps"] if e["type"] == "llm_call" and (node is None or e["node"] == node)]
kinds = lambda o, k: [e for e in o["steps"] if e["type"] == "decision" and e["data"].get("kind") == k]

# W1 정상 경로 — Worker 의 LLM 호출 0회
ANALYSES["w1"] = A("plan_trip", destination="서울 종로", visit_date=SAT, want_food=True)
o = run_agent("w1", "pw-1")
check("W1 정상 확정", o["result"].get("status") == "ok")
check("W1 Worker LLM 0회", len(llm_calls(o, "worker")) == 0, f'전체 LLM {len(llm_calls(o))}회')
check("W1 작업 목록 · 수행 보고 · 검증 메시지가 로그에", all(kinds(o, k) for k in ("tasks", "worker_report", "review")))
t = kinds(o, "tasks")[0]["data"]["tasks"]
check("W1 고정 인자 표시", "destination" in t[0]["fixed"] and "lat" in t[1]["fixed"], str([x["fixed"] for x in t]))

# W2 결과 없음 → Worker 가 LLM 으로 인자를 고쳐 같은 Tool 재시도, 고정 인자는 그대로
FOOD_EMPTY.add("라멘")
DECIDE.clear()
ANALYSES["w2"] = A("plan_trip", destination="서울 종로", visit_date=SAT, want_food=True)
# 식당 검색어를 '라멘'으로 시작하게 하되, 사용자가 고정한 검색어는 아니게 만든다 (food_keyword 없음)
from app.agent import planner_worker  # noqa: E402
_orig_make = planner_worker.make_steps


def make_steps_ramen(state, em, node="plan"):
    steps, loc, why = _orig_make(state, em, node)
    for s in steps:
        if s["tool"] == "search_nearby_food":
            s["args"]["keyword"] = "라멘"
    return steps, loc, why


planner_worker.make_steps = make_steps_ramen
o = run_agent("w2", "pw-2")
food_calls = [e["data"]["args"] for e in o["steps"] if e["type"] == "tool_call" and e["data"]["tool"] == "search_nearby_food"]
check("W2 같은 Tool 로 인자만 고쳐 재시도", [a["keyword"] for a in food_calls] == ["라멘", "식당"], str(food_calls))
check("W2 고정 인자(lat)는 바뀌지 않음", food_calls[0]["lat"] == food_calls[1]["lat"] and
      kinds(o, "worker_decide")[0]["data"]["ignored"] == ["lat"])
check("W2 Worker LLM 1회 · 식당 결과 확보", len(llm_calls(o, "worker")) == 1 and o["result"].get("food"))
planner_worker.make_steps = _orig_make

# W3 치명적 실패(키 없음) → LLM 에 넘기지 않음
FAIL_FOOD["on"] = True
ANALYSES["w3"] = A("plan_trip", destination="서울 종로", visit_date=SAT, want_food=True)
o = run_agent("w3", "pw-3")
check("W3 치명적 실패는 Worker LLM 0회", len(llm_calls(o, "worker")) == 0 and
      kinds(o, "worker_failure")[0]["data"]["code"] == "INVALID_KEY")
FAIL_FOOD["on"] = False

# W4 후보 없음 → Worker 가 해결 못 함 → Planner 에게 요청 → Planner 가 반경을 넓혀 재계획
PLACES_EMPTY_UNDER["m"] = 8000
ANALYSES["w4"] = A("plan_trip", destination="오사카", visit_date=SAT)
o = run_agent("w4", "pw-4")
check("W4 Worker 요청이 Planner 재계획에 반영", o["replan_count"] >= 1 and "Worker 요청" in o["result"]["replans"][0]["reason"]
      and o["result"].get("status") == "ok", str(o["result"].get("replans")))
PLACES_EMPTY_UNDER["m"] = 0

# W5 Worker LLM 끄기 (비교 실험)
config.WORKER_LLM = False
FOOD_EMPTY.add("맛집")
ANALYSES["w5"] = A("plan_trip", destination="서울 종로", visit_date=SAT, want_food=True)
o = run_agent("w5", "pw-5")
check("W5 Worker LLM 꺼짐 → 재시도 없이 보고", len(llm_calls(o, "worker")) == 0 and o["result"].get("food_error"))

# W6 필수 인자 누락 → Worker 의 LLM 을 부르지 않는다 (지어낼 값이 아니다)
import app.agent.planner_worker as PW  # noqa: E402
_orig_steps = PW.make_steps


def steps_without_lat(*a, **k):
    steps, loc, why = _orig_steps(*a, **k)
    for s_ in steps:
        if s_["tool"] == "search_nearby_food":
            s_["args"] = {kk: vv for kk, vv in s_["args"].items() if kk != "lat"}
    return steps, loc, why


PW.make_steps = steps_without_lat
DECIDE.clear()
ANALYSES["w6"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="14:00", end_time="18:00", want_food=True)
o = run_agent("w6", "pw-6")
miss = [e for e in kinds(o, "worker_failure") if "필수 인자 누락" in e["data"]["reason"]]
check("W6 필수 인자 누락은 Worker LLM 0회", miss and len(llm_calls(o, "worker")) == 0 and not DECIDE,
      miss[0]["data"]["reason"] if miss else "")
PW.make_steps = _orig_steps

# W7 로깅 Agent 규칙이 이상으로 판정하면 Planner 가 작업 목록을 다시 구성한다
import app.evaluator as EV  # noqa: E402
_orig_judge = EV.judge_events
EV.judge_events = lambda events: ("anomaly", [{"code": "REPEATED_CALL", "detail": "시험용 이상 판정"}])
ANALYSES["w7"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="14:00", end_time="18:00")
o = run_agent("w7", "pw-7")
EV.judge_events = _orig_judge
check("W7 이상 판정 → Planner 재구성", len(kinds(o, "tasks")) >= 2 and
      any("로깅 Agent 이상 판정" in r["reason"] for r in o["result"].get("replans", [])),
      f'작업 목록 {len(kinds(o, "tasks"))}번')

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
