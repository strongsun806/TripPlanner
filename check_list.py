"""체크리스트를 코드로 확인한다 — 3일차 step5_check 와 같은 모양 (통과 · 실패 · 미확인)

    통과    실행해서 확인했고, 맞다
    실패    실행해서 확인했고, 틀리다
    미확인  실행해 보지 못했다 (키가 없다 · 화면에서 봐야 한다)  ← 실행하지 않은 것을 '통과' 로 적지 않는다

실행 (TripPlanner 폴더에서, 서버는 꺼도 된다):
    python check_list.py          외부 API Tool 은 .env 의 진짜 키로, Agent 흐름은 가짜 LLM 으로
    python check_list.py --fake   전부 가짜 (키 · 네트워크 없이)

결과는 화면과 docs/08_체크리스트_결과.md 에 남는다. 확인용 기록은 data/check.db 에 쓰고 실제 일정 DB 는 건드리지 않는다.
"""
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["DB_PATH"] = str(ROOT / "data" / "check.db")
Path(os.environ["DB_PATH"]).unlink(missing_ok=True)
FAKE = "--fake" in sys.argv

RESULTS = []


def check(no, name, cond, note=""):
    RESULTS.append((no, name, "통과" if cond else "실패", note))
    print(f"[{'통과' if cond else '실패'}] {no:<7} {name}" + (f"  — {note}" if note else ""))


def skip(no, name, why):
    RESULTS.append((no, name, "미확인", why))
    print(f"[미확인] {no:<7} {name} — {why}")


def guarded(no, name, fn):
    """확인 코드 자체가 터지면 '실패'가 아니라 원인과 함께 실패로 남긴다"""
    try:
        fn()
    except Exception as e:
        check(no, name, False, f"확인 중 오류 {type(e).__name__}: {str(e)[:80]}")


# ── 4.1 Tool (외부 API) ─────────────────────────────────────
import tools  # noqa: E402

from app.agent.registry import TOOLS, catalog  # noqa: E402

has = lambda k: bool(os.getenv(k))
mode = "가짜 응답" if FAKE else "진짜 API"

check("4.1-a", "Tool 목록이 명세와 같다", {t["name"] for t in catalog()} ==
      {"search_places", "get_weather", "search_nearby_food", "get_schedule", "register_schedule", "search_travel_docs"})

# 네트워크가 필요 없는 확인 — 언제나 진짜 Tool 함수로 (가짜를 끼우기 전에)
bad = [TOOLS["search_places"].run(destination=""), TOOLS["search_places"].run(foo=1),
       TOOLS["get_weather"].run(lat=1, lon=2, visit_date="내일")]
check("4.1-c", "실패 호출도 예외 없이 ok: False", all(b.get("ok") is False for b in bad),
      str([b.get("error", {}).get("code") for b in bad]))
saved = os.environ.pop("VISUALCROSSING_KEY", None)
r = TOOLS["get_weather"].run(lat=37.5, lon=127.0)
check("4.1-d", "키를 비운 호출은 INVALID_KEY", (not r["ok"]) and r["error"]["code"] == "INVALID_KEY")
if saved is not None:
    os.environ["VISUALCROSSING_KEY"] = saved
r = TOOLS["get_weather"].run(lat=37.5, lon=127.0, visit_date=str(date.today() + timedelta(days=30)))
check("4.1-e", "요청 기준(30일 뒤) ≠ 조회 가능 범위 → OUT_OF_RANGE 로 밝힘", (not r["ok"]) and r["error"]["code"] == "OUT_OF_RANGE")

if FAKE:
    sys.path.insert(0, str(ROOT / "tests"))
    import fakes  # noqa: E402
    fakes.install()
    for k in ("GEOAPIFY_KEY", "VISUALCROSSING_KEY", "KAKAO_REST_KEY"):
        os.environ.setdefault(k, "fake")

