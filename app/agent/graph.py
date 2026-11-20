"""메인 Agent 실행 흐름 (F3005 ~ F3011) — LangGraph

    START → analyze ─┬─(Tool 필요)→ plan → execute → judge ─┬─(재계획)→ plan …
                     │                                      └─(확정 · 중단)→ respond → END
                     └─(인사 · 되묻기 · 오류)────────────────────────────────→ respond → END

    analyze   LLM   요청 → 의도 · 조건 추출, Memory 와 합치기, 모자란 조건이면 되묻기       F3005 · F3010
    plan      규칙  의도와 현재 전략(실내/야외 · 반경 · 날짜)으로 Tool 호출 순서 구성     F3006
    execute   규칙  Tool 호출, 앞 결과를 다음 입력으로, 실패는 기록하고 진행, 결과 캐시    F3007
    judge     계산  날씨 · 동선 · 시간창 판단, 재계획 여부 결정 (상한 MAX_REPLAN)          F3008 · F3009
    respond   LLM   확정된 사실만으로 답변 작성 (LLM 실패 시 규칙 기반 문장으로 대체)       F3011

모든 단계는 진입 · 종료를 이벤트로 발행하고, Tool 호출 · 판단도 이벤트로 남긴다 (F3012).
"""
import json
from datetime import date, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .. import config
from . import judge as J
from . import llm, memory
from .events import RunEmitter, Timer
from .registry import TOOLS, geocode


class State(TypedDict, total=False):
    emitter: Any                 # RunEmitter — 발행 도우미 (그래프 밖에서 만든다)
    session_id: str
    message: str
    memory: dict                 # 직전 조건 · 요약 (원본 응답은 없다)
    last_evaluation: dict | None # 직전 실행의 평가 (심화 F3020)
    # analyze
    intent: str
    reason: str
    conditions: dict
    assumptions: list
    defaults: list               # 기본값을 쓴 내용 — 응답에 밝힌다
    clarify: str | None          # 되물을 질문
    plan_id: int | None
    question: str | None
    llm_tools: list              # LLM 이 Tool 목록을 보고 고른 Tool (2일차 Plan.tools)
    # 현재 전략 — 재계획 때 바뀐다
    style: str
    radius: int
    visit_date: str | None
    location: dict | None        # 여행지 중심 좌표 (geocode)
    # plan · execute
    steps: list                  # [{"tool", "args"}]
    plan_reason: str
    cache: dict                  # 같은 Tool · 같은 인자는 다시 부르지 않는다
    results: dict                # Tool 이름 → 최신 결과
    evidence: list               # 응답에 근거로 보일 Tool 결과 요약
    # judge
    replan_count: int
    replans: list                # [{"n", "reason", "change"}]
    excluded: list               # 제외한 후보와 사유 (재계획 전 것 포함)
    verdict: dict
    outcome: dict                # respond 로 넘길 판단 결과
    # respond
    reply: str
    result: dict
    # Planner / Worker (심화 F3021)
    tasks: list
    replan_requests: list
    reviewed: bool


# ── 공통: 단계 진입 · 종료 이벤트 ─────────────────────────────
def step(name):
    def wrap(fn):
        def run(state: State):
            em: RunEmitter = state["emitter"]
            em.emit("node_start", name)
            with Timer() as t:
                try:
                    update = fn(state, em) or {}
                except (llm.LLMUnavailable, llm.LLMError):
                    raise
                except Exception as e:
                    em.emit("error", name, error=type(e).__name__, message=str(e)[:200])
                    raise
            em.emit("node_end", name, ms=t.ms, summary=_summary(name, {**state, **update}))
            return update
        run.__name__ = name
        return run
    return wrap


def _summary(name, s):
    if name == "analyze":
        return {"intent": s.get("intent"), "clarify": bool(s.get("clarify"))}
    if name == "plan":
        return {"steps": [x["tool"] for x in s.get("steps", [])]}
    if name == "execute":
        return {"results": {k: v.get("ok") for k, v in s.get("results", {}).items()}}
    if name == "judge":
        return {"action": s.get("verdict", {}).get("action")}
    return {}


