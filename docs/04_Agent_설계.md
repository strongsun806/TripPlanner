# Agent 설계 문서

## 1. 전체 구조

```
 frontend (HTML/JS)                       app (FastAPI)
 ┌──────────────┐  POST /api/chat   ┌─────────────────────────────────────────────────────────┐
 │ 대화 화면     │ ────────────────▶ │ runner.run_agent ─▶ 메인 Agent (LangGraph)                │
 │ 내 일정 화면  │ ◀──────────────── │                     analyze → plan → execute → judge ─┐  │
 │ 실행 로그 화면│  reply·result·steps│                       ▲                       (재계획)│  │
 └──────────────┘                    │                       └───────────────────────────────┘  │
                                     │                     → respond                           │
                                     │          │ publish(event)                                │
                                     │          ▼                                               │
                                     │      EventBus ──subscribe──▶ 로깅 Agent ─▶ SQLite events  │
                                     │                                   └─ run_end ─▶ 평가기(F3020)│
                                     │  Tool: tools.py (외부 API 3) · agent/local_tools.py (로컬 3) │
                                     └─────────────────────────────────────────────────────────┘
```

- 메인 Agent 는 이벤트를 **발행만** 한다. 로깅 Agent 가 붙어 있는지 모르고, 구독자가 오류를 내도 영향받지 않는다
  (`LOGGING_ENABLED=0` 으로 끄고 실행해도 정상 — 시험 S8).
- 화면의 '실행 과정'은 메인 Agent 가 스스로 모은 `steps` 로 그린다. 로깅 Agent 에 기대지 않는다.

## 2. 실행 단계와 단계별 입출력

| 단계 | 방식 | 입력 (State) | 출력 (State) | 발행 이벤트 |
|---|---|---|---|---|
| analyze | LLM 1회 | message, memory(직전 조건 · 요약 · 3턴), last_evaluation, **Tool 목록(catalog)** | intent, conditions(합친 결과), **llm_tools(LLM 이 고른 Tool)**, assumptions, defaults, clarify, style, radius, visit_date | `llm_call`, `decision(intent)` 의도와 이유 · 바뀐 필드 · 고른 Tool, `decision(clarify)` |
| plan | 규칙 | intent, conditions, **llm_tools**, style, radius, visit_date, replans | steps[{tool, args}], location(좌표), plan_reason | `tool_call/result(geocode, flow)`, `decision(tool_choice)` LLM 선택과 보정, `decision(plan)` |
| execute | 규칙 | steps, cache | results{tool: 결과}, evidence[요약], cache | `tool_call`, `tool_result(ms)`, `tool_cache_hit` |
| judge | 계산 | results, conditions, style, radius, excluded | verdict(replan/respond), outcome(일정 · 날씨 · 사유), excluded, replans | `decision(replan · replan_cap · satisfied · stop)` |
| respond | LLM 1회 | outcome, evidence, defaults, assumptions, replans | reply, result(화면용 구조) | `llm_call` |

그래프 구성 (`app/agent/graph.py`):

```
START → analyze ─┬─(Tool 필요)→ plan → execute → judge ─┬─(재계획)→ plan …
                 │                                      └─(확정 · 중단)→ respond → END
                 └─(인사 · 되묻기)──────────────────────────────────────→ respond → END
```

### 의도 6종과 계획

| 의도 | 예 | 계획 (Tool 순서) |
|---|---|---|
| plan_trip | "토요일 오사카, 오후 야외, 비 오면 곤란" | search_places → get_weather → get_schedule → (국내 · 식당 요청 시) search_nearby_food |
| weather | "내일 해운대 날씨" | get_weather |
| food | "종로 라멘집" | (국내만) search_nearby_food |
| travel_info | "가을에 좋은 일본 미식 여행지" | search_travel_docs |
| register_calendar | "3번 일정 캘린더에 넣어줘" | register_schedule |
| smalltalk | "안녕, 뭘 할 수 있어?" | 없음 — 계획 단계를 건너뜀 |

여행지 좌표(geocode)는 판단이 필요 없는 작업이라 Tool 이 아니라 plan 단계가 직접 구한다 (명세서 2.3).

### LLM 의 Tool 선택과 코드의 보정 (2일차 `Plan.tools` 방식)

analyze 의 LLM 은 Tool 목록(이름 · 설명)을 보고 **부를 Tool 이름**을 고른다. 인자는 고르지 않는다.
plan 단계가 그 선택을 아래 규칙으로 대조하고, 결과를 `decision(tool_choice)` 로 남긴다 (화면 '실행 과정'에 + / − 배지).