if has("GEOAPIFY_KEY"):
    r = TOOLS["search_places"].run(destination="서울 종로", limit=3)
    check("4.1-b", f"정상 호출은 ok: True ({mode})", r["ok"] is True, "" if r["ok"] else r["error"]["code"])
else:
    skip("4.1-b", "정상 호출은 ok: True", ".env 에 GEOAPIFY_KEY 없음")
if has("GEOAPIFY_KEY") and has("VISUALCROSSING_KEY"):
    p = TOOLS["search_places"].run(destination="오사카", limit=3)
    if p["ok"]:
        c = p["data"]["center"]
        w = TOOLS["get_weather"].run(lat=c["lat"], lon=c["lon"])
        check("4.1-f", f"후보 Tool 좌표 → 날씨 Tool 입력 ({mode})", w["ok"] is True, "" if w["ok"] else w["error"]["code"])
        check("4.1-g", "날짜를 안 주면 기본값(오늘)을 쓰고 밝힘", w["ok"] and w["data"]["used_default_date"] is True)
    else:
        check("4.1-f", "후보 Tool 좌표 → 날씨 Tool 입력", False, p["error"]["code"])
else:
    skip("4.1-f", "후보 Tool 좌표 → 날씨 Tool 입력", "GEOAPIFY_KEY · VISUALCROSSING_KEY 필요")
    skip("4.1-g", "날짜 기본값", "VISUALCROSSING_KEY 필요")

if has("KAKAO_REST_KEY"):
    if FAKE:
        fakes.FOOD_EMPTY.add("zzqx없는가게")
    r = TOOLS["search_nearby_food"].run(lat=37.5729, lon=126.9794, keyword="zzqx없는가게")
    check("4.1-h", f"보조 정보 결과 없음 → NO_DATA ({mode})", (not r["ok"]) and r["error"]["code"] == "NO_DATA",
          "" if not r["ok"] else "결과가 있음")
else:
    skip("4.1-h", "보조 정보 결과 없음 형식", "KAKAO_REST_KEY 필요")

# ── 이후는 Agent 흐름 — LLM 은 언제나 가짜 (같은 입력 → 같은 결과로 확인) ─────
if not FAKE:
    sys.path.insert(0, str(ROOT / "tests"))
    import fakes  # noqa: E402,F811
    fakes.install()
from fakes import ANALYSES, CALLS, RAIN, SAT, A  # noqa: E402

from app import store  # noqa: E402
from app.agent.events import BUS  # noqa: E402
from app.agent.graph import GRAPH  # noqa: E402
from app.agent.runner import run_agent  # noqa: E402

store.init_db()
calls = lambda o: [e["data"]["tool"] for e in o["steps"] if e["type"] == "tool_call" and not e["data"].get("flow")]

# ── 4.2 메인 Agent ──────────────────────────────────────────
nodes = set(GRAPH.get_graph().nodes) - {"__start__", "__end__"}
check("4.2-a", "그래프가 분석 → 계획 → 실행 → 판단 → 응답", nodes == {"analyze", "plan", "execute", "judge", "respond"}, str(sorted(nodes)))

ANALYSES["c-hi"] = A("smalltalk")
o = run_agent("c-hi", "chk-1")
check("4.2-b", "Tool 이 필요 없는 요청은 계획을 건너뜀 (가짜 LLM)", calls(o) == [])

RAIN[(SAT, "jp")] = 80
CALLS.clear()
ANALYSES["c-rain"] = A("plan_trip", destination="오사카", visit_date=SAT)
o = run_agent("c-rain", "chk-2")
check("4.2-c", "비 예보 → 실내로 재계획 (가짜 LLM)", o["replan_count"] == 1 and o["result"].get("status") == "ok")
check("4.2-d", "재계획 때 이미 받은 날씨는 다시 부르지 않음", sum(1 for c in CALLS if c[0] == "get_weather") == 1)
check("4.2-e", "제외 후보에 사유", all(e.get("reason") for e in o["result"]["excluded"]) and bool(o["result"]["excluded"]))
check("4.2-f", "역산한 기준(늦어도 나서야 할 시각) 제시", bool(o["result"].get("calc", {}).get("늦어도_마지막_장소를_나서는_시각")))