def _brief(tool: str, r: dict) -> str:
    """Tool 결과 한 줄 요약 — 이벤트와 응답 근거에 쓴다 (원본 응답은 넘기지 않는다)"""
    if not r.get("ok"):
        return f'{r["error"]["code"]}: {r["error"]["message"]}'
    d = r["data"]
    if tool == "search_places":
        return f'{d["style"]} 후보 {len(d["places"])}곳 (반경 {d.get("radius_m", 5000) // 1000}km)'
    if tool == "get_weather":
        return f'{d["date"]} 강수확률 {d["precip_prob"]:.0f}%, {d.get("conditions") or ""}'.strip(", ")
    if tool == "search_nearby_food":
        return f'{d["keyword"]} {len(d["places"])}곳'
    if tool == "get_schedule":
        return f'{d["date"]} 일정 {len(d["events"])}건'
    if tool == "register_schedule":
        return f'캘린더 등록: {d.get("title")}'
    if tool == "search_travel_docs":
        return f'문서 {len(d.get("hits", []))}건'
    return "ok"


# ── analyze (LLM) ────────────────────────────────────────────
NEEDS_DESTINATION = {"plan_trip", "weather", "food"}


@step("analyze")
def analyze(state: State, em: RunEmitter):
    mem = state["memory"]
    with Timer() as lt:
        a = llm.analyze(state["message"], mem["conditions"], mem["last_summary"], mem["turns"])
    em.emit("llm_call", "analyze", purpose="요청 분석", ms=lt.ms)
    conds, changed = memory.merge_conditions(mem["conditions"], a.changes.model_dump(), a.reset)
    em.emit("decision", "analyze", kind="intent", intent=a.intent, reason=a.reason,
            changed=changed, conditions=conds, assumptions=a.assumptions, tools=a.tools)
    last_eval = state.get("last_evaluation")
    if last_eval and last_eval.get("verdict") == "anomaly":     # 로깅 Agent 평가를 참조한다 (F3020)
        em.emit("decision", "analyze", kind="evaluation_ref", verdict="anomaly",
                codes=[w["code"] for w in last_eval["warnings"]],
                reason="직전 실행이 이상으로 판정됨 — 이번 실행에서 같은 Tool·인자 재호출을 캐시로 막고 재계획 상한을 지킴")

    clarify, defaults = None, []
    if a.intent in NEEDS_DESTINATION and not conds.get("destination"):
        clarify = "어느 여행지(도시나 동네)를 생각하고 있나요?"
    elif a.intent == "plan_trip" and conds.get("end_time") and not conds.get("start_time"):
        clarify = (f"몇 시부터 일정을 시작할 수 있나요? {conds['end_time']}까지 끝내야 한다는 것만으로는 "
                   f"계산할 시작점이 없어요.")
    elif a.intent == "register_calendar" and not a.plan_id:
        clarify = "캘린더에 등록할 일정 번호를 알려 주세요. 내 일정 화면에서 확인할 수 있어요."
    if clarify:
        em.emit("decision", "analyze", kind="clarify", question=clarify,
                reason="Tool 을 부르기 전에 꼭 필요한 조건이 비어 있음")

    if a.intent in ("plan_trip", "weather") and not conds.get("visit_date") and not clarify:
        conds["visit_date"] = date.today().isoformat()                 # 조회 기준 기본값 (F3002)
        defaults.append(f"날짜를 말하지 않아 오늘({conds['visit_date']}) 기준으로 조회했어요.")
    return {"intent": a.intent, "reason": a.reason, "conditions": conds, "assumptions": a.assumptions,
            "defaults": defaults, "clarify": clarify, "plan_id": a.plan_id, "llm_tools": list(a.tools),
            "question": a.question or state["message"],
            "style": conds.get("style") or "outdoor", "radius": config.RADIUS_STEPS[0],
            "visit_date": conds.get("visit_date"), "location": None,
            "cache": {}, "results": {}, "evidence": [], "replan_count": 0, "replans": [], "excluded": []}


def after_analyze(state: State):
    if state.get("clarify") or state["intent"] == "smalltalk":
        return "respond"
    return "plan"


