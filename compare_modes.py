"""구조 비교 실험 (심화 F3021) + 대표 시나리오 실행 (검증 4.7)

같은 대표 요청을 세 구성으로 돌려 재계획 횟수 · 요청당 LLM 호출 수 · 응답 시간 · Tool 호출 수를 비교하고
docs/05_구조_비교.md 로 저장한다. 실행 로그도 data/trip.db 에 남아 '실행 로그' 화면에서 볼 수 있다.

    single              기존 메인 Agent (analyze → plan → execute → judge → respond)
    planner_worker      Planner / Worker 분리, Worker LLM 켬
    planner_worker_off  Planner / Worker 분리, Worker LLM 끔

실행 (TripPlanner 폴더에서, 서버는 꺼도 된다):
    python compare_modes.py            실제 키로 실행 (LLM · 외부 API 호출 약 15건 × 요청)
    python compare_modes.py --offline  가짜 LLM · 외부 API 로 흐름만 확인 (키 불필요)
"""
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OFFLINE = "--offline" in sys.argv
if OFFLINE:
    os.environ["DB_PATH"] = str(ROOT / "data" / "compare_offline.db")
    sys.path.insert(0, str(ROOT / "tests"))
    from fakes import ANALYSES, FOOD_EMPTY, RAIN, SAT, A, install
    install()

from app import config, store  # noqa: E402
from app.agent.events import BUS  # noqa: E402
from app.agent.runner import run_agent  # noqa: E402
from app.logging_agent import LoggingAgent  # noqa: E402

REQUESTS = [
    ("제약을 한 번에 충족", "이번 주말 서울 종로에서 2시부터 6시까지 돌아볼 곳, 저녁 먹을 곳도 알려줘"),
    ("재계획이 필요", "다음 주 토요일 오사카, 오후에 야외 위주로 돌고 싶어. 비 오면 곤란해"),
    ("상한에 도달", "오사카에서 무조건 야외로만, 비 안 오는 날 하루 골라서 일정 짜줘"),
    ("Tool 하나가 실패", "이번 주말 서울 종로에서 2시부터 5시까지, 근처 zzqx식당도 찾아줘"),
    ("Tool 이 필요 없음", "안녕! 너는 뭘 할 수 있어?"),
]
MODES = [("single", "single", True), ("planner_worker", "planner_worker", True),
         ("planner_worker_off", "planner_worker", False)]

if OFFLINE:   # 가짜 응답 — 실제 실행에서는 LLM 이 요청을 분석한다
    RAIN.update({(SAT, "jp"): 80})
    FOOD_EMPTY.add("맛집")         # 기본 검색어로는 결과 없음 → Worker 가 검색어를 고쳐 재시도하는지 본다
    from datetime import timedelta
    for i in range(1, 15):
        RAIN[((date.fromisoformat(SAT) + timedelta(days=i)).isoformat(), "jp")] = 85
    ANALYSES[REQUESTS[0][1]] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="14:00", end_time="18:00",
                                 want_food=True, food_keyword="한식")
    ANALYSES[REQUESTS[1][1]] = A("plan_trip", destination="오사카", visit_date=SAT, start_time="13:00", style="outdoor")
    ANALYSES[REQUESTS[2][1]] = A("plan_trip", destination="오사카", visit_date=SAT, outdoor_only=True)
    ANALYSES[REQUESTS[3][1]] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="14:00", end_time="17:00",
                                 want_food=True)
    ANALYSES[REQUESTS[4][1]] = A("smalltalk")


PAUSE = float(os.environ.get("COMPARE_PAUSE", "0" if OFFLINE else "4"))   # GMS 요청 한도를 피하려고 실행 사이에 쉰다


def measure(out):
    st = out["steps"]
    status = out["result"].get("status") or out["result"].get("kind") or "-"
    if out.get("error"):
        status = f'오류 {out["error"]["code"]}'
    return {"ok": out["ok"], "status": status,
            "replans": out["replan_count"], "llm": sum(e["type"] == "llm_call" for e in st),
            "worker_llm": sum(e["type"] == "llm_call" and e["node"] == "worker" for e in st),
            "tools": sum(e["type"] == "tool_call" and not e["data"].get("flow") for e in st),
            "ms": out["ms"], "session": out["session_id"]}