for i in range(15):
    RAIN[((date.fromisoformat(SAT) + timedelta(days=i)).isoformat(), "jp")] = 90
ANALYSES["c-cap"] = A("plan_trip", destination="오사카", visit_date=SAT, outdoor_only=True)
o = run_agent("c-cap", "chk-3")
check("4.2-g", "재계획 상한에서 멈추고 사유와 함께 응답 (NF3004)", o["replan_count"] == 3 and o["result"].get("status") == "cap")
RAIN.clear()

ANALYSES["c-f1"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="13:00", end_time="17:00")
run_agent("c-f1", "chk-4")
ANALYSES["c-f2"] = A("plan_trip", reset=False, destination="오사카")
o = run_agent("c-f2", "chk-4")
check("4.2-h", "후속 요청은 바뀐 조건만 갱신", o["conditions"].get("visit_date") == SAT and o["conditions"].get("start_time") == "13:00")

# ── 4.3 로깅 ─────────────────────────────────────────────────
subs = list(BUS._subscribers)
BUS._subscribers.clear()
ANALYSES["c-nolog"] = A("plan_trip", destination="서울 종로", visit_date=SAT)
o = run_agent("c-nolog", "chk-5")
check("4.3-a [15]", "구독(로깅 Agent) 없이도 Agent 가 돈다", o["ok"] and o["result"].get("status") == "ok")
BUS._subscribers.extend(subs)
src = (ROOT / "app" / "logging_agent.py").read_text(encoding="utf-8")
check("4.3-b", "로깅 Agent 는 LLM 을 쓰지 않는다 (코드에 LLM import 없음)", "llm" not in src.split('"""')[-1].lower())


def api_checks():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        ANALYSES["c-a1"] = A("plan_trip", destination="서울 종로", visit_date=SAT)
        s1 = c.post("/api/chat", json={"message": "c-a1"}).json()["session_id"]
        ANALYSES["c-a2"] = A("smalltalk")
        s2 = c.post("/api/chat", json={"message": "c-a2"}).json()["session_id"]
        import time
        time.sleep(0.3)
        ev = c.get(f"/api/logs/{s1}").json()["events"]
        check("4.3-c", "세션 단위 저장, 다른 세션과 섞이지 않음", s1 != s2 and all(e["session_id"] == s1 for e in ev))
        check("4.3-d", "상세는 실행 순서대로", [e["seq"] for e in ev] == sorted(e["seq"] for e in ev))
        types = {e["type"] for e in ev}
        check("4.3-e", "단계 진입 · 종료, Tool 요청 · 응답 · 소요 시간 이벤트",
              {"node_start", "node_end", "tool_call", "tool_result"} <= types and all("ms" in e["data"] for e in ev if e["type"] == "tool_result"))
        check("4.3-f", "LLM 판단과 이유가 이벤트에", any(e["type"] == "decision" and e["data"].get("kind") == "intent" and e["data"].get("reason") for e in ev))
        check("4.3-g", "실행 로그 수정 API 없음 (PUT → 405)", c.put(f"/api/logs/{s1}", json={}).status_code == 405)
        check("4.3-h", "실행 로그 삭제", c.delete(f"/api/logs/{s1}").status_code == 204 and c.get(f"/api/logs/{s1}").status_code == 404)

        body = {"place_name": "경복궁", "city": "서울", "visit_date": SAT, "start_time": "14:00", "end_time": "16:00"}
        a = c.post("/api/plans", json=body).json()
        b = c.post("/api/plans", json={**body, "place_name": "광장시장", "start_time": "15:00", "end_time": "17:00"}).json()
        check("4.4-a", "핵심 값 · 생성 일시 저장", bool(a["plan"]["created_at"]) and a["plan"]["place_name"] == "경복궁")
        check("4.4-b", "겹치면 저장하고 겹치는 일정을 알림", b["plan"]["plan_id"] and len(b["overlaps"]) == 1)
        u = c.put(f'/api/plans/{a["plan"]["plan_id"]}', json={**body, "start_time": "10:00", "end_time": "11:00"})
        check("4.4-c", "수정 · 삭제", u.status_code == 200 and c.delete(f'/api/plans/{a["plan"]["plan_id"]}').status_code == 204)

        paths = c.get("/openapi.json").json()["paths"]
        check("4.5-a", "대화 · 계획 · 로그 API 가 다른 경로", {"/api/chat", "/api/plans", "/api/logs"} <= set(paths))