# ── plan (규칙) ───────────────────────────────────────────────
def make_steps(state: State, em: RunEmitter, node: str = "plan"):
    """의도와 현재 전략으로 Tool 호출 순서를 만든다. (steps, location, why) — Planner(F3021)도 같이 쓴다"""
    intent, conds = state["intent"], state["conditions"]
    steps, why = [], ""
    location = state.get("location")

    if intent in NEEDS_DESTINATION and location is None:
        # 여행지 좌표는 판단이 필요 없는 일이라 Tool 이 아니라 흐름이 직접 구한다 (명세서 2.3)
        with Timer() as t:
            g = geocode(conds["destination"])
        em.emit("tool_call", node, tool="geocode", args={"destination": conds["destination"]}, flow=True)
        em.emit("tool_result", node, tool="geocode", ok=g["ok"], ms=t.ms, flow=True,
                brief=g["data"]["destination"] if g["ok"] else f'{g["error"]["code"]}: {g["error"]["message"]}')
        if not g["ok"]:
            state["results"]["geocode"] = g
            return [], None, "여행지 위치를 찾지 못해 Tool 을 부르지 않음"
        location = g["data"]
    domestic = (location or {}).get("country_code") == "kr"

    if intent == "plan_trip":
        steps.append({"tool": "search_places", "args": {
            "destination": conds["destination"], "style": state["style"],
            "limit": config.CANDIDATE_LIMIT, "radius_m": state["radius"]}})
        steps.append({"tool": "get_weather", "args": {
            "lat": location["lat"], "lon": location["lon"], "visit_date": state["visit_date"]}})
        why = "볼 장소를 정하지 않았으므로 후보 조회 → 그날 날씨 확인"
        if "get_schedule" in TOOLS:
            steps.append({"tool": "get_schedule", "args": {"visit_date": state["visit_date"]}})
            why += " → 그날 내 일정 조회(빈 시간 계산)"
        if conds.get("want_food") or "search_nearby_food" in (state.get("llm_tools") or []):
            if domestic:
                steps.append({"tool": "search_nearby_food", "args": {
                    "lat": location["lat"], "lon": location["lon"],
                    "keyword": conds.get("food_keyword") or "맛집", "radius_m": config.FOOD_RADIUS_M}})
                why += " → 주변 식당"
            else:
                why += " (해외라 식당 검색 Tool 은 제외 — 카카오 로컬은 국내 전용)"
    elif intent == "weather":
        steps.append({"tool": "get_weather", "args": {
            "lat": location["lat"], "lon": location["lon"], "visit_date": state["visit_date"]}})
        why = "날씨만 물었으므로 날씨 Tool 하나"
    elif intent == "food":
        if domestic:
            steps.append({"tool": "search_nearby_food", "args": {
                "lat": location["lat"], "lon": location["lon"],
                "keyword": conds.get("food_keyword") or "맛집", "radius_m": config.FOOD_RADIUS_M}})
            why = "식당만 물었으므로 식당 Tool 하나"
        else:
            why = "해외 여행지라 식당 검색 Tool(국내 전용)을 부르지 않음"
    elif intent == "travel_info":
        if "search_travel_docs" in TOOLS:
            steps.append({"tool": "search_travel_docs", "args": {"query": state["question"]}})
            why = "외부 API 로 확인할 수 없는 지식 질문이라 여행 문서 검색"
        else:
            why = "지식 검색 Tool 이 아직 없음"
    elif intent == "register_calendar":
        if "register_schedule" in TOOLS:
            steps.append({"tool": "register_schedule", "args": {"plan_id": state["plan_id"]}})
            why = "저장한 일정 번호로 캘린더 등록"
        else:
            why = "캘린더 Tool 이 아직 없음"

    if state["replan_count"]:
        why = f'재계획 {state["replan_count"]}회차: {state["replans"][-1]["change"]} — ' + why
    check_llm_choice(state, em, node, steps, domestic)
    return steps, location, why


REQUIRED = {"plan_trip": {"search_places", "get_weather", "get_schedule"}, "weather": {"get_weather"},
            "food": {"search_nearby_food"}, "travel_info": {"search_travel_docs"},
            "register_calendar": {"register_schedule"}}


