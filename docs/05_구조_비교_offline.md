# 구조 비교 — 단일 Agent vs Planner/Worker (심화 F3021)

> 실행: 2026-11-20 09:19 · 가짜 LLM · 외부 API (흐름 확인용) · 모델 gpt-4.1-mini · `python compare_modes.py --offline`

| 대표 요청 | 구성 | 결과 | 재계획 | LLM 호출 (Worker) | Tool 호출 | 응답 시간 |
|---|---|---|---|---|---|---|
| 제약을 한 번에 충족 | single | ok | 0 | 2 (0) | 4 | 0.1s |
| 제약을 한 번에 충족 | planner_worker | ok | 0 | 2 (0) | 4 | 0.3s |
| 제약을 한 번에 충족 | planner_worker_off | ok | 0 | 2 (0) | 4 | 0.1s |
| 재계획이 필요 | single | ok | 1 | 2 (0) | 4 | 0.1s |
| 재계획이 필요 | planner_worker | ok | 1 | 2 (0) | 4 | 0.2s |
| 재계획이 필요 | planner_worker_off | ok | 1 | 2 (0) | 4 | 0.2s |
| 상한에 도달 | single | cap | 3 | 2 (0) | 9 | 0.2s |
| 상한에 도달 | planner_worker | cap | 3 | 2 (0) | 9 | 0.3s |
| 상한에 도달 | planner_worker_off | cap | 3 | 2 (0) | 9 | 0.3s |
| Tool 하나가 실패 | single | ok | 0 | 2 (0) | 4 | 0.1s |
| Tool 하나가 실패 | planner_worker | ok | 0 | 3 (1) | 5 | 0.1s |
| Tool 하나가 실패 | planner_worker_off | ok | 0 | 2 (0) | 4 | 0.1s |
| Tool 이 필요 없음 | single | smalltalk | 0 | 2 (0) | 0 | 0.0s |
| Tool 이 필요 없음 | planner_worker | smalltalk | 0 | 2 (0) | 0 | 0.0s |
| Tool 이 필요 없음 | planner_worker_off | smalltalk | 0 | 2 (0) | 0 | 0.0s |

## 구성별 평균

| 구성 | 재계획 | LLM 호출 | Worker LLM | Tool 호출 | 응답 시간 |
|---|---|---|---|---|---|
| single | 0.8 | 2.0 | 0.0 | 4.2 | 0.1s |
| planner_worker | 0.8 | 2.2 | 0.2 | 4.4 | 0.2s |
| planner_worker_off | 0.8 | 2.0 | 0.0 | 4.2 | 0.1s |

## 읽는 법

- 정상 경로(제약을 한 번에 충족 · Tool 불필요)에서 Worker LLM 호출이 0 이어야 한다.
- 'Tool 하나가 실패'에서 Worker LLM 을 켜면 검색어를 고쳐 같은 Tool 을 다시 부르고(LLM 호출 +1), 끄면 실패를 그대로 보고한다.
- 재계획 횟수는 두 구조가 같아야 정상이다 — 재계획 판단(judge)은 같은 규칙을 쓴다.
- 실행 로그 화면에서 세션 `cmp-…` 를 열면 각 실행의 단계 · Tool 호출 · Planner/Worker 메시지를 볼 수 있다.
- 결과가 `오류 LLM_…` 인 줄은 GMS 호출이 실패한 실행이다 — 비교에서 빼고 읽는다.
