# TripPlanner — Agent 기반 국내·해외 여행 플래너

AI 코어 프로젝트 3회차 (개인). 여행지 · 날짜 · 시간대 · 선호를 말하면 Agent 가 후보 장소와 그날 날씨, 내 약속을 확인해
**제약(비 · 시간창 · 도보 동선)에 맞는 하루 일정**을 짜고, 맞지 않으면 실내 · 다른 날 · 다른 반경으로 **재계획**한다.
실행 과정은 로깅 서브 Agent 가 이벤트로 수집해 남기고, 화면에서 단계 · Tool 호출 · 재계획 지점을 그대로 보여 준다.

2회차 [TripPilot](https://github.com/strongsun806/TripPilot)(RAG 여행 플래너)의 도메인과 Vector DB 를 이어 쓴다.
1 ~ 3일차 라이브의 구조(ok/fail · Tool 명함 · scrub · LLM 이 catalog 를 보고 Tool 선택 · 이벤트 버스 ·
로깅 Agent · 규칙 넷 판정 · 바꿔 끼우는 캘린더 · 통과/실패/미확인 체크)를 그대로 따른다 —
대응표는 [`docs/04_Agent_설계.md`](docs/04_Agent_설계.md) 9 · 10절.

## 진행 상황

| 구분 | 내용 | 상태 |
|---|---|---|
| 필수 F3001~F3004 | Tool 3종 (Geoapify · Visual Crossing · 카카오 로컬) | ✅ |
| 필수 F3005~F3011 | 메인 Agent (LangGraph: 분석 → 계획 → 실행 → 판단 → 응답, 재계획 · Memory) | ✅ |
| 필수 F3012~F3014 | 이벤트 발행 · 로깅 Agent · 실행 로그 API | ✅ |
| 필수 F3015~F3019 | 계획 관리 · FastAPI · 대화/내 일정/실행 로그 화면 | ✅ |
| 심화 F3020 | 로깅 Agent 평가 (규칙 판정 + 이상일 때만 LLM 진단) | ✅ |
| 심화 F3021 | Planner / Worker 분리 + 구조 비교 실험 | ✅ (비교 결과는 실제 키로 실행) |
| 심화 F3022 | 2회차 Vector DB 검색 Tool | ✅ |
| 심화 F3023 | 일정 조회 · 등록 Tool (로컬 → Google Calendar 교체 가능) | ✅ |

오프라인 시험 56건 통과 (`tests/`), 체크리스트 자동 확인 `check_list.py`. 요구사항별 위치와 확인 방법은 [`docs/03_요구사항_정의서.md`](docs/03_요구사항_정의서.md).

## 실행

```bash
# 1) 패키지 (상위 폴더 venv 사용 시)
source ../../venv/Scripts/activate              # PowerShell: ..\..\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2) 키 — .env.example 을 .env 로 복사해 채운다
cp .env.example .env

# 3) 서버 + 화면 (터미널 두 개, 둘 다 TripPlanner 폴더에서)
uvicorn app.main:app --reload                   # http://localhost:8000/docs
python -m http.server 5500 -d frontend          # http://localhost:5500

# 4) (선택) 심화 F3022 문서 색인 — 2회차 chroma_db 가 있으면 .env 의 RAG_DB_DIR 로 가리키면 된다
python rag_index.py
```

| 확인용 스크립트 | 하는 일 |
|---|---|
| `python api_check.py` | 외부 API 3종 호출 확인 (도메인 확정) |
| `python tools.py` | Tool 목록 + 정상 · 실패 시험 |
| `python llm_check.py` | GMS LLM 호출 + Tool 선택 확인 |
| `python tests/test_agent_offline.py` 외 2개 | 키 없이 Agent · API · Planner/Worker 흐름 시험 |
| `python compare_modes.py` | 대표 요청 5개 × 구성 3개 실행 → `docs/05_구조_비교.md` |
| `python check_list.py` | 체크리스트를 코드로 확인 (통과 · 실패 · 미확인) → `docs/08_체크리스트_결과.md` |

## 화면

| 화면 | 내용 |
|---|---|
| 홈 `index.html` | 출발 안내판(저장한 일정을 날짜 · 도시별로 글자판에) · 체크인(요청을 적으면 대화로 넘겨 바로 보냄) · 다음 출발 D-day · 최근 요청 · 탑승 동선 |
| 대화 `chat.html` | 요청 → 추천 일정 카드(시각 · 체류 · 도보 · 판단 기준 · 식당 · 제외 사유 · 근거) · '이 일정 저장'. 오른쪽 '실행 과정'에 단계 · Tool · 판단 · LLM 호출을 순서대로, 재계획은 노랑 띠 |
| 내 일정 `plans.html` | 날짜별 하루 시간표. 겹치는 일정은 나란히 · 노랑. 블록을 눌러 수정 · 삭제. 내 캘린더 약속 관리 |
| 실행 로그 `logs.html` | 세션 목록 → 실행별 이벤트 표, 규칙 판정 · LLM 진단, 세션 삭제 |

## 외부 API

| Tool | API | 범위 | 무료 한도 |
|---|---|---|---|
| search_places | Geoapify Geocoding + Places | 전 세계 | 3,000 크레딧/일, 출처 표기 |
| get_weather | Visual Crossing Timeline | 전 세계 · 15일 | 1,000 레코드/일 (조회한 날짜 수만큼 사용) |
| search_nearby_food | 카카오 로컬 키워드 검색 | 국내 | 100,000건/일 |
| (LLM) | SSAFY GMS · claude-haiku-5-5 (GPT 토큰 소진으로 교체, `x-api-key` 헤더) | | |

교육장 네트워크에서 Open-Meteo 가 차단되어 날씨는 Visual Crossing 으로 바꿨다 ([`docs/01_도메인_확정.md`](docs/01_도메인_확정.md)).

## 폴더 구조

```
TripPlanner/
├─ tools.py                  외부 API 툴 3종 + Tool 약속(ok/fail) + geocode 도우미
├─ app/
│  ├─ main.py                FastAPI 앱 · CORS · 라우터 · 로깅 Agent 연결
│  ├─ config.py              판단 기준값 · 상한 · LLM 설정 (한 곳)
│  ├─ schemas.py             요청 · 응답 형식
│  ├─ store.py               SQLite — plans · events · calendar · evaluations
│  ├─ logging_agent.py       로깅 서브 Agent (구독 · 저장, LLM 없음)
│  ├─ evaluator.py           실행 평가 (F3020)
│  ├─ rag.py                 2회차 Vector DB 검색 · 색인 (F3022)
│  ├─ calendar_backend.py    캘린더 구현체 local · google (F3023)
│  ├─ agent/
│  │  ├─ graph.py            메인 Agent (LangGraph)
│  │  ├─ planner_worker.py   Planner / Worker 구조 (F3021)
│  │  ├─ judge.py            제약 판단 계산
│  │  ├─ llm.py              LLM 호출 (분석 · 응답 · Worker 판단)
│  │  ├─ memory.py           대화 맥락
│  │  ├─ events.py           이벤트 버스 · 발행
│  │  ├─ registry.py         Tool 목록 (외부 + 로컬)
│  │  ├─ local_tools.py      로컬 서버 API 툴 3종 (F3022 · F3023)
│  │  └─ runner.py           요청 1건 실행 입구
│  └─ api/                   chat · plans · logs · calendar · meta 라우터
├─ frontend/                 index.html(홈) · chat.html(대화) · plans.html(내 일정) · logs.html(실행 로그) · style.css · common.js
├─ tests/                    오프라인 시험 (가짜 LLM · 외부 API)
├─ data/rag/                 2회차 여행지 메타데이터 (색인 원본)
├─ docs/                     01 도메인 · 02 Tool · 03 요구사항 · 04 Agent 설계 · 05 구조 비교 · 06 API · 07 검증 · 08 체크 결과
├─ rag_index.py · compare_modes.py · check_list.py · api_check.py · llm_check.py
└─ requirements.txt · .env.example
```

## 설정 (`.env`, 선택 항목)

| 이름 | 기본 | 설명 |
|---|---|---|
| AGENT_MODE | single | `planner_worker` 로 바꾸면 심화 F3021 구조 |
| WORKER_LLM | 1 | 0 이면 Worker 가 LLM 없이 실패를 그대로 보고 (비교 실험) |
| LOGGING_ENABLED | 1 | 0 이면 로깅 Agent 를 붙이지 않음 (분리 확인) |
| CALENDAR_BACKEND | local | `google` + `credentials.json` 이면 Google Calendar |
| RAG_DB_DIR | data/chroma | 2회차 `chroma_db` 폴더를 가리키면 그대로 검색 |

## 출처 · 라이선스

- 위키백과 문서(2회차 색인)는 CC BY-SA 4.0. 검색 결과에 출처 링크를 함께 보인다.
- 장소 데이터 © OpenStreetMap contributors, Powered by Geoapify. 날씨 Visual Crossing. 식당 카카오 로컬.