def check_llm_choice(state, em, node, steps, domestic):
    """LLM 이 고른 Tool(2일차 Plan.tools)과 코드가 만든 순서를 대조해 남긴다.
    인자 · 순서는 코드가 정하고(앞 결과 → 다음 입력), 판단에 꼭 필요한 Tool 이 빠졌으면 넣고,
    쓸 수 없는 Tool(없는 이름 · 해외 식당 검색)은 빼고, 그 보정을 이벤트로 남긴다."""
    if state.get("replan_count"):
        return                                         # 재계획은 judge 의 규칙이 정한다
    chosen = state.get("llm_tools") or []
    final = [s["tool"] for s in steps]
    added = [t for t in final if t not in chosen and t in REQUIRED.get(state["intent"], set())]
    dropped = []
    for t in chosen:
        if t in final:
            continue
        if t not in TOOLS:
            dropped.append({"tool": t, "reason": "등록되지 않은 Tool"})
        elif t == "search_nearby_food" and not domestic:
            dropped.append({"tool": t, "reason": "해외 좌표 — 카카오 로컬은 국내 전용"})
        else:
            dropped.append({"tool": t, "reason": f'{state["intent"]} 요청에 쓰지 않는 Tool'})
    em.emit("decision", node, kind="tool_choice", llm_tools=chosen, final=final, added=added, dropped=dropped,
            reason=("LLM 이 Tool 을 고르지 않아 의도별 기본 계획 사용" if not chosen else
                    "LLM 선택 그대로" if not added and not dropped else "LLM 선택을 코드가 보정"))


@step("plan")
def plan(state: State, em: RunEmitter):
    steps, location, why = make_steps(state, em, "plan")
    em.emit("decision", "plan", kind="plan", steps=[s["tool"] for s in steps], args=steps, reason=why)
    return {"steps": steps, "location": location, "plan_reason": why}


# ── execute (규칙) ────────────────────────────────────────────
@step("execute")
def execute(state: State, em: RunEmitter):
    cache, results, evidence = state["cache"], dict(state["results"]), list(state["evidence"])
    for s in state["steps"]:
        name, args = s["tool"], s["args"]
        key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
        if key in cache:                               # 재계획 때 이미 받은 결과는 다시 부르지 않는다 (F3009)
            r = cache[key]
            em.emit("tool_cache_hit", "execute", tool=name, args=args, ok=r["ok"], brief=_brief(name, r))
        else:
            em.emit("tool_call", "execute", tool=name, args=args)
            with Timer() as t:
                r = TOOLS[name].run(**args)            # Tool 은 실패도 ok/fail 로 돌려준다 — 예외가 나오지 않는다
            cache[key] = r
            em.emit("tool_result", "execute", tool=name, ok=r["ok"], ms=t.ms,
                    code=None if r["ok"] else r["error"]["code"], brief=_brief(name, r))
        results[name] = r                              # 실패해도 기록하고 다음 Tool 로 진행한다 (F3007)
        evidence.append({"tool": name, "ok": r["ok"], "brief": _brief(name, r)})
    return {"results": results, "evidence": evidence}


# ── judge (계산 · 규칙) ───────────────────────────────────────
STOP_CODES = {"INVALID_KEY", "UPSTREAM_ERROR", "BAD_ARGUMENT", "NOT_FOUND"}   # 다시 해도 결과가 같다 → 재계획 안 함


def _replan(state, em, reason, change, update):
    if state["replan_count"] >= config.MAX_REPLAN:
        v = {"action": "respond", "cap_reached": True,
             "reason": f"재계획 상한({config.MAX_REPLAN}회)에 도달해 멈춤. 마지막 사유: {reason}"}
        em.emit("decision", "judge", kind="replan_cap", reason=v["reason"])
        return {"verdict": v, "outcome": {"status": "cap", "reason": reason}}
    n = state["replan_count"] + 1
    replans = state["replans"] + [{"n": n, "reason": reason, "change": change}]
    em.emit("decision", "judge", kind="replan", n=n, reason=reason, change=change)
    return {"verdict": {"action": "replan", "reason": reason}, "replan_count": n, "replans": replans, **update}


