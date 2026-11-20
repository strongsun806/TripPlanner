"""저장소 — SQLite (data/trip.db). 라우터 · Agent · 로깅 Agent 는 이 파일의 함수만 쓴다.

    plans        여행 일정 (F3015 · F3016)        저장 · 목록 · 상세 · 수정 · 삭제
    events       실행 로그 (F3013 · F3014)        덧붙이기 · 조회 · 삭제만 — 수정 함수는 일부러 두지 않는다
    calendar     내 일정 (심화 F3023 로컬 구현체)
    evaluations  실행 평가 (심화 F3020)           판정과 진단을 칸을 나눠 저장
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH") or Path(__file__).resolve().parent.parent / "data" / "trip.db")

COLUMNS = ["plan_id", "place_name", "city", "visit_date", "start_time", "end_time",
           "lat", "lon", "memo", "created_at"]


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS plans (
                plan_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                place_name TEXT NOT NULL,
                city       TEXT NOT NULL,
                visit_date TEXT NOT NULL,     -- YYYY-MM-DD
                start_time TEXT NOT NULL,     -- HH:MM:SS
                end_time   TEXT NOT NULL,
                lat REAL, lon REAL, memo TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                run_id     TEXT NOT NULL,
                seq        INTEGER NOT NULL,      -- 실행 안에서의 발생 순서
                ts         TEXT NOT NULL,
                type       TEXT NOT NULL,
                node       TEXT,
                data       TEXT NOT NULL          -- JSON, 발생한 사실 그대로
            );
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
            CREATE TABLE IF NOT EXISTS calendar (
                event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time   TEXT NOT NULL,
                source     TEXT NOT NULL DEFAULT 'manual',   -- manual | plan
                plan_id    INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                run_id     TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                verdict    TEXT NOT NULL,         -- normal | anomaly | undecidable
                warnings   TEXT NOT NULL,         -- JSON 목록 (규칙 판정, LLM 없음)
                diagnosis  TEXT,                  -- LLM 진단 (이상일 때만) — 판정과 칸을 나눈다
                diagnosis_error TEXT,
                created_at TEXT NOT NULL
            );
        """)


def now():
    return datetime.now().isoformat(timespec="seconds")


# ── plans ───────────────────────────────────────────────────
def _row(con, plan_id):
    r = con.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    return dict(r) if r else None


def _values(plan: dict):
    """date · time 객체를 문자열로 — SQLite 는 문자열로 저장한다 (같은 형식이라 문자열 비교로 시간 비교가 된다)"""
    return [str(plan[k]) if plan[k] is not None and k in ("visit_date", "start_time", "end_time") else plan[k]
            for k in COLUMNS[1:-1]]


def create(plan: dict) -> dict:
    with db() as con:
        cur = con.execute(
            f"INSERT INTO plans ({', '.join(COLUMNS[1:])}) VALUES ({', '.join('?' * (len(COLUMNS) - 1))})",
            [*_values(plan), now()])
        return _row(con, cur.lastrowid)


def list_all(visit_date: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM plans", []
    if visit_date:
        sql, args = sql + " WHERE visit_date = ?", [visit_date]
    with db() as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY visit_date, start_time", args)]


def get(plan_id: int) -> dict | None:
    with db() as con:
        return _row(con, plan_id)


def update(plan_id: int, plan: dict) -> dict | None:
    with db() as con:
        cur = con.execute(
            f"UPDATE plans SET {', '.join(f'{c} = ?' for c in COLUMNS[1:-1])} WHERE plan_id = ?",
            [*_values(plan), plan_id])
        return _row(con, plan_id) if cur.rowcount else None


def delete(plan_id: int) -> bool:
    with db() as con:
        return con.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,)).rowcount > 0


def overlaps(visit_date, start_time, end_time, exclude_id=None) -> list[dict]:
    """같은 날짜에 시간이 겹치는 일정. 겹침 = 기존 시작 < 새 종료 그리고 새 시작 < 기존 종료
    (14:00~16:00 과 16:00~17:00 처럼 끝과 시작이 맞닿으면 겹치지 않는다)"""
    with db() as con:
        rows = con.execute(
            "SELECT * FROM plans WHERE visit_date = ? AND start_time < ? AND ? < end_time AND plan_id != ? "
            "ORDER BY start_time",
            (str(visit_date), str(end_time), str(start_time), exclude_id or -1))
        return [dict(r) for r in rows]


