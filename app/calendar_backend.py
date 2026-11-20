"""일정 저장소 구현체 (심화 F3023)

Tool(get_schedule · register_schedule)은 아래 두 메서드만 쓴다. 구현체를 바꿔도 Tool 의 입출력은 그대로다.

    list_events(date)                          → [{"title", "start_time", "end_time", "source"}]
    add_event(title, date, start, end, plan_id) → {"event_id", "title", ...}

    LocalCalendar   SQLite calendar 테이블 (기본)                       CALENDAR_BACKEND=local
    GoogleCalendar  Google Calendar API (OAuth, primary 캘린더)         CALENDAR_BACKEND=google
                    credentials.json(Google Cloud 콘솔의 OAuth 데스크톱 클라이언트)을
                    TripPlanner 폴더에 두면 처음 한 번 브라우저 로그인 후 token.json 이 생긴다.
"""
import os
from pathlib import Path

from . import store

ROOT = Path(__file__).resolve().parent.parent
TIMEZONE = os.getenv("CALENDAR_TZ", "Asia/Seoul")


class LocalCalendar:
    name = "local"

    def list_events(self, event_date: str) -> list[dict]:
        return [{"title": e["title"], "start_time": e["start_time"], "end_time": e["end_time"],
                 "source": e["source"], "plan_id": e["plan_id"]} for e in store.calendar_events(event_date)]

    def find_plan(self, plan_id: int):
        return store.calendar_find_plan(plan_id)

    def add_event(self, title, event_date, start_time, end_time, plan_id=None) -> dict:
        return store.calendar_add(title, event_date, start_time, end_time, source="plan" if plan_id else "manual",
                                  plan_id=plan_id)


class GoogleCalendar:
    """같은 메서드를 Google Calendar API 로 구현한다. 필요한 라이브러리:
    google-api-python-client · google-auth-oauthlib · google-auth-httplib2 (명세서 필수 라이브러리)"""
    name = "google"
    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self):
        self._service = None

    def service(self):
        if self._service:
            return self._service
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        token, cred = ROOT / "token.json", ROOT / "credentials.json"
        creds = Credentials.from_authorized_user_file(str(token), self.SCOPES) if token.exists() else None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not cred.exists():
                    raise RuntimeError("credentials.json 이 없습니다.")
                creds = InstalledAppFlow.from_client_secrets_file(str(cred), self.SCOPES).run_local_server(port=0)
            token.write_text(creds.to_json(), encoding="utf-8")
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _range(self, event_date):
        return f"{event_date}T00:00:00+09:00", f"{event_date}T23:59:59+09:00"

    def list_events(self, event_date: str) -> list[dict]:
        t0, t1 = self._range(event_date)
        # timeZone 을 주지 않으면 시각이 UTC(…Z)로 올 수 있다 — 3일차 GoogleCalendar.busy 와 같은 주의
        # 못 읽으면 예외가 나고 get_schedule 이 UPSTREAM_ERROR 로 바꾼다 ('일정 없음'으로 넘기지 않는다)
        items = self.service().events().list(calendarId="primary", timeMin=t0, timeMax=t1, singleEvents=True,
                                             orderBy="startTime", timeZone=TIMEZONE).execute().get("items", [])
        out = []
        for it in items:
            s, e = it["start"].get("dateTime"), it["end"].get("dateTime")
            if not s or not e:                       # 하루 종일 일정은 시간 판단에서 뺀다
                continue
            plan_id = it.get("extendedProperties", {}).get("private", {}).get("tripplanner_plan_id")
            out.append({"title": it.get("summary", "(제목 없음)"), "start_time": s[11:16], "end_time": e[11:16],
                        "source": "plan" if plan_id else "google", "plan_id": int(plan_id) if plan_id else None})
        return out

    def find_plan(self, plan_id: int):
        items = self.service().events().list(calendarId="primary",
                                             privateExtendedProperty=f"tripplanner_plan_id={plan_id}").execute()
        return (items.get("items") or [None])[0]

    def add_event(self, title, event_date, start_time, end_time, plan_id=None) -> dict:
        body = {"summary": title,
                "start": {"dateTime": f"{event_date}T{str(start_time)[:5]}:00", "timeZone": TIMEZONE},
                "end": {"dateTime": f"{event_date}T{str(end_time)[:5]}:00", "timeZone": TIMEZONE}}
        if plan_id:
            body["extendedProperties"] = {"private": {"tripplanner_plan_id": str(plan_id)}}
        ev = self.service().events().insert(calendarId="primary", body=body).execute()
        return {"event_id": ev["id"], "title": title, "event_date": str(event_date),
                "start_time": str(start_time)[:5], "end_time": str(end_time)[:5], "link": ev.get("htmlLink")}


def get_calendar():
    return GoogleCalendar() if os.getenv("CALENDAR_BACKEND", "local") == "google" else LocalCalendar()


CALENDAR = get_calendar()