guarded("4.3~4.5", "API 확인", api_checks)
skip("4.5-b", "대화 · 내 일정 화면이 API 로 표시", "화면에서 직접 확인 (docs/07)")
skip("4.5-c", "실행 단계 · 재계획 지점 · 처리 중 · 키 누락 안내 표시", "화면에서 직접 확인 (docs/07)")

# ── 심화 ─────────────────────────────────────────────────────
from app import evaluator  # noqa: E402

ev_ = lambda seq, type_, **d: {"seq": seq, "type": type_, "node": None, "data": d}
check("4.6-F3020a", "근거 이벤트가 없으면 '판정 불가'", evaluator.judge_events([])[0] == "undecidable")
bad = [ev_(1, "run_start"), ev_(2, "decision", kind="plan", steps=["get_weather"]),
       ev_(3, "tool_call", tool="get_weather", args={"a": 1}), ev_(4, "tool_result", tool="get_weather", ok=False, code="NO_DATA"),
       ev_(5, "tool_call", tool="get_weather", args={"a": 1}), ev_(6, "tool_result", tool="get_weather", ok=False, code="NO_DATA"),
       ev_(7, "tool_call", tool="search_places", args={}), ev_(8, "decision", kind="replan_cap"), ev_(9, "run_end")]
codes = {w["code"] for w in evaluator.judge_events(bad)[1]}
check("4.6-F3020b", "규칙 넷: 계획 밖 · 반복 · 과다 재계획 · 연속 실패",
      codes == {"UNPLANNED_CALL", "REPEATED_CALL", "EXCESSIVE_REPLAN", "FAIL_STREAK"}, str(sorted(codes)))

store.calendar_add("점심 약속", SAT, "12:00", "14:00")
ANALYSES["c-cal"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="10:00", end_time="18:00")
o = run_agent("c-cal", "chk-6")
check("4.6-F3023", "조회한 내 일정을 피해 일정 배치", all(i["start_time"] >= "14:00" for i in o["result"].get("itinerary", [{"start_time": "0"}])))

from app import rag  # noqa: E402
if rag.available_collections():
    r = TOOLS["search_travel_docs"].run(query="가을 일본 미식")
    check("4.6-F3022", "문서 검색이 근거 문서 · 출처를 돌려줌", r["ok"] and all(h.get("source") for h in r["data"]["hits"]))
else:
    r = TOOLS["search_travel_docs"].run(query="가을 일본 미식")
    check("4.6-F3022", "색인이 없으면 NO_INDEX", (not r["ok"]) and r["error"]["code"] == "NO_INDEX", "rag_index.py 로 색인하면 검색까지 확인")
cmp_doc = ROOT / "docs" / "05_구조_비교.md"
if not cmp_doc.exists():
    skip("4.6-F3021", "Planner/Worker 구조 비교", "python compare_modes.py 로 docs/05 생성")
