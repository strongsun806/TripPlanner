"""오프라인 시험 — 셀프 체크리스트에서 실제 API 로는 만들기 어려운 경우 (키 · 네트워크 불필요)

실행 (TripPlanner 폴더에서):  python tests/test_checklist_offline.py

    G1 (체크 3 · F3003)  판단 속성(강수확률)이 비면 0 으로 채우지 않고 판단을 보류한 채 일정을 짠다
    G2 (체크 17 · F3013) 로그 저장이 실패해도 대화는 정상
    G3 (체크 17 · F3013) 저장된 로그 = 발행된 이벤트 그대로 (LLM 요약 · 가공 없음)
    G4 (체크 32 · F3023) 캘린더 구현체를 Google 로 바꿔도 Tool 입출력 형식이 같다 (Google API 는 가짜 서비스)
"""
import os
import sys
from pathlib import Path

os.environ["DB_PATH"] = str(Path(__file__).resolve().parent.parent / "data" / "test_checklist.db")
Path(os.environ["DB_PATH"]).unlink(missing_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fakes import ANALYSES, SAT, A, install  # noqa: E402

install()
import tools  # noqa: E402
from app import store  # noqa: E402
from app.agent import registry  # noqa: E402
from app.agent.events import BUS  # noqa: E402
from app.agent.runner import run_agent  # noqa: E402
from app.logging_agent import LoggingAgent  # noqa: E402

store.init_db()
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


# G1 강수확률이 비어 있음 → NO_FORECAST → 날씨 판단 보류, 일정은 짠다 (0 으로 채우지 않음)
orig_weather = registry.TOOLS["get_weather"].func
registry.TOOLS["get_weather"].func = lambda lat, lon, visit_date=None: tools.fail(
    "NO_FORECAST", f"{visit_date} 강수확률이 비어 있어 날씨를 판단하지 않습니다.")
ANALYSES["g1"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="14:00", end_time="18:00")
o = run_agent("g1", "chk-1")
w = o["result"].get("weather") or {}
check("G1 빈 속성은 판단 보류 · 일정은 진행", o["ok"] and w.get("status") == "unknown"
      and w.get("precip_prob") is None and o["result"].get("itinerary"),
      f'weather={w.get("status")}, precip={w.get("precip_prob")}, 일정 {len(o["result"].get("itinerary", []))}곳')
registry.TOOLS["get_weather"].func = orig_weather

# G2 로그 저장 실패 — 로깅 Agent 는 실패만 세고, 대화는 정상
agent = LoggingAgent()
agent.attach(BUS)
orig_append = store.append_event
store.append_event = lambda e: (_ for _ in ()).throw(RuntimeError("디스크 가득 참 (시험)"))
ANALYSES["g2"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="14:00", end_time="18:00")
o = run_agent("g2", "chk-2")
check("G2 로그 저장 실패해도 대화 정상", o["ok"] and o["result"].get("itinerary") and agent.failed > 0 and agent.saved == 0,
      f"저장 실패 {agent.failed}건, 응답 ok={o['ok']}")
store.append_event = orig_append

# G3 저장된 로그가 발행된 이벤트와 같다 — 순서 · 종류 · 내용 그대로
agent.saved = agent.failed = 0
ANALYSES["g3"] = A("plan_trip", destination="서울 종로", visit_date=SAT, start_time="14:00", end_time="18:00")
o = run_agent("g3", "chk-3")
stored = store.session_events("chk-3")
emitted = o["steps"]
same = len(stored) == len(emitted) and all(
    s["seq"] == e["seq"] and s["type"] == e["type"] and s["node"] == e["node"] and s["data"] == e["data"]
    for s, e in zip(stored, emitted))
check("G3 저장된 로그 = 발행된 이벤트 (가공 없음)", same, f"발행 {len(emitted)}건 · 저장 {len(stored)}건")
agent.detach()

# G4 캘린더 구현체 교체 — Local 과 Google(가짜 서비스)의 list_events · add_event 출력 키가 같다
from app.calendar_backend import GoogleCalendar, LocalCalendar  # noqa: E402


class FakeEvents:
    def __init__(self):
        self.items = []

    def list(self, **kw):
        items = self.items
        if kw.get("privateExtendedProperty"):
            pid = kw["privateExtendedProperty"].split("=")[1]
            items = [i for i in items if i.get("extendedProperties", {}).get("private", {}).get("tripplanner_plan_id") == pid]
        return type("Req", (), {"execute": lambda self_: {"items": items}})()

    def insert(self, calendarId, body):
        ev = {**body, "id": f"g{len(self.items) + 1}", "htmlLink": "https://calendar.google.com/x",
              "start": {"dateTime": body["start"]["dateTime"] + "+09:00"},
              "end": {"dateTime": body["end"]["dateTime"] + "+09:00"}}
        self.items.append(ev)
        return type("Req", (), {"execute": lambda self_: ev})()


class FakeService:
    def __init__(self):
        self._events = FakeEvents()

    def events(self):
        return self._events


local, google = LocalCalendar(), GoogleCalendar()
google._service = FakeService()
a_l = local.add_event("친구 점심", SAT, "12:00", "13:00")
a_g = google.add_event("친구 점심", SAT, "12:00", "13:00")
l_l, l_g = local.list_events(SAT), google.list_events(SAT)
need_add = {"event_id", "title", "event_date", "start_time", "end_time"}
need_list = {"title", "start_time", "end_time", "source", "plan_id"}
check("G4 add_event 출력 형식 같음", need_add <= set(a_l) and need_add <= set(a_g)
      and (a_l["start_time"], a_l["end_time"]) == (a_g["start_time"], a_g["end_time"]), f"{sorted(a_g)}")
check("G4 list_events 출력 형식 같음 (판단에 쓰는 시각까지)", l_l and l_g and set(l_l[0]) == set(l_g[0]) == need_list
      and (l_l[0]["start_time"], l_l[0]["end_time"]) == (l_g[0]["start_time"], l_g[0]["end_time"]),
      f"local {l_l[0]} · google {l_g[0]}")

print(f"\n{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