@step("judge")
def judge(state: State, em: RunEmitter):
    intent, results, conds = state["intent"], state["results"], state["conditions"]
    if "geocode" in results and not results["geocode"]["ok"]:
        err = results["geocode"]["error"]
        em.emit("decision", "judge", kind="stop", reason=err["message"])
        return {"verdict": {"action": "respond", "reason": err["message"]},
                "outcome": {"status": "error", "code": err["code"], "reason": err["message"]}}
    if intent != "plan_trip":
        em.emit("decision", "judge", kind="respond", reason="사실 조회 요청이라 제약 판단 없이 응답")
        return {"verdict": {"action": "respond"}, "outcome": {"status": "info"}}

    places_r = results.get("search_places", {})
    if not places_r.get("ok"):
        code = places_r.get("error", {}).get("code")
        if code == "NO_DATA":
            nxt = [r for r in config.RADIUS_STEPS if r > state["radius"]]
            if nxt:
                return _replan(state, em, f'반경 {state["radius"] // 1000}km 안에 후보가 없음',
                               f'검색 반경 {state["radius"] // 1000}km → {nxt[0] // 1000}km', {"radius": nxt[0]})
        msg = places_r.get("error", {}).get("message", "후보를 찾지 못했습니다.")
        em.emit("decision", "judge", kind="stop", reason=msg, code=code)
        return {"verdict": {"action": "respond", "reason": msg},
                "outcome": {"status": "error", "code": code, "reason": msg}}

    data = dict(places_r["data"])
    # 사용자가 뺀 장소 (대화 내내 유지) — 판단 전에 후보에서 거른다
    kept, dropped = J.apply_exclude(data["places"], conds.get("exclude") or [])
    if dropped:
        em.emit("decision", "judge", kind="exclude", reason=f'요청에 따라 {len(dropped)}곳 제외',
                names=[d["name"] for d in dropped], words=conds.get("exclude"))
        data["places"] = kept
        state = {**state, "excluded": state["excluded"] + dropped}
    if not data["places"]:
        nxt = [r for r in config.RADIUS_STEPS if r > state["radius"]]
        if nxt:
            return _replan(state, em, "뺀 장소를 거르고 나니 후보가 없음",
                           f'검색 반경 {state["radius"] // 1000}km → {nxt[0] // 1000}km', {"radius": nxt[0]})
    w = J.judge_weather(results.get("get_weather"), state["style"])
    if w["status"] == "rain":
        rained_out = [{"name": p["name"], "reason": f'비 예보(강수확률 {w["precip_prob"]:.0f}%)로 야외 제외'}
                      for p in data["places"][:3]]
        excluded = state["excluded"] + rained_out
        if not conds.get("outdoor_only"):
            return _replan(state, em, w["note"], "야외 → 실내 후보로 다시 찾기",
                           {"style": "indoor", "excluded": excluded})
        nxt = date.fromisoformat(state["visit_date"]) + timedelta(days=1)
        if not conds.get("date_fixed") and nxt <= date.today() + timedelta(days=14):
            return _replan(state, em, w["note"] + " — 실내는 원하지 않음",
                           f'날짜 {state["visit_date"]} → {nxt.isoformat()} (비 안 오는 날 찾기)',
                           {"visit_date": nxt.isoformat(), "excluded": excluded})
        reason = f'{state["visit_date"]} 비 예보인데 실내로도, 다른 날로도 바꿀 수 없음'
        em.emit("decision", "judge", kind="stop", reason=reason)
        return {"verdict": {"action": "respond", "reason": reason}, "excluded": excluded,
                "outcome": {"status": "unsatisfied", "reason": reason, "weather": w}}

    # 시간창 — 말하지 않았으면 기본 시간창, 내 일정이 있으면 겹치는 시간을 뺀다 (F3023)
    defaults = list(state["defaults"])
    start = J.to_min(conds.get("start_time") or config.DEFAULT_WINDOW[0])
    end = J.to_min(conds.get("end_time") or config.DEFAULT_WINDOW[1])
    if not conds.get("start_time") and not conds.get("end_time"):
        defaults.append(f"시간을 말하지 않아 {config.DEFAULT_WINDOW[0]}~{config.DEFAULT_WINDOW[1]} 기준으로 짰어요.")
    if end <= start:
        reason = f"끝나는 시각({J.fmt(end)})이 시작 시각({J.fmt(start)})보다 늦지 않아요."
        em.emit("decision", "judge", kind="stop", reason=reason)
        return {"verdict": {"action": "respond", "reason": reason}, "defaults": defaults,
                "outcome": {"status": "unsatisfied", "reason": reason}}
    busy, blocked = [], []
    sched = results.get("get_schedule")
    if sched and sched.get("ok"):
        busy = sched["data"]["events"]
        s2, e2, blocked = J.free_window(start, end, busy)
        if blocked:
            defaults.append(f"그날 일정({', '.join(b['title'] for b in blocked)})을 피해 "
                            f"{J.fmt(s2)}~{J.fmt(e2)} 빈 시간에 맞췄어요.")
        start, end = s2, e2

    itin = J.build_itinerary(data["center"], data["places"], start, end)
    excluded = state["excluded"] + itin["excluded"]
    if not itin["ok"]:
        if itin["available_min"] >= config.MIN_STAY_MIN and state["radius"] > config.NEAR_RADIUS:
            return _replan(state, em, itin["reason"], f'검색 반경을 {config.NEAR_RADIUS // 1000}km로 좁혀 가까운 곳만',
                           {"radius": config.NEAR_RADIUS, "excluded": excluded, "defaults": defaults})
        em.emit("decision", "judge", kind="stop", reason=itin["reason"])
        return {"verdict": {"action": "respond", "reason": itin["reason"]}, "excluded": excluded,
                "defaults": defaults, "outcome": {"status": "unsatisfied", "reason": itin["reason"], "weather": w}}

    # 위키백과 문서가 있는 장소는 한 줄 소개를 붙인다 (못 가져와도 일정은 그대로)
    wikis = [it for it in itin["items"] if it.get("wiki")]
    if wikis:
        from concurrent.futures import ThreadPoolExecutor
        from tools import wiki_summary
        with ThreadPoolExecutor(max_workers=3) as ex:
            for it, w in zip(wikis, ex.map(lambda it: wiki_summary(it["wiki"], it["name"]), wikis)):
                if w:
                    it["about"] = w["about"]
                    it["wiki"] = {**it["wiki"], "url": w["url"]}
    em.emit("decision", "judge", kind="satisfied",
            reason=f'{len(itin["items"])}곳, 장소당 {itin["stay_min"]}분, 이동 {itin["travel_min"]}분 — 시간창 충족',
            weather=w["note"])
    return {"verdict": {"action": "respond"}, "excluded": excluded, "defaults": defaults,
            "outcome": {"status": "ok", "weather": w, "itinerary": itin, "busy": blocked}}


