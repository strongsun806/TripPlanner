"""대화 맥락 (F3010)

세션마다 '직전 요청과 응답'을 기억해 후속 요청에 반영한다.

보관하는 것                         보관하지 않는 것
    · 확정된 조건 (여행지 · 날짜 · 시간 …)   · Tool 의 원본 응답
    · 직전 추천 요약 (장소 이름 · 날짜)       · 후보 전체 목록, 날씨 원문
    · 최근 대화 3턴의 짧은 요약

"같은 날 다른 곳으로"처럼 조건 일부만 바꾸면 analyze 단계가 바뀐 필드만 돌려주고,
merge_conditions 가 나머지 조건을 그대로 둔다.
"""
import threading

_LOCK = threading.Lock()
_SESSIONS: dict[str, dict] = {}
MAX_TURNS = 3


def load(session_id: str) -> dict:
    with _LOCK:
        m = _SESSIONS.get(session_id)
        return {"conditions": dict(m["conditions"]), "last_summary": m["last_summary"], "turns": list(m["turns"])} \
            if m else {"conditions": {}, "last_summary": None, "turns": []}


def save(session_id: str, conditions: dict, summary: dict | None, user_message: str, reply: str):
    with _LOCK:
        m = _SESSIONS.setdefault(session_id, {"conditions": {}, "last_summary": None, "turns": []})
        m["conditions"] = dict(conditions)
        if summary is not None:
            m["last_summary"] = summary
        m["turns"] = (m["turns"] + [{"user": user_message[:200], "assistant": reply[:200]}])[-MAX_TURNS:]


def forget(session_id: str):
    with _LOCK:
        _SESSIONS.pop(session_id, None)


def merge_conditions(previous: dict, changes: dict, reset: bool) -> tuple[dict, list[str]]:
    """바뀐 조건만 갱신한다. reset 이면(전혀 다른 여행 요청) 이전 조건을 버린다.
    돌려주는 두 번째 값은 이번에 바뀐 필드 이름 — 실행 기록에 남긴다."""
    base = {} if reset else dict(previous)
    changed = []
    for k, v in changes.items():
        if v is None:
            continue
        if base.get(k) != v:
            changed.append(k)
        base[k] = v
    return base, changed
