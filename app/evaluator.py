"""로깅 Agent 평가 기능 (심화 F3020)

1) 판정 — LLM 없이 실행 로그의 이벤트를 대조하고 횟수를 센다
    REPEATED_CALL     같은 Tool 을 같은 인자로 두 번 이상 실제 호출 (캐시가 막았어야 함)
    EXCESSIVE_REPLAN  재계획이 상한(MAX_REPLAN)에 닿았거나 상한 도달 이벤트가 있음
    UNPLANNED_CALL    직전 계획(plan 판단 이벤트)에 없던 Tool 을 호출
    FAIL_STREAK       같은 Tool 이 연달아 실패 (바로 앞 결과도 그 Tool 의 실패) — 3일차 rule_fail_streak
   근거 이벤트가 없으면(요청 시작 · 끝, 계획 판단이 없으면) '이상 없음'이 아니라 '판정 불가'로 남긴다.

2) 진단 — 이상으로 판정한 실행에만 LLM 이 실행 기록을 읽고 원인을 세 문장 이내로 쓴다.
   진단은 판정과 다른 칸(diagnosis)에 저장해 경고 수에 섞지 않는다. 진단이 실패해도 판정과 로그는 그대로 남는다.

메인 Agent 는 store.latest_evaluation(session_id) 로 직전 실행의 판정을 참조할 수 있다.
"""
import json

from . import config, store


def judge_events(events: list[dict]) -> tuple[str, list[dict]]:
    types = {e["type"] for e in events}
    if not events or "run_start" not in types or "run_end" not in types:
        return "undecidable", [{"code": "NO_EVIDENCE", "detail": "요청 시작 · 끝 이벤트가 없어 판정할 근거가 없음"}]
    plans = [e for e in events if e["type"] == "decision" and e["data"].get("kind") in ("plan", "tasks")]
    calls = [e for e in events if e["type"] == "tool_call" and not e["data"].get("flow")]
    if calls and not plans:
        return "undecidable", [{"code": "NO_EVIDENCE", "detail": "Tool 호출은 있는데 계획 이벤트가 없어 대조할 수 없음"}]

    warnings = []
    seen = {}
    for e in calls:
        key = json.dumps([e["data"]["tool"], e["data"].get("args")], sort_keys=True, ensure_ascii=False)
        seen[key] = seen.get(key, 0) + 1
    for key, n in seen.items():
        if n > 1:
            tool, args = json.loads(key)
            warnings.append({"code": "REPEATED_CALL", "detail": f"{tool} 를 같은 인자로 {n}번 호출 {json.dumps(args, ensure_ascii=False)[:120]}"})

    replans = [e for e in events if e["type"] == "decision" and e["data"].get("kind") == "replan"]
    if len(replans) >= config.MAX_REPLAN or any(e["data"].get("kind") == "replan_cap" for e in events if e["type"] == "decision"):
        warnings.append({"code": "EXCESSIVE_REPLAN", "detail": f"재계획 {len(replans)}회 (상한 {config.MAX_REPLAN})"})

    for c in calls:                                   # 호출 직전의 가장 최근 계획과 대조
        prev = [p for p in plans if p["seq"] < c["seq"]]
        planned = set(prev[-1]["data"].get("steps", [])) if prev else set()
        if c["data"]["tool"] not in planned:
            warnings.append({"code": "UNPLANNED_CALL", "detail": f'{c["data"]["tool"]} 는 직전 계획 {sorted(planned)} 에 없음'})

    prev_fail = None                                  # 바로 앞 Tool 결과가 실패였다면 그 Tool 이름
    for e in events:
        if e["type"] != "tool_result" or e["data"].get("flow"):
            continue
        tool, ok_ = e["data"]["tool"], e["data"].get("ok")
        if not ok_ and tool == prev_fail:
            warnings.append({"code": "FAIL_STREAK", "detail": f'{tool} 가 연달아 실패 ({e["data"].get("code")})'})
            break
        prev_fail = None if ok_ else tool

    return ("anomaly" if warnings else "normal"), warnings


DIAGNOSE_SYSTEM = """너는 여행 플래너 Agent 의 실행 기록을 검토한다. 규칙 판정이 이상으로 본 실행이다.
세 문장으로 답한다 (3일차 diagnose 와 같은 형식).
① 분석 · 계획 · Tool 호출 · 판단 · 응답 중 어느 단계의 문제인지
② 근거가 된 이벤트 (Tool 이름 그대로)
③ 무엇을 고칠지
기록에 없는 것은 추측하지 않는다."""


def diagnose(events: list[dict], warnings: list[dict]) -> str:
    from .agent.llm import get_llm
    brief = [{"seq": e["seq"], "type": e["type"], "node": e["node"],
              "data": {k: v for k, v in e["data"].items() if k in ("tool", "args", "ok", "code", "kind", "steps", "reason", "change", "brief")}}
             for e in events if e["type"] in ("decision", "tool_call", "tool_result", "tool_cache_hit", "error")]
    out = get_llm().invoke([("system", DIAGNOSE_SYSTEM),
                            ("human", f"경고: {json.dumps(warnings, ensure_ascii=False)}\n\n"
                                      f"실행 기록: {json.dumps(brief, ensure_ascii=False)[:6000]}")])
    return out.content.strip()


def evaluate_run(run_id: str, session_id: str, with_diagnosis: bool = True) -> dict:
    events = store.run_events(run_id)
    verdict, warnings = judge_events(events)
    store.save_evaluation(run_id, session_id, verdict, warnings)
    result = {"run_id": run_id, "verdict": verdict, "warnings": warnings, "diagnosis": None}
    if verdict == "anomaly" and with_diagnosis:        # 정상 · 판정 불가에는 LLM 을 부르지 않는다
        try:
            result["diagnosis"] = diagnose(events, warnings)
            store.save_diagnosis(run_id, diagnosis=result["diagnosis"])
        except Exception as e:
            store.save_diagnosis(run_id, error=type(e).__name__)
            result["diagnosis_error"] = type(e).__name__
    return result