def after_judge(state: State):
    return "plan" if state["verdict"]["action"] == "replan" else "respond"


# ── respond (LLM) ────────────────────────────────────────────
@step("respond")
def respond(state: State, em: RunEmitter):
    intent = state["intent"]
    if state.get("clarify"):
        reply = state["clarify"]
        result = {"kind": "clarify"}
    elif intent == "smalltalk":
        with Timer() as lt:
            reply = llm.smalltalk(state["message"], state["memory"]["turns"])
        em.emit("llm_call", "respond", purpose="대화 응답", ms=lt.ms)
        result = {"kind": "smalltalk"}
    else:
        facts, result = build_facts(state)
        try:
            with Timer() as lt:
                reply = llm.respond(state["message"], facts)
            em.emit("llm_call", "respond", purpose="응답 작성", ms=lt.ms)
        except llm.LLMError as e:                      # 응답 작성만 실패했으면 규칙 기반 문장으로 대신한다 (NF3003)
            em.emit("error", "respond", error="LLMError", message=str(e), fallback=True)
            reply = fallback_text(facts)
    summary = None
    if result.get("itinerary"):
        summary = {"destination": state["conditions"].get("destination"), "date": state.get("visit_date"),
                   "places": [i["name"] for i in result["itinerary"]]}
    memory.save(state["session_id"], state.get("conditions", {}), summary, state["message"], reply)
    return {"reply": reply, "result": result}


