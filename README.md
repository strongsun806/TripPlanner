# TripPlanner — Agent 기반 국내·해외 여행 플래너

AI 코어 프로젝트 3회차 (개인). 사용자가 말한 여행지 · 날짜 · 선호를 받아 외부 API 로 후보 장소와 날씨를 확인하고,
제약(비 · 시간창)에 맞는 하루 일정을 제안한다. 맞지 않으면 실내 장소나 다른 날짜로 재계획한다.

## 진행 상황

| 단계 | 상태 |
|---|---|
| 도메인 확정 · 외부 API 3종 호출 확인 | ✅ 1일차 |
| Tool 3종 구현 (F3001 ~ F3004) | ✅ 1일차 |
| 메인 Agent (분석 → 계획 → 실행 → 판단 → 응답, LangGraph) | ⬜ |
| Memory · 이벤트 발행 · 로깅 Agent | ⬜ |
| 계획 관리 API + 화면 (F3015 · F3016, SQLite) | ✅ 1일차 |
| 대화 · 실행 로그 API (FastAPI) | ⬜ |
| 화면 — 대화 · 실행 과정 표시 | ⬜ |
| 심화 | ⬜ |

## 외부 API

| Tool | API | 범위 | 무료 한도 |
|---|---|---|---|
| search_places | Geoapify Geocoding + Places | 전 세계 | 3,000 크레딧/일 |
| get_weather | Visual Crossing Timeline | 전 세계 · 15일 | 1,000 레코드/일 |
| search_nearby_food | 카카오 로컬 키워드 검색 | 국내 | 100,000건/일 |

## 폴더 구조

```
TripPlanner/
├─ app/              백엔드 (FastAPI)
│  ├─ main.py        앱 · CORS · 라우터 연결
│  ├─ schemas.py     요청 · 응답 형식 (PlanIn · PlanOut · PlanSaved)
│  ├─ store.py       계획 저장소 (SQLite, data/trip.db)
│  └─ api/plans.py   /api/plans 저장 · 목록 · 상세 · 수정 · 삭제
├─ frontend/
│  └─ index.html     계획 관리 화면
├─ tools.py          Tool 3종 + 등록 목록 (python tools.py 로 시험)
├─ api_check.py      외부 API 호출 확인용 (도메인 확정 단계)
├─ llm_check.py      GMS LLM 호출 · Tool 선택 확인 (기획 검증 2.7)
├─ .env.example      키 이름 (.env 로 복사해 키를 넣는다)
└─ docs/
   ├─ 01_도메인_확정.md
   ├─ 02_Tool_정의서.md
   └─ images/        4-1_tool_check.png · 2-7_llm_tool_select.png
```

## 실행

```bash
# 프로젝트 상위 폴더의 venv 사용
source ../../venv/Scripts/activate     # Git Bash  (PowerShell: ..\..\venv\Scripts\Activate.ps1)
cp .env.example .env                   # 키 입력
python api_check.py                    # 외부 API 호출 확인
python tools.py                        # Tool 목록 + 정상 · 실패 시험
python llm_check.py                    # LLM 호출 + Tool 선택 확인

# 서버 + 화면 (터미널 두 개, 둘 다 TripPlanner 폴더에서)
uvicorn app.main:app --reload          # http://localhost:8000/docs
python -m http.server 5500 -d frontend # http://localhost:5500
```

## 계획 관리 API

| 메서드 · 경로 | 하는 일 | 성공 | 실패 |
|---|---|---|---|
| POST `/api/plans` | 일정 저장 | 201 `{plan, overlaps}` | 422 (종료 ≤ 시작 등) |
| GET `/api/plans?visit_date=` | 목록 (날짜 · 시작 순) | 200 | — |
| GET `/api/plans/{id}` | 상세 | 200 | 404 |
| PUT `/api/plans/{id}` | 수정 | 200 `{plan, overlaps}` | 404 · 422 |
| DELETE `/api/plans/{id}` | 삭제 | 204 | 404 |

- 같은 날짜에 시간이 겹치는 일정이 있으면 **저장은 하고** `overlaps` 로 알린다. 끝과 시작이 맞닿으면(14–16 · 16–17) 겹침이 아니다.

## 참고
- 2회차 프로젝트 [TripPilot](https://github.com/strongsun806/TripPilot) 의 여행 도메인을 이어 간다. Vector DB 는 심화 F3022 에서 재사용 예정.
- 교육장 네트워크에서 Open-Meteo 가 차단되어 날씨 API 를 Visual Crossing 으로 교체했다 (`docs/01_도메인_확정.md`).
