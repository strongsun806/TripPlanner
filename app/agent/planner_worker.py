"""메인 Agent 구조 분리 — Planner / Worker (심화 F3021)          AGENT_MODE=planner_worker

    START → analyze → planner → worker ─┬─(계획 변경 요청)→ planner …
                                        └→ judge ─┬─(재계획)→ planner …
                                                  └→ review ─┬─(로깅 Agent 이상 판정)→ planner (1회)
                                                             └→ respond → END

    analyze  (LLM)  기존과 같다
    planner  (규칙) 요청을 작업 목록으로 분해한다. 사용자 조건으로 채운 인자는 '고정 인자'로 표시한다
    worker   (규칙, 필요할 때만 LLM)
             평소에는 LLM 없이 작업 목록대로 Tool 을 부른다.
             인자를 고치면 결과가 달라질 수 있는 실패(NO_DATA · NOT_FOUND · BAD_ARGUMENT)일 때만
             LLM 에게 retry · skip · replan_request 를 묻는다. retry 는 같은 Tool 로만, WORKER_RETRY 회까지,
             고정 인자는 바꾸지 않는다. API 키 오류 같은 치명적 실패는 LLM 에게 넘기지 않는다.
             스스로 해결하지 못한 실패는 사유와 함께 Planner 에게 보고한다.
    judge    (계산) 기존과 같다
    review   (규칙) 로깅 Agent 의 판정 규칙(evaluator.judge_events)으로 지금까지의 실행을 검증한다.
             이상이면 Planner 가 작업 목록을 다시 구성한다 (한 번만).
    respond  (LLM)  기존과 같다

Planner · Worker · 로깅 Agent 사이의 메시지(작업 목록 · 수행 보고 · 검증 결과)는 모두 이벤트로 남는다.
"""
import json

from langgraph.graph import END, START, StateGraph

from .. import config
from . import llm
from .events import RunEmitter, Timer
from .graph import (State, _brief, _replan, after_analyze, analyze, judge, make_steps, respond, step)
from .registry import TOOLS

RETRYABLE = {"NO_DATA", "NOT_FOUND", "BAD_ARGUMENT"}     # 인자를 고치면 달라질 수 있는 실패
FATAL = {"INVALID_KEY", "UPSTREAM_ERROR"}                # 다시 해도 같다 → LLM 에 넘기지 않는다


def fixed_args(tool: str, args: dict, conds: dict) -> list[str]:
    """사용자 조건으로 채운 인자 — Worker 가 바꾸면 안 된다"""
    fixed = []
    for k in args:
        if k in ("destination", "visit_date", "lat", "lon", "plan_id", "query"):
            fixed.append(k)
        elif k == "style" and (conds.get("style") or conds.get("outdoor_only")):
            fixed.append(k)
        elif k == "keyword" and conds.get("food_keyword"):
            fixed.append(k)
    return fixed


@step("planner")
def planner(state: State, em: RunEmitter):
    steps, location, why = make_steps(state, em, "planner")
    tasks = [{"id": f"t{i + 1}", "tool": s["tool"], "args": s["args"],
              "fixed": fixed_args(s["tool"], s["args"], state["conditions"])} for i, s in enumerate(steps)]
    em.emit("decision", "planner", kind="tasks", steps=[t["tool"] for t in tasks], tasks=tasks, reason=why,
            to="worker")
    return {"steps": steps, "tasks": tasks, "location": location, "plan_reason": why, "replan_requests": []}


