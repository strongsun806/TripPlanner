"""검증 증빙 모으기 — 서버(8000)와 화면(5500)이 켜진 상태에서 실행한다 (start.bat 먼저).

    python capture_evidence.py

하는 일 (실제 LLM · 외부 API 사용)
  1) API 로 대표 요청을 보내 결과를 docs/evidence/ 에 JSON 으로 남긴다 (후속 요청 · 저장 · 겹침 · 캘린더 포함)
  2) 크롬(없으면 엣지)을 화면 없이 띄워 각 화면을 docs/images/ 에 PNG 로 찍는다
     대화 화면은 chat.html?q=… 로 열어 실제 요청이 끝난 뒤의 모습을 찍는다
  3) 그래프 구조, check_list.py 결과를 docs/evidence/ 에 텍스트로 남긴다
키 값은 어디에도 쓰지 않는다 (응답 · 오류는 서버가 이미 가린 것).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parent
IMG = ROOT / "docs" / "images"
EVD = ROOT / "docs" / "evidence"
API = "http://localhost:8000"
UI = "http://localhost:5500"
IMG.mkdir(parents=True, exist_ok=True)
EVD.mkdir(parents=True, exist_ok=True)
client = httpx.Client(base_url=API, timeout=180)


def log(*a):
    print(*a, flush=True)


def save_json(name, obj):
    (EVD / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def find_browser():
    cands = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        shutil.which("chrome") or "", shutil.which("msedge") or "",
    ]
    return next((c for c in cands if c and Path(c).exists()), None)


BROWSER = find_browser()


def shot(url, name, w=1440, h=1000, budget=25000):
    """화면 없이 열어 찍는다. 요청이 오가는 동안은 가상 시간이 멈춰서, LLM 응답이 온 뒤에 찍힌다."""
    if not BROWSER:
        log("  [건너뜀] 크롬 · 엣지를 찾지 못함:", name)
        return False
    out = IMG / name
    prof = tempfile.mkdtemp(prefix="tp_shot_")          # 매번 새 프로필 → 새 대화 세션
    cmd = [BROWSER, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
           "--no-default-browser-check", f"--user-data-dir={prof}", f"--window-size={w},{h}",
           f"--virtual-time-budget={budget}", f"--screenshot={out}", url]
    t = time.time()
    try:
        subprocess.run(cmd, timeout=240, capture_output=True)
    except subprocess.TimeoutExpired:
        log("  [시간 초과]", name)
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    ok = out.exists() and out.stat().st_mtime >= t - 1
    log(f"  {'찍음' if ok else '[실패]'} {name} ({time.time() - t:.0f}s)")
    return ok


def chat(message, session_id=None):
    r = client.post("/api/chat", json={"message": message, "session_id": session_id})
    r.raise_for_status()
    out = r.json()
    log(f"  chat ok={out['ok']} intent={out.get('intent')} 재계획={out.get('replan_count')} "
        f"{(out.get('error') or {}).get('code', '')} — {message[:30]}")
    return out


def main():
    # 0) 서버 확인
    try:
        st = client.get("/api/status").json()
        httpx.get(UI, timeout=5).raise_for_status()
    except Exception as e:
        log("서버(8000) 또는 화면(5500)이 꺼져 있어요. start.bat 를 먼저 실행하세요.", type(e).__name__)
        sys.exit(1)
    log("서버 확인:", ", ".join(f"{k['name']}={'O' if k['set'] else 'X'}" for k in st["keys"]), "· 모델", st["settings"]["model"])
    log("브라우저:", BROWSER or "없음")

    # 1) API 로 대표 요청 — 후속 요청은 바뀐 조건만 갱신 (S6)
    log("\n[1] API 대표 요청")
    a = chat("다음 주 토요일 오사카, 오후에 야외 위주로 돌고 싶어. 비 오면 곤란해")
    save_json("chat_osaka.json", a)
    visit = (a.get("conditions") or {}).get("visit_date") or str(date.today() + timedelta(days=8))
    b = chat("같은 날 서울 종로로 바꿔줘", a["session_id"])
    save_json("chat_followup_jongno.json", {"before": a.get("conditions"), "after": b.get("conditions"),
                                             "session_id": b["session_id"], "reply": b.get("reply")})

    # 캘린더 약속 (F3023) — 그날 15~16시를 비워 두고 짜는지 대화 화면에서 본다
    client.post("/api/calendar", json={"title": "친구와 커피", "event_date": visit,
                                       "start_time": "15:00", "end_time": "16:00"})

    # 계획 저장 · 겹침 (F3015) — 대화 카드의 '이 일정 저장'과 같은 요청
    saved, clashes = [], []
    it = ((a.get("result") or {}).get("itinerary")) or []
    city = (a.get("result") or {}).get("city") or (a.get("result") or {}).get("destination") or "오사카"
    for s in it:
        r = client.post("/api/plans", json={"place_name": s["name"], "city": city, "visit_date": visit,
                                            "start_time": s["start_time"], "end_time": s["end_time"],
                                            "lat": s.get("lat"), "lon": s.get("lon")})
        if r.status_code == 201:
            saved.append(r.json()["plan"])
    if it:
        r = client.post("/api/plans", json={"place_name": "오사카성 야경", "city": city, "visit_date": visit,
                                            "start_time": it[0]["start_time"], "end_time": it[0]["end_time"],
                                            "memo": "겹침 확인용"})
        clashes = r.json().get("overlaps", [])
    save_json("plans_saved.json", {"saved": saved, "overlap_alert": clashes,
                                   "list": client.get("/api/plans").json()})
    log(f"  일정 저장 {len(saved)}건 · 겹침 알림 {len(clashes)}건")

    # 2) 화면 캡처 — 대화 화면은 실제 요청이 끝난 뒤
    log("\n[2] 화면 캡처")
    shot(f"{UI}/index.html", "4-5_home.png", 1440, 1700, 6000)
    scenarios = [
        ("4-2_chat_osaka_replan.png", "다음 주 토요일 오사카, 오후에 야외 위주로 돌고 싶어. 비 오면 곤란해"),
        ("4-2_chat_jongno_food.png", "이번 주말 서울 종로에서 2시부터 6시까지 돌아볼 곳, 저녁 먹을 곳도 알려줘"),
        ("4-2_chat_replan_cap.png", "다음 주 토요일 오사카, 무조건 야외로만, 비 안 오는 날로. 날짜는 바꾸지 마"),
        ("4-2_chat_smalltalk.png", "안녕! 너는 뭘 할 수 있어?"),
        ("4-6_chat_rag.png", "가을에 가기 좋은 일본 미식 여행지 추천해줘"),
    ]
    for name, q in scenarios:
        shot(f"{UI}/chat.html?q={quote(q)}", name, 1440, 1000, 30000)
    shot(f"{UI}/plans.html?date={visit}", "4-4_plans_clash.png", 1440, 1400, 6000)

    # 실행 로그 — 상한 도달(이상 판정) 세션이 있으면 그 세션을 연다
    sessions = client.get("/api/logs").json()
    pick = next((s for s in sessions if "무조건 야외" in (s.get("first_message") or "")), sessions[0] if sessions else None)
    if pick:
        time.sleep(3)   # 이상 판정 뒤 LLM 진단이 저장될 시간
        shot(f"{UI}/logs.html?session={quote(pick['session_id'])}", "4-3_logs.png", 1440, 1600, 8000)
        save_json("log_session.json", client.get(f"/api/logs/{pick['session_id']}").json())
    shot(f"{API}/docs", "4-5_api_docs.png", 1440, 1500, 8000)

    # 3) 텍스트 증빙
    log("\n[3] 텍스트 증빙")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    g = subprocess.run([sys.executable, "-c", "from app.agent.graph import GRAPH; print(GRAPH.get_graph().draw_mermaid())"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
    (EVD / "graph_mermaid.md").write_text("```mermaid\n" + g.stdout + "```\n", encoding="utf-8")
    c = subprocess.run([sys.executable, "check_list.py"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
    (EVD / "check_list_output.txt").write_text(c.stdout + c.stderr, encoding="utf-8")
    log(c.stdout.strip().splitlines()[-2] if c.stdout.strip() else "check_list 출력 없음")
    log("\n끝. docs/images · docs/evidence 를 확인하세요.")


if __name__ == "__main__":
    main()
