"""계획 저장소 — SQLite. 라우터와 (나중에) Agent 는 이 파일의 함수만 쓴다.

1일차 실습의 PLANS 딕셔너리를 같은 역할의 함수로 바꾼 것이다. 서버를 다시 켜도 일정이 남는다.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trip.db"

COLUMNS = ["plan_id", "place_name", "city", "visit_date", "start_time", "end_time",
           "lat", "lon", "memo", "created_at"]


def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                plan_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                place_name TEXT NOT NULL,
                city       TEXT NOT NULL,
                visit_date TEXT NOT NULL,     -- YYYY-MM-DD
                start_time TEXT NOT NULL,     -- HH:MM:SS
                end_time   TEXT NOT NULL,
                lat REAL, lon REAL, memo TEXT,
                created_at TEXT NOT NULL
            )""")


def _row(con, plan_id):
    r = con.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
    return dict(r) if r else None


def _values(plan: dict):
    """date · time 객체를 문자열로 — SQLite 는 문자열로 저장한다 (같은 형식이라 문자열 비교로 시간 비교가 된다)"""
    return [str(plan[k]) if plan[k] is not None and k in ("visit_date", "start_time", "end_time") else plan[k]
            for k in COLUMNS[1:-1]]


def create(plan: dict) -> dict:
    with connect() as con:
        cur = con.execute(
            f"INSERT INTO plans ({', '.join(COLUMNS[1:])}) VALUES ({', '.join('?' * (len(COLUMNS) - 1))})",
            [*_values(plan), datetime.now().isoformat(timespec="seconds")])
        return _row(con, cur.lastrowid)


def list_all(visit_date: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM plans", []
    if visit_date:
        sql, args = sql + " WHERE visit_date = ?", [visit_date]
    with connect() as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY visit_date, start_time", args)]


def get(plan_id: int) -> dict | None:
    with connect() as con:
        return _row(con, plan_id)


def update(plan_id: int, plan: dict) -> dict | None:
    with connect() as con:
        cur = con.execute(
            f"UPDATE plans SET {', '.join(f'{c} = ?' for c in COLUMNS[1:-1])} WHERE plan_id = ?",
            [*_values(plan), plan_id])
        return _row(con, plan_id) if cur.rowcount else None


def delete(plan_id: int) -> bool:
    with connect() as con:
        return con.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,)).rowcount > 0


def overlaps(visit_date, start_time, end_time, exclude_id=None) -> list[dict]:
    """같은 날짜에 시간이 겹치는 일정. 겹침 = 기존 시작 < 새 종료 그리고 새 시작 < 기존 종료
    (14:00~16:00 과 16:00~17:00 처럼 끝과 시작이 맞닿으면 겹치지 않는다)"""
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM plans WHERE visit_date = ? AND start_time < ? AND ? < end_time AND plan_id != ? "
            "ORDER BY start_time",
            (str(visit_date), str(end_time), str(start_time), exclude_id or -1))
        return [dict(r) for r in rows]