| 경우 | 처리 |
|---|---|
| 판단에 꼭 필요한 Tool 이 빠짐 (일정 추천의 후보 · 날씨 · 내 일정) | 넣는다 (`added`) |
| 등록되지 않은 이름, 해외에서 식당 검색처럼 쓸 수 없는 Tool | 뺀다 (`dropped` + 사유) |
| 식당을 말하지 않았지만 LLM 이 국내 식당 Tool 을 고름 | 포함한다 |
| LLM 이 아무것도 고르지 않음 | 의도별 기본 계획 |

순서와 인자는 코드가 정한다 — 앞 Tool 의 결과(좌표)가 다음 Tool 의 입력이 되어야 하기 때문이다 (2일차 dispatch 와 같다).
재계획 때는 LLM 을 다시 부르지 않고 judge 의 규칙이 바꾼다.

### 되묻기 (Tool 을 부르기 전에)

- 일정 · 날씨 · 식당 요청인데 여행지가 없을 때
- 끝나야 하는 시각만 있고 시작 시각이 없을 때 — 계산할 시작점이 없다 (시험 S7)
- 캘린더 등록인데 일정 번호가 없을 때

## 3. 제약 판단 계산식 (judge, `app/agent/judge.py`)

설정값은 모두 `app/config.py` 한 곳에 있다.

| 항목 | 식 · 기준 |
|---|---|
| 비 판단 | 강수확률 > `RAIN_THRESHOLD`(50%) 이고 야외면 야외 후보 제외. 예보가 없거나(범위 밖 · 값 없음 · 키 없음) 실패하면 **판단 보류**를 밝히고 제외하지 않음 |
| 시간창 | 사용자 시작 ~ 끝. 말하지 않았으면 `DEFAULT_WINDOW`(10:00~18:00)를 쓰고 응답에 밝힘. 그날 캘린더 약속(F3023)과 겹치는 부분을 빼고 가장 긴 빈 구간 |
| 동선 | 여행지 중심에서 가장 가까운 후보부터 '가장 가까운 다음 후보'로 연결. 장소 사이 도보가 `MAX_LEG_MIN`(60분)을 넘으면 그 후보는 사유와 함께 제외 |
| 도보 시간 | 직선거리(km) × `DETOUR`(1.3) ÷ `WALK_KMH`(4) × 60, 5분 단위 올림 |
| 체류 시간 | (시간창 − 이동 합) ÷ 장소 수, 5분 단위 내림. `MIN_STAY_MIN`(40분) 이상이 되는 가장 많은 장소 수(최대 3)를 고름 |
| 제시 | 장소별 시작 · 끝 시각, 다음 장소까지 도보, "늦어도 HH:MM 에 마지막 장소를 나서야" — 실제 체류 · 영업시간은 API 로 알 수 없어 지어내지 않고 역산한 기준으로 제시 |

## 4. 재계획 조건과 상한 (F3009)

| 상황 | 재계획 | 바꾸는 것 |
|---|---|---|
| 비 예보 + 야외 | 한다 | style: outdoor → indoor |
| 비 예보 + '야외만' + 날짜 고정 아님 | 한다 | 날짜 +1일 (예보 범위 안에서) |
| 비 예보 + '야외만' + 날짜 고정 | 안 한다 | 사유와 함께 응답 |
| 후보 없음(NO_DATA) | 한다 | 검색 반경 5km → 10km → 20km |
| 동선 · 시간창 불충족 (시간창 ≥ 40분) | 한다 | 검색 반경 2km 로 좁힘 |
| 키 없음 · 외부 오류 · 위치 못 찾음 · 시간창 < 40분 | 안 한다 | 다시 해도 결과가 같다 → 바로 안내 |

- 상한 `MAX_REPLAN`=3. 도달하면 `replan_cap` 이벤트를 남기고 멈춘 뒤 마지막 사유와 함께 조건 조정을 되묻는다 (시험 S3).
- 재계획해도 같은 Tool · 같은 인자는 **다시 부르지 않는다** — 요청 1건 동안 결과를 캐시하고 `tool_cache_hit` 로 남긴다 (시험 S2).
- 재계획 전에 제외한 후보는 사유와 함께 누적해 응답에 보인다.

## 5. 대화 맥락 (Memory, F3010)

| 보관 | 보관하지 않음 |
|---|---|
| 확정된 조건 (여행지 · 날짜 · 시각 · 선호 · 제약) | Tool 원본 응답, 후보 전체 목록 |
| 직전 추천 요약 (여행지 · 날짜 · 장소 이름) | 날씨 원문, 계산 과정 |
| 최근 3턴의 짧은 요약 (각 200자) | |

- analyze 의 LLM 은 '이번에 새로 말했거나 바꾼 조건'만 돌려준다. `merge_conditions` 가 나머지를 유지한다.
  "같은 날 다른 곳으로" → 여행지만 바뀌고 날짜 · 시각은 유지 (시험 S6). 전혀 다른 요청이면 `reset=true` 로 비운다.