def main():
    store.init_db()
    logger = LoggingAgent()
    logger.attach(BUS)                       # 실행 로그를 남겨 '실행 로그' 화면에서 볼 수 있게
    rows = {}
    stamp = datetime.now().strftime("%m%d%H%M")
    llm_fail = 0
    for mode_name, mode, worker_llm in MODES:
        config.AGENT_MODE, config.WORKER_LLM = mode, worker_llm
        for label, msg in REQUESTS:
            if rows and PAUSE:
                time.sleep(PAUSE)
            out = run_agent(msg, f"cmp-{stamp}-{mode_name}-{REQUESTS.index((label, msg)) + 1}")
            if out.get("error") and out["error"]["code"].startswith("LLM"):
                llm_fail += 1
                if llm_fail >= 2 and len(rows) <= 2:            # 처음부터 LLM 이 안 되면 비교가 의미 없다
                    print(f"\nLLM 호출이 계속 실패합니다: {out['error']['message']}")
                    print("python llm_check.py 로 GMS 연결을 먼저 확인하세요. 결과표는 쓰지 않았습니다.")
                    logger.detach()
                    sys.exit(1)
            rows[(mode_name, label)] = measure(out)
            m = rows[(mode_name, label)]
            print(f"{mode_name:<19} {label:<12} 상태={m['status']:<10} 재계획={m['replans']} LLM={m['llm']}"
                  f"(Worker {m['worker_llm']}) Tool={m['tools']} {m['ms']}ms")
    logger.detach()
    write_report(rows)


def write_report(rows):
    lines = [f"# 구조 비교 — 단일 Agent vs Planner/Worker (심화 F3021)", "",
             f"> 실행: {datetime.now():%Y-%m-%d %H:%M} · {'가짜 LLM · 외부 API (흐름 확인용)' if OFFLINE else '실제 LLM · 외부 API'}"
             f" · 모델 {config.GMS_MODEL} · `python compare_modes.py{' --offline' if OFFLINE else ''}`", "",
             "| 대표 요청 | 구성 | 결과 | 재계획 | LLM 호출 (Worker) | Tool 호출 | 응답 시간 |", "|---|---|---|---|---|---|---|"]
    for label, _ in REQUESTS:
        for mode_name, _, _ in MODES:
            m = rows[(mode_name, label)]
            lines.append(f"| {label} | {mode_name} | {m['status']} | {m['replans']} | {m['llm']} ({m['worker_llm']}) | "
                         f"{m['tools']} | {m['ms'] / 1000:.1f}s |")
    lines += ["", "## 구성별 평균", "", "| 구성 | 재계획 | LLM 호출 | Worker LLM | Tool 호출 | 응답 시간 |", "|---|---|---|---|---|---|"]
    for mode_name, _, _ in MODES:
        ms = [rows[(mode_name, l)] for l, _ in REQUESTS]
        avg = lambda k: sum(x[k] for x in ms) / len(ms)
        lines.append(f"| {mode_name} | {avg('replans'):.1f} | {avg('llm'):.1f} | {avg('worker_llm'):.1f} | "
                     f"{avg('tools'):.1f} | {avg('ms') / 1000:.1f}s |")
    lines += ["", "## 읽는 법", "",
              "- 정상 경로(제약을 한 번에 충족 · Tool 불필요)에서 Worker LLM 호출이 0 이어야 한다.",
              "- 'Tool 하나가 실패'에서 Worker LLM 을 켜면 검색어를 고쳐 같은 Tool 을 다시 부르고(LLM 호출 +1), 끄면 실패를 그대로 보고한다.",
              "- 재계획 횟수는 두 구조가 같아야 정상이다 — 재계획 판단(judge)은 같은 규칙을 쓴다.",
              "- 실행 로그 화면에서 세션 `cmp-…` 를 열면 각 실행의 단계 · Tool 호출 · Planner/Worker 메시지를 볼 수 있다.",
              "- 결과가 `오류 LLM_…` 인 줄은 GMS 호출이 실패한 실행이다 — 비교에서 빼고 읽는다."]
    path = ROOT / "docs" / ("05_구조_비교_offline.md" if OFFLINE else "05_구조_비교.md")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n저장: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