# ── events (실행 로그) ───────────────────────────────────────
def append_event(e: dict):
    with db() as con:
        con.execute("INSERT INTO events (session_id, run_id, seq, ts, type, node, data) VALUES (?,?,?,?,?,?,?)",
                    (e["session_id"], e["run_id"], e["seq"], e["ts"], e["type"], e.get("node"),
                     json.dumps(e.get("data", {}), ensure_ascii=False, default=str)))


def list_sessions() -> list[dict]:
    with db() as con:
        rows = con.execute("""
            SELECT s.session_id, s.first_ts, s.last_ts, s.runs, s.events,
                   (SELECT json_extract(data, '$.message') FROM events e
                     WHERE e.session_id = s.session_id AND e.type = 'run_start' ORDER BY e.id LIMIT 1) AS first_message
            FROM (SELECT session_id, MIN(ts) first_ts, MAX(ts) last_ts,
                         COUNT(DISTINCT run_id) runs, COUNT(*) events
                  FROM events GROUP BY session_id) s
            ORDER BY s.last_ts DESC""")
        return [dict(r) for r in rows]


def session_events(session_id: str) -> list[dict]:
    with db() as con:
        rows = con.execute("SELECT * FROM events WHERE session_id = ? ORDER BY id", (session_id,))
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def run_events(run_id: str) -> list[dict]:
    with db() as con:
        rows = con.execute("SELECT * FROM events WHERE run_id = ? ORDER BY seq", (run_id,))
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def delete_session(session_id: str) -> bool:
    with db() as con:
        n = con.execute("DELETE FROM events WHERE session_id = ?", (session_id,)).rowcount
        con.execute("DELETE FROM evaluations WHERE session_id = ?", (session_id,))
        return n > 0


# ── calendar (F3023 로컬 구현체) ─────────────────────────────
def calendar_events(event_date: str) -> list[dict]:
    with db() as con:
        rows = con.execute("SELECT * FROM calendar WHERE event_date = ? ORDER BY start_time", (event_date,))
        return [dict(r) for r in rows]


def calendar_all() -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM calendar ORDER BY event_date, start_time")]


def calendar_add(title, event_date, start_time, end_time, source="manual", plan_id=None) -> dict:
    with db() as con:
        cur = con.execute("INSERT INTO calendar (title, event_date, start_time, end_time, source, plan_id, created_at)"
                          " VALUES (?,?,?,?,?,?,?)",
                          (title, str(event_date), str(start_time)[:5], str(end_time)[:5], source, plan_id, now()))
        return dict(con.execute("SELECT * FROM calendar WHERE event_id = ?", (cur.lastrowid,)).fetchone())


def calendar_find_plan(plan_id: int) -> dict | None:
    with db() as con:
        r = con.execute("SELECT * FROM calendar WHERE plan_id = ?", (plan_id,)).fetchone()
        return dict(r) if r else None


def calendar_delete(event_id: int) -> bool:
    with db() as con:
        return con.execute("DELETE FROM calendar WHERE event_id = ?", (event_id,)).rowcount > 0


# ── evaluations (F3020) ─────────────────────────────────────
def save_evaluation(run_id, session_id, verdict, warnings):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO evaluations (run_id, session_id, verdict, warnings, created_at)"
                    " VALUES (?,?,?,?,?)", (run_id, session_id, verdict, json.dumps(warnings, ensure_ascii=False), now()))


def save_diagnosis(run_id, diagnosis=None, error=None):
    with db() as con:
        con.execute("UPDATE evaluations SET diagnosis = ?, diagnosis_error = ? WHERE run_id = ?",
                    (diagnosis, error, run_id))


def evaluations(session_id: str) -> list[dict]:
    with db() as con:
        rows = con.execute("SELECT * FROM evaluations WHERE session_id = ? ORDER BY created_at", (session_id,))
        return [{**dict(r), "warnings": json.loads(r["warnings"])} for r in rows]


def latest_evaluation(session_id: str) -> dict | None:
    ev = evaluations(session_id)
    return ev[-1] if ev else None
