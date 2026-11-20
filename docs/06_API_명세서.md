# API 명세서 (F3017)

서버: `uvicorn app.main:app --reload` → `http://localhost:8000` · 자동 문서 `http://localhost:8000/docs`
요청 · 응답 형식은 모두 Pydantic 모델(`app/schemas.py`)로 정의했다. 오류 본문은 FastAPI 기본 형식 `{"detail": …}`.

## 대화 — `/api/chat`

### POST `/api/chat` — 여행 플래너에게 요청

요청 `ChatIn`
```json
{"message": "다음 주 토요일 오사카, 오후에 야외 위주로. 비 오면 곤란해", "session_id": null}
```
| 필드 | 형식 | 설명 |
|---|---|---|
| message | string 1~500 | 사용자 요청 |
| session_id | string \| null | 이어서 대화하려면 직전 응답의 값. 없으면 새 세션 |

응답 `ChatOut` (200)
| 필드 | 설명 |
|---|---|
| session_id · run_id | 세션 · 이번 실행 식별자 |
| ok | 정상 처리 여부 |
| reply | 사용자에게 보일 답변 |
| intent | plan_trip · weather · food · travel_info · register_calendar · smalltalk |
| conditions | 지금까지 확정된 조건 (Memory) |
| result | 화면이 그릴 구조 — `status`(ok · unsatisfied · cap · error · info), `itinerary[{name, start_time, end_time, stay_min, walk_to_next_min, lat, lon}]`, `weather`, `calc`, `food`, `food_error`, `docs`, `calendar`, `excluded[{name, reason}]`, `replans[{n, reason, change}]`, `defaults`, `assumptions`, `evidence[{tool, ok, brief}]` |
| replan_count | 재계획 횟수 |
| error | `{code, message}` — LLM_KEY_MISSING · LLM_ERROR · AGENT_ERROR, 정상이면 null |
| steps | 이번 실행의 이벤트 목록 (실행 순서) |
| ms | 처리 시간 |

### DELETE `/api/chat/{session_id}` — 대화 맥락 지우기 → 204

## 계획 관리 — `/api/plans` (F3015 · F3016)

| 메서드 · 경로 | 하는 일 | 성공 | 실패 |
|---|---|---|---|
| POST `/api/plans` | 저장 | 201 `PlanSaved {plan, overlaps[]}` | 422 (종료 ≤ 시작 등) |
| GET `/api/plans?visit_date=YYYY-MM-DD` | 목록 (날짜 · 시작 순) | 200 `PlanOut[]` | — |
| GET `/api/plans/{plan_id}` | 상세 | 200 `PlanOut` | 404 |
| PUT `/api/plans/{plan_id}` | 수정 | 200 `PlanSaved` | 404 · 422 |
| DELETE `/api/plans/{plan_id}` | 삭제 | 204 | 404 |

`PlanIn`: `place_name`(1~100) · `city`(1~50) · `visit_date` · `start_time` · `end_time` · `lat?` · `lon?` · `memo?`(≤300)
`PlanOut` = PlanIn + `plan_id` · `created_at`. 같은 날짜에 시간이 겹치면 저장은 하고 `overlaps` 에 겹치는 일정을 담는다 (맞닿음은 겹침 아님).

## 실행 로그 — `/api/logs` (F3014)

| 메서드 · 경로 | 하는 일 | 응답 |
|---|---|---|
| GET `/api/logs` | 세션 목록 (최근 순) | `SessionSummary[] {session_id, first_ts, last_ts, runs, events, first_message}` |
| GET `/api/logs/{session_id}` | 세션 상세 | `SessionLog {session_id, events[LogEvent], evaluations[]}` · 없으면 404 |
| DELETE `/api/logs/{session_id}` | 삭제 | 204 · 없으면 404 |

수정 API 는 없다 (PUT/PATCH → 405). `LogEvent`: `{id, session_id, run_id, seq, ts, type, node, data}`.
`evaluations[]`(F3020): `{run_id, verdict(normal · anomaly · undecidable), warnings[{code, detail}], diagnosis, diagnosis_error}` — 판정과 진단은 다른 필드.

## 내 캘린더 — `/api/calendar` (심화 F3023)

| 메서드 · 경로 | 하는 일 |
|---|---|
| GET `/api/calendar?event_date=` | 약속 목록 |
| POST `/api/calendar` `{title, event_date, start_time, end_time}` | 약속 추가 → 201 |
| DELETE `/api/calendar/{event_id}` | 약속 삭제 (로컬) → 204 · 404 |

## 상태 · 명세

| 경로 | 응답 |
|---|---|
| GET `/api/status` | `keys[{name, label, set}]` — 키 값은 내보내지 않음(NF3002), `settings`(판단 기준값 · 모드 · 모델), `logging{enabled, saved, failed}` |
| GET `/api/tools` | 등록된 Tool 명세 `[{name, kind(external · local), description, parameters}]` (F3001) |
