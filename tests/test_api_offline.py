"""오프라인 API 시험 — /api/chat · /api/logs · /api/plans 가 서로 맞물려 도는지 (가짜 LLM · 외부 API)

실행 (TripPlanner 폴더에서):  python tests/test_api_offline.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["DB_PATH"] = str(Path(__file__).resolve().parent.parent / "data" / "test_api.db")
Path(os.environ["DB_PATH"]).unlink(missing_ok=True)
from fakes import ANALYSES, RAIN, SAT, A, install  # noqa: E402

install()
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


with TestClient(app) as c:
    # 대화 → 재계획이 일어나는 요청
    RAIN[SAT] = 75
    ANALYSES["오사카 토요일 야외"] = A("plan_trip", destination="오사카", visit_date=SAT)
    r = c.post("/api/chat", json={"message": "오사카 토요일 야외"})
    o = r.json()
    check("POST /api/chat 200", r.status_code == 200 and o["ok"], o.get("reply", "")[:60])
    check("응답에 실행 단계 · 재계획 포함", o["replan_count"] == 1 and any(s["type"] == "decision" for s in o["steps"]))
    sid = o["session_id"]

    # 같은 세션으로 후속 요청
    ANALYSES["같은 날 서울 종로로"] = A("plan_trip", reset=False, destination="서울 종로")
    o2 = c.post("/api/chat", json={"message": "같은 날 서울 종로로", "session_id": sid}).json()
    check("후속 요청: 날짜 유지", o2["conditions"]["visit_date"] == SAT and o2["session_id"] == sid)

    # 다른 세션
    ANALYSES["안녕"] = A("smalltalk")
    o3 = c.post("/api/chat", json={"message": "안녕"}).json()
    time.sleep(0.3)

    # 실행 로그 (로깅 Agent 가 저장한 것)
    sessions = c.get("/api/logs").json()
    check("GET /api/logs 세션 2개", len(sessions) == 2, str([(s["session_id"], s["runs"]) for s in sessions]))
    detail = c.get(f"/api/logs/{sid}").json()
    seqs = [(e["run_id"], e["seq"]) for e in detail["events"]]
    check("상세: 실행 순서대로", all(a[1] < b[1] for a, b in zip(seqs, seqs[1:]) if a[0] == b[0]))
    check("상세: run 2개 · 세션 섞이지 않음", len({e["run_id"] for e in detail["events"]}) == 2 and
          all(e["session_id"] == sid for e in detail["events"]))
    types = {e["type"] for e in detail["events"]}
    check("이벤트 종류", {"run_start", "node_start", "node_end", "tool_call", "tool_result", "decision", "run_end"} <= types,
          str(sorted(types)))
    check("Tool 소요 시간 기록", all("ms" in e["data"] for e in detail["events"] if e["type"] == "tool_result"))
    check("의도 판단과 이유 기록", any(e["type"] == "decision" and e["data"].get("kind") == "intent" and e["data"].get("reason")
                                for e in detail["events"]))
    check("수정 API 없음 (PUT → 405)", c.put(f"/api/logs/{sid}", json={}).status_code == 405)
    check("DELETE /api/logs 204 → 404", c.delete(f"/api/logs/{sid}").status_code == 204 and
          c.get(f"/api/logs/{sid}").status_code == 404)

    # 추천 일정을 계획으로 저장 (F3015)
    item = o2["result"]["itinerary"][0]
    body = {"place_name": item["name"], "city": "서울 종로", "visit_date": SAT,
            "start_time": item["start_time"], "end_time": item["end_time"], "lat": item["lat"], "lon": item["lon"]}
    s1 = c.post("/api/plans", json=body).json()
    s2 = c.post("/api/plans", json=body).json()
    check("추천 일정 저장 + 겹침 알림", s1["overlaps"] == [] and len(s2["overlaps"]) == 1)

    st = c.get("/api/status").json()
    check("/api/status 키 값은 내보내지 않음", all(set(k) == {"name", "label", "set"} for k in st["keys"]))
    check("/api/tools Tool 명세", {t["name"] for t in c.get("/api/tools").json()} >= {"search_places", "get_weather", "search_nearby_food"})

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