- 세션은 서버 메모리에 둔다. '새 대화' 버튼이 `DELETE /api/chat/{session_id}` 로 지운다.

## 6. 실행 이벤트와 로깅 Agent (F3012 ~ F3014)

이벤트 한 건: `{session_id, run_id, seq, ts, type, node, data}`

| type | 언제 | data |
|---|---|---|
| run_start / run_end | 요청 1건의 시작 · 끝 | message / ok, ms, replans, intent, mode, llm_calls |
| node_start / node_end | 단계 진입 · 종료 | — / ms, summary |
| decision | 흐름을 바꾸는 판단 | kind(intent · clarify · plan · replan · replan_cap · satisfied · stop · tasks · worker_* · review · evaluation_ref), reason, … |
| tool_call / tool_result | Tool 요청 · 응답 | tool, args / ok, code, ms, brief |
| tool_cache_hit | 재계획 때 결과 재사용 | tool, args, brief |
| llm_call | LLM 호출 | purpose, ms |
| error | 단계 안의 오류 | error, message |

로깅 Agent (`app/logging_agent.py`)
- 서버가 뜰 때 `LOGGING_ENABLED=1` 이면 버스에 구독자로 붙는다. 받는 즉시 `events` 테이블에 덧붙인다 (LLM 없음, 가공 없음).
- 저장이 실패해도 예외를 삼키고 횟수만 센다 — 대화는 계속된다.
- `run_end` 를 받으면 평가기(F3020)를 별도 스레드에서 돌린다 — 응답 시간에 영향 없음.
- 실행 로그는 목록 · 상세 · 삭제만 있다. 수정 함수 · API 는 없다.

## 7. LLM 을 부르는 조건

| 누가 | 언제 | 부르지 않는 때 |
|---|---|---|
| analyze | 매 요청 1회 (의도 · 조건 · 부를 Tool 선택) | — |
| respond | Tool 이 필요한 요청 · 인사에 1회 | 되묻기 (정해진 문장) |
| Worker (F3021) | Tool 이 NO_DATA · NOT_FOUND · BAD_ARGUMENT 로 실패했을 때만 | 정상 경로, 치명적 실패(키 · 외부 오류), `WORKER_LLM=0` |
| 평가기 (F3020) | 규칙 판정이 '이상'일 때만 진단 | 정상 · 판정 불가 |
| 임베딩 (F3022) | 문서 검색 질의 1회 | 키가 없으면 해시 벡터로 대체 |

계획 수립 · Tool 인자 채우기 · 제약 계산 · 재계획 판단은 규칙으로 할 수 있으므로 LLM 을 쓰지 않는다.

## 8. Planner / Worker 구조 (심화 F3021, `AGENT_MODE=planner_worker`)

```
START → analyze → planner → worker → route_worker ─┬─(Worker 계획 변경 요청)→ planner …
                                                   └→ judge ─┬─(재계획)→ planner …
                                                             └→ review ─┬─(로깅 Agent 이상 판정, 1회)→ planner
                                                                        └→ respond → END
```

- **Planner**: 요청을 작업 목록 `[{id, tool, args, fixed}]` 으로 분해. 사용자 조건에서 온 인자(여행지 · 날짜 · 좌표 · 지정한 실내/야외 · 지정한 음식)는 `fixed` 로 표시.
- **Worker**: 평소에는 LLM 없이 작업 목록대로 Tool 을 부른다. 인자를 고치면 결과가 달라질 수 있는 실패일 때만
  LLM 에게 `retry` · `skip` · `replan_request` 를 묻는다. retry 는 같은 Tool, 최대 2회, `fixed` 인자는 LLM 이 바꾸려 해도 무시(기록).
  치명적 실패 · 필수 인자 누락은 LLM 에 넘기지 않는다. 수행 보고(`worker_report`)를 Planner 에게 보낸다.
- **route_worker**: Worker 의 계획 변경 요청을 Planner 가 풀 수 있으면(후보 없음 → 반경 확대) 재계획에 반영, 아니면 거절을 기록.
- **review**: 로깅 Agent 의 판정 규칙(`evaluator.judge_events`)으로 지금까지의 실행을 검증. 이상이면 Planner 가 작업 목록을 한 번 다시 구성.
- 비교 실험: `python compare_modes.py` → `docs/05_구조_비교.md` (재계획 횟수, 요청당 LLM 호출 수, Worker LLM 켬/끔, 응답 시간).

## 9. 2일차 라이브(Movie Planner 축소판)와의 대응