def build_facts(state: State) -> tuple[dict, dict]:
    conds, results, out = state["conditions"], state["results"], state.get("outcome", {})
    food = results.get("search_nearby_food")
    facts = {
        "의도": state["intent"], "여행지": conds.get("destination"), "날짜": state.get("visit_date"),
        "계획_사유": state.get("plan_reason"),
        "상태": out.get("status"), "사유": out.get("reason"),
        "근거_Tool_결과": state["evidence"],
        "기본값_사용": state.get("defaults", []), "해석": state.get("assumptions", []),
        "재계획": state.get("replans", []), "재계획_상한_도달": out.get("status") == "cap",
        "제외_후보": state.get("excluded", [])[:8],
    }
    result = {"kind": state["intent"], "status": out.get("status"), "destination": conds.get("destination"),
              "date": state.get("visit_date"), "replans": state.get("replans", []),
              "excluded": state.get("excluded", [])[:8], "defaults": state.get("defaults", []),
              "assumptions": state.get("assumptions", []), "evidence": state["evidence"]}
    if out.get("weather"):
        facts["날씨_판단"] = out["weather"]
        result["weather"] = out["weather"]
    elif "get_weather" in results:
        w = J.judge_weather(results["get_weather"], state.get("style", "outdoor"))
        facts["날씨_판단"] = w
        result["weather"] = w
    if out.get("itinerary"):
        it = out["itinerary"]
        facts["추천_일정"] = it["items"]
        facts["계산"] = {"시간창": it["window"], "장소당_체류_분": it["stay_min"], "이동_합_분": it["travel_min"],
                       "늦어도_마지막_장소를_나서는_시각": it["latest_leave"],
                       "식": f"도보(분)=직선거리×{config.DETOUR}÷{config.WALK_KMH}km/h×60, "
                             f"체류=(시간창−이동)÷장소수 ≥ {config.MIN_STAY_MIN}분"}
        result["itinerary"] = it["items"]
        result["calc"] = facts["계산"]
        result["city"] = conds.get("destination")
    if out.get("busy"):
        facts["피한_내_일정"] = out["busy"]
    if food:
        if food["ok"]:
            facts["주변_식당"] = food["data"]["places"]
            result["food"] = food["data"]["places"]
        else:
            facts["주변_식당_실패"] = food["error"]
            result["food_error"] = food["error"]
    if "search_travel_docs" in results:
        facts["문서_검색"] = results["search_travel_docs"]
        result["docs"] = results["search_travel_docs"]
    if "register_schedule" in results:
        facts["캘린더_등록"] = results["register_schedule"]
        result["calendar"] = results["register_schedule"]
    return facts, result


def fallback_text(f: dict) -> str:
    lines = []
    if f.get("추천_일정"):
        lines.append(f'{f["여행지"]} {f["날짜"]} 일정을 짰어요.')
        lines += [f'- {i["start_time"]}~{i["end_time"]} {i["name"]}'
                  + (f' (다음 장소까지 도보 {i["walk_to_next_min"]}분)' if i["walk_to_next_min"] else "")
                  for i in f["추천_일정"]]
    elif f.get("사유"):
        lines.append(f'일정을 확정하지 못했어요: {f["사유"]}')
    if f.get("날씨_판단"):
        lines.append(f'날씨: {f["날씨_판단"]["note"]}')
    for r in f.get("재계획", []):
        lines.append(f'재계획 {r["n"]}회차: {r["change"]} ({r["reason"]})')
    lines += f.get("기본값_사용", [])
    lines.append("(답변 문장을 만드는 LLM 호출이 실패해 확인된 사실만 정리했어요.)")
    return "\n".join(lines)


# ── 그래프 조립 ──────────────────────────────────────────────
def build_graph():
    g = StateGraph(State)
    for fn in (analyze, plan, execute, judge, respond):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "analyze")
    g.add_conditional_edges("analyze", after_analyze, {"plan": "plan", "respond": "respond"})
    g.add_edge("plan", "execute")
    g.add_edge("execute", "judge")
    g.add_conditional_edges("judge", after_judge, {"plan": "plan", "respond": "respond"})
    g.add_edge("respond", END)
    return g.compile()


GRAPH = build_graph()
