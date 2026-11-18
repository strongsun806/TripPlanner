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
| 계획 저장 · 실행 로그 API (FastAPI) | ⬜ |
| 화면 (대화 · 계획 관리) | ⬜ |
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
```

## 참고
- 2회차 프로젝트 [TripPilot](https://github.com/strongsun806/TripPilot) 의 여행 도메인을 이어 간다. Vector DB 는 심화 F3022 에서 재사용 예정.
- 교육장 네트워크에서 Open-Meteo 가 차단되어 날씨 API 를 Visual Crossing 으로 교체했다 (`docs/01_도메인_확정.md`).