else:
    rows = [ln.split("|")[1:-1] for ln in cmp_doc.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| ") and ln.count("|") == 8 and not ln.startswith("| 대표") and not ln.startswith("|---")]
    rows = [[c.strip() for c in r] for r in rows]
    modes = {r[1] for r in rows}
    errors = [r for r in rows if r[2].startswith("오류")]
    normal = [r for r in rows if r[0] in ("제약을 한 번에 충족", "Tool 이 필요 없음")]
    worker_quiet = all("(0)" in r[4] for r in normal)
    by_case = {}
    for r in rows:
        by_case.setdefault((r[0], r[1]), r[3])
    same_replan = all(by_case.get((c, "single")) == by_case.get((c, "planner_worker"))
                      for c in {r[0] for r in rows} if c != "Tool 하나가 실패")
    ok = {"single", "planner_worker"} <= modes and not errors and worker_quiet and same_replan
    check("4.6-F3021", "Planner/Worker 구조 비교 (docs/05, 실제 LLM)", ok,
          f"{len(rows)}건 · 오류 {len(errors)} · 정상 경로 Worker LLM 0: {worker_quiet} · 재계획 일치: {same_replan}")

# ── 비기능 ───────────────────────────────────────────────────
check("NF3002 [25]", "오류 문구에 키가 남지 않는다", "abcd1234" not in tools.scrub("...?key=abcd1234&apiKey=abcd1234"))

env = ROOT / ".env"
tracked = []
try:
    rc = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT, capture_output=True).returncode
    if rc == 128:                                   # git 저장소가 아니다 → 확인할 수 없다
        skip("NF3001-a", ".env 는 git 이 무시한다", "git 저장소가 아님")
    else:
        check("NF3001-a", ".env 는 git 이 무시한다", rc == 0)
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
except FileNotFoundError:
    skip("NF3001-a", ".env 는 git 이 무시한다", "git 이 없음")
if env.exists() and tracked:
    secrets = [line.split("=", 1)[1].strip().strip('"\'') for line in env.read_text(encoding="utf-8").splitlines()
               if "=" in line and not line.strip().startswith("#") and "KEY" in line.split("=", 1)[0]]
    secrets = [s for s in secrets if len(s) >= 8]
    leaked = [f for f in tracked if (ROOT / f).is_file() and (ROOT / f).stat().st_size < 2_000_000 and
              any(s in (ROOT / f).read_text(encoding="utf-8", errors="ignore") for s in secrets)]
    check("NF3001-b", "커밋된 파일에 .env 의 키 값이 없다", not leaked, ", ".join(leaked))
else:
    skip("NF3001-b", "커밋된 파일에 키 값이 없다", ".env 또는 git 기록 없음")

# ── 결과 저장 ────────────────────────────────────────────────
counts = {k: sum(1 for r in RESULTS if r[2] == k) for k in ("통과", "실패", "미확인")}
print(f"\n통과 {counts['통과']} · 실패 {counts['실패']} · 미확인 {counts['미확인']}")
lines = ["# 체크리스트 결과 — 코드로 확인 (통과 · 실패 · 미확인)", "",
         f"> {datetime.now():%Y-%m-%d %H:%M} · `python check_list.py{' --fake' if FAKE else ''}` · "
         f"외부 API Tool: {'가짜 응답' if FAKE else '진짜 키'} · Agent 흐름: 가짜 LLM", "",
         f"통과 {counts['통과']} · 실패 {counts['실패']} · 미확인 {counts['미확인']} — 미확인은 실행하지 못한 항목이며 통과로 세지 않는다.", "",
         "| 번호 | 항목 | 결과 | 근거 · 사유 |", "|---|---|---|---|"]
lines += [f"| {n} | {name} | {res} | {note} |" for n, name, res, note in RESULTS]
(ROOT / "docs" / "08_체크리스트_결과.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("저장: docs/08_체크리스트_결과.md")
Path(os.environ["DB_PATH"]).unlink(missing_ok=True)
sys.exit(1 if counts["실패"] else 0)