@step("worker")
def worker(state: State, em: RunEmitter):
    cache, results, evidence = state["cache"], dict(state["results"]), list(state["evidence"])
    report, requests = [], []
    for task in state.get("tasks", []):
        name, args, fixed = task["tool"], dict(task["args"]), task["fixed"]
        attempt = 0
        while True:
            key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
            if key in cache:
                r = cache[key]
                em.emit("tool_cache_hit", "worker", tool=name, args=args, ok=r["ok"], brief=_brief(name, r))
            else:
                em.emit("tool_call", "worker", tool=name, args=args, task=task["id"], attempt=attempt)
                with Timer() as t:
                    r = TOOLS[name].run(**args)
                cache[key] = r
                em.emit("tool_result", "worker", tool=name, ok=r["ok"], ms=t.ms,
                        code=None if r["ok"] else r["error"]["code"], brief=_brief(name, r))
            if r["ok"]:
                report.append({"task": task["id"], "tool": name, "status": "done", "attempts": attempt + 1})
                break
            code = r["error"]["code"]
            # 필수 인자가 비어 있으면 Worker 가 지어낼 값이 아니다 → LLM 에 넘기지 않고 그대로 보고 (명세 F3021)
            missing = [k for k in TOOLS[name].parameters.get("required", []) if args.get(k) in (None, "")]
            if missing:
                em.emit("decision", "worker", kind="worker_failure", tool=name, code=code, llm=False,
                        reason=f"필수 인자 누락({', '.join(missing)}) — LLM 에 넘기지 않음")
                report.append({"task": task["id"], "tool": name, "status": "failed", "code": code})
                break
            if code in FATAL or code not in RETRYABLE or not config.WORKER_LLM:
                why = "치명적 실패 — LLM 에 넘기지 않음" if code in FATAL else \
                      "Worker LLM 꺼짐 — 그대로 보고" if not config.WORKER_LLM else "인자로 고칠 수 없는 실패"
                em.emit("decision", "worker", kind="worker_failure", tool=name, code=code, reason=why, llm=False)
                report.append({"task": task["id"], "tool": name, "status": "failed", "code": code})
                break
            if attempt >= config.WORKER_RETRY:
                em.emit("decision", "worker", kind="worker_failure", tool=name, code=code,
                        reason=f"재시도 상한 {config.WORKER_RETRY}회 도달", llm=False)
                requests.append({"task": task["id"], "tool": name, "code": code, "reason": "재시도 상한 도달"})
                report.append({"task": task["id"], "tool": name, "status": "failed", "code": code})
                break
            try:
                with Timer() as lt:
                    d = llm.worker_decide(name, TOOLS[name].parameters, args, fixed, r["error"], attempt)
                em.emit("llm_call", "worker", purpose="실패 대응 판단", ms=lt.ms)
            except (llm.LLMError, llm.LLMUnavailable) as e:   # 판단을 못 받으면 재시도 없이 보고 — 요청 전체는 계속된다
                em.emit("error", "worker", error=type(e).__name__, message="Worker 판단 LLM 호출 실패 — 재시도 없이 보고")
                report.append({"task": task["id"], "tool": name, "status": "failed", "code": code})
                break
            changes = {k: v for k, v in d.new_args.items()
                       if k not in fixed and k in TOOLS[name].parameters.get("properties", {})}
            em.emit("decision", "worker", kind="worker_decide", tool=name, action=d.action, reason=d.reason,
                    new_args=changes, ignored=[k for k in d.new_args if k not in changes], llm=True)
            if d.action == "retry" and changes and {**args, **changes} != args:
                args = {**args, **changes}
                attempt += 1
                continue
            if d.action == "replan_request":
                requests.append({"task": task["id"], "tool": name, "code": code, "reason": d.reason})
            report.append({"task": task["id"], "tool": name, "status": "skipped" if d.action == "skip" else "failed",
                           "code": code})
            break
        results[name] = r
        evidence.append({"tool": name, "ok": r["ok"], "brief": _brief(name, r)})
    em.emit("decision", "worker", kind="worker_report", report=report, replan_requests=requests, to="planner",
            reason=f'작업 {len(report)}개 중 완료 {sum(x["status"] == "done" for x in report)}개'
                   + (f', 계획 변경 요청 {len(requests)}건' if requests else ""))
    return {"results": results, "evidence": evidence, "replan_requests": requests}


@step("route_worker")
def route_worker(state: State, em: RunEmitter):
    """Worker 의 계획 변경 요청을 Planner 의 재계획에 반영한다 — 반경 넓히기처럼 Planner 가 바꿀 수 있는 것만"""
    reqs = state.get("replan_requests") or []
    if not reqs:
        return {"verdict": {"action": "judge"}}
    req = reqs[0]
    if req["tool"] == "search_places" and req["code"] == "NO_DATA":
        nxt = [r for r in config.RADIUS_STEPS if r > state["radius"]]
        if nxt:
            return _replan(state, em, f'Worker 요청: {req["reason"]}', f'Planner: 검색 반경 → {nxt[0] // 1000}km',
                           {"radius": nxt[0]})
    em.emit("decision", "planner", kind="request_declined", reason=f'Worker 요청을 계획으로 풀 수 없음: {req["reason"]}')
    return {"verdict": {"action": "judge"}}


@step("review")
def review(state: State, em: RunEmitter):
    """로깅 Agent 의 판정 규칙으로 지금까지의 실행을 검증 (LLM 없음)"""
    from ..evaluator import judge_events
    steps = em.steps + [{"seq": em.seq + 1, "type": "run_end", "node": None, "data": {}}]
    verdict, warnings = judge_events([{"type": "run_start", "seq": 0, "node": None, "data": {}}] + steps)
    em.emit("decision", "review", kind="review", verdict=verdict, codes=[w["code"] for w in warnings], to="planner",
            reason="로깅 Agent 규칙 판정")
    if verdict == "anomaly" and not state.get("reviewed") and state["verdict"]["action"] == "respond" \
            and not any(w["code"] == "EXCESSIVE_REPLAN" for w in warnings):
        return {**_replan(state, em, "로깅 Agent 이상 판정: " + ", ".join(w["code"] for w in warnings),
                          "Planner: 작업 목록 다시 구성", {}), "reviewed": True}
    return {"reviewed": True}


def build():
    g = StateGraph(State)
    for fn in (analyze, planner, worker, route_worker, judge, review, respond):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "analyze")
    g.add_conditional_edges("analyze", after_analyze, {"plan": "planner", "respond": "respond"})
    g.add_edge("planner", "worker")
    g.add_edge("worker", "route_worker")
    g.add_conditional_edges("route_worker", lambda s: "planner" if s["verdict"]["action"] == "replan" else "judge",
                            {"planner": "planner", "judge": "judge"})
    g.add_conditional_edges("judge", lambda s: "planner" if s["verdict"]["action"] == "replan" else "review",
                            {"planner": "planner", "review": "review"})
    g.add_conditional_edges("review", lambda s: "planner" if s["verdict"]["action"] == "replan" else "respond",
                            {"planner": "planner", "respond": "respond"})
    g.add_edge("respond", END)
    return g.compile()


GRAPH = build()


def run(state: dict) -> dict:
    return GRAPH.invoke(state, config={"recursion_limit": 80})