| 2일차 (`02_Tool_Agent`) | TripPlanner | 비고 |
|---|---|---|
| ① `ok` · `fail` | `tools.py` `ok` · `fail` | 같은 모양 |
| ② `Tool` 명함 + `run()` 의 TypeError → BAD_ARGUMENT | `tools.py` `Tool` | 같다 |
| ③ `call_kobis` (본문, 실패) | `tools.py` `call_api` | 401 · 403 → INVALID_KEY |
| ④ `get_box_office` 의 `is_estimated` (미래 날짜 → 최근 집계일) | `get_weather` 의 `used_default_date` · `OUT_OF_RANGE` | 요청 기준 ≠ 조회 기준을 밝힌다 |
| ④ `get_movie_detail` 의 `NO_RUNTIME` | `get_weather` 의 `NO_FORECAST` | 비면 채우지 않고 판단 보류 |
| ⑤ `scrub` | `tools.py` `scrub` + 이벤트 문구 | 키 값 · `key=` · `KakaoAK` 가리기 |
| ⑥ `TOOLS` · `catalog()` | `app/agent/registry.py` (외부 + 로컬) | `kind` 로 구분 |
| step3 `analyze → dispatch → judge → respond` | `analyze → plan → execute → judge → respond` | dispatch 를 계획(plan)과 실행(execute)으로 나눔. judge → plan 재계획 고리 추가 |
| step3 `latest_start` (마감 − 러닝타임 − 광고) | `judge.build_itinerary` 의 `latest_leave` | 지어내지 않고 역산 |
| step3 `traced()` 의 step.enter · exit | `step()` 데코레이터의 node_start · node_end | |
| step3 `fake_plan` · `fake_answer` (대역 배우) | `tests/fakes.py` | 키 없이 흐름 시험 |
| step4 `Plan(tools, deadline)` + `catalog()` 를 프롬프트에 | `llm.Analysis(tools, changes …)` + catalog | 코드가 선택을 보정 (위 2절) |
| step4 `gpt-5.4-nano`, `GMS_API_KEY` | `GMS_MODEL` · `GMS_KEY`/`GMS_API_KEY` 둘 다 | gpt-5 계열은 temperature 생략. 지금은 GPT 토큰 소진 공지에 따라 `claude-haiku-5-5` (anthropic 주소 + `x-api-key` 헤더) — `config.gms_headers()` 한 곳에서 붙인다 |
| step5 `EventBus.publish` · `subscribe` (구독 0명이어도 동작) | `events.EventBus` + `logging_agent` | 로깅 Agent 가 구독자 |

## 10. 3일차 라이브(로깅 · 심화)와의 대응

| 3일차 (`3일차_로깅_심화`) | TripPlanner | 비고 |
|---|---|---|
| step1 `LoggingAgent.on_event` — 받는 즉시 저장, LLM 없음 | `app/logging_agent.py` `handle` | 저장 실패는 삼키고 횟수만 센다 |
| step1 `sessions` · `session` · `delete` (수정 없음) | `store.list_sessions` · `session_events` · `delete_session` | 수정 함수 자체가 없다 |
| step2 `/api/logs` — PUT 은 405 | `app/api/logs.py` | 시험 · `check_list.py` 4.3-g |
| 9쪽 구독 줄을 빼도 에이전트가 돈다 (체크리스트 15번) | `LOGGING_ENABLED=0`, 시험 S8, `check_list.py` 4.3-a | |
| step3 규칙 넷 `rule_mismatch` · `rule_repeat` · `rule_replan` · `rule_fail_streak` | `evaluator.judge_events` 의 UNPLANNED_CALL · REPEATED_CALL · EXCESSIVE_REPLAN · FAIL_STREAK | |
| step3 `judge([])` → '판정 불가' | `undecidable` | 기록이 없으면 '이상 없음'이 아니다 |
| step3 `diagnose` — 경고가 난 세션만, ① 단계 ② 근거 이벤트 ③ 고칠 점 | `evaluator.diagnose` (같은 세 항목), 판정과 다른 칸에 저장 | |
| step4 `LocalCalendar` · `GoogleCalendar` (같은 메서드, 바꿔 끼우기) | `calendar_backend.LocalCalendar` · `GoogleCalendar` | `CALENDAR_BACKEND` 로 교체 |
| step4 `timeZone` 을 주지 않으면 UTC, 못 읽으면 '비어 있음'이 아니다 | Google 조회에 `timeZone=Asia/Seoul`, 실패는 UPSTREAM_ERROR | |
| step4 `create_calendar_event(plan_id)` — 값은 서버가 저장소에서, 빈 값은 채우지 않는다 | `register_schedule(plan_id)` | |
| step4 `is_free` 순수 함수 | `judge.free_window` (빈 구간 계산) | |
| step5 `check` · `skip` — 통과 · 실패 · 미확인 | `check_list.py` → `docs/08_체크리스트_결과.md` | 실행하지 않은 것은 통과로 적지 않는다 |
