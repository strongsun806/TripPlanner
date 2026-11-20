# Tool 정의서 (F3001 ~ F3004, 심화 F3022 · F3023)

> 구현: `tools.py` · 확인: `python tools.py` (결과 캡처 `images/4-1_tool_check.png`, 2026-11-18)

## 공통 응답 형식

```json
{"ok": true,  "data": { ... }}
{"ok": false, "error": {"code": "NO_DATA", "message": "사람이 읽을 사유"}}
```

- 성공도 실패도 이 모양으로 돌려준다. 예외는 `Tool.run()` 이 잡아 실패로 바꾼다.
- 응답 원본을 넘기지 않고 판단에 쓸 값만 뽑는다. 비어 있는 값은 `None` 으로 둔다.
- 키가 든 요청 주소는 오류 메시지에 넣지 않는다 (NF3002). 원인은 서버 로그에만 남기고, 그때도 `scrub()` 으로
  키 값 · `key=…` · `KakaoAK …` 를 `***` 로 가린다 (2일차 ⑤와 같은 방식). 실행 이벤트의 오류 문구도 같은 함수를 거친다.

## 실패 코드

| 코드 | 뜻 | 재계획? |
|---|---|---|
| BAD_ARGUMENT | 입력이 규격과 다르다 (빈 값 · 형식 틀림 · 모르는 인자) | 아니오 — 되묻는다 |
| INVALID_KEY | 키가 없거나 틀렸다 (HTTP 401 · 403 포함) | 아니오 — 설정 안내 |
| NOT_FOUND | 여행지 이름으로 위치를 찾지 못했다 | 아니오 — 되묻는다 |
| NO_DATA | 조회는 됐지만 결과가 비었다 | 예 — 조건을 바꿔 다시 |
| UPSTREAM_ERROR | 외부 API 시간 초과 · 서버 오류 | 아니오 — 사유 기록 후 진행 |
| OUT_OF_RANGE | 예보 범위(오늘 ~ 14일 뒤) 밖의 날짜 | 아니오 — 날씨 판단 보류를 밝힌다 |
| NO_FORECAST | 예보는 왔지만 강수확률이 비어 있다 | 아니오 — 날씨 판단하지 않음 |
| OUT_OF_REGION | 카카오 로컬에 해외 좌표를 넣었다 | 아니오 — 보조 정보 없이 진행 |
| NO_INDEX | 문서 색인이 아직 없다 (F3022) | 아니오 — '아직 모으지 못했다'고 답한다 |
| ALREADY_REGISTERED | 이미 캘린더에 등록한 일정 (F3023) | 아니오 |

## Tool 목록

### 1. search_places — 후보 목록 (F3002)

| 항목 | 내용 |
|---|---|
| 설명 (Agent 가 읽음) | 사용자가 여행지(도시 · 동네 이름)를 말했고 그곳에서 갈 만한 장소 후보가 필요할 때 부른다. 비가 와서 실내로 바꿔야 하면 style='indoor'로 다시 부른다. |
| 외부 API | Geoapify Geocoding API → Places API (전 세계) |
| 입력 | `destination` (string, 필수) · `style` ("outdoor" \| "indoor", 기본 outdoor) · `limit` (1~20, 기본 10) · `radius_m` (500~30000, 기본 5000 — 재계획 때 넓히거나 좁힌다) |
| 출력 | `destination`(정식 주소), `center{lat, lon}`, `country_code`, `style`, `radius_m`, `places[{place_id, name, lat, lon, category, country_code, wiki, score}]`, `filtered_out`(거른 수) |
| 실패 | BAD_ARGUMENT · INVALID_KEY · NOT_FOUND · NO_DATA · UPSTREAM_ERROR |
| 다음 Tool 로 넘기는 값 | `center` 또는 `places[].lat/lon` → get_weather · search_nearby_food |

- style → 카테고리: outdoor = 관광명소 · 공원 · 자연 · 해변 / indoor = 박물관 · 문화시설 · 아쿠아리움 · 쇼핑몰
- 이름: 해외 장소는 현지어로 오므로 `name:ko` → `name:en` → `name` 순으로 고른다. 이름이 없는 장소 · 같은 이름은 뺀다.
- **관광지가 아닌 곳 거르기**: 지도 데이터에 '사적'으로 잡혀 있어도 병원 · 학교 · 관공서 · 주차장, 건물이 사라진 '…터' 표석, `(1887~2008)` 처럼 지금은 없는 건물은 뺀다 (`is_sight`). 넉넉히(limit × 4, 최대 60) 받아 거른 뒤 `limit` 개를 돌려준다.
- 길가 조형물 · 기념 표지(artwork · memorial)는 위키백과 문서가 있을 만큼 이름난 것만 남긴다. 실내(indoor)는 박물관 · 문화시설 · 아쿠아리움 · 동물원 · 쇼핑몰만.
- **위키백과로 순위 보강**: OSM 태그에 위키백과(위키데이터) 문서가 있는 곳은 이름난 곳으로 보고 앞에 둔다 (`score` 3점, 명소 · 박물관 분류 +2). 문서 링크는 `wiki` 로 넘겨 탑승권에 '위키백과에서 보기'와 요약 첫 문장(`wiki_summary`, 출처 위키백과 CC BY-SA)을 붙인다.
- **사용자가 뺀 장소**: '병원 빼' 같은 말은 분석 단계의 `exclude` 조건이 되고, 판단 단계가 이름 · 분류로 거른다. 대화 내내 쌓여 후속 요청에도 유지된다.
- 검색 반경: 여행지 중심 5km 에서 시작. 후보가 없으면 10 · 20km 로 넓히고, 동선이 길면 2km 로 좁힌다 (재계획).
- 여행지 이름 → 좌표는 `geocode()` 도우미가 한다. 판단이 필요 없는 일이라 Tool 로 등록하지 않고 실행 흐름(plan 단계)이 직접 부르며, 같은 이름은 한 번만 조회한다.

### 2. get_weather — 판단 근거 (F3003)

| 항목 | 내용 |
|---|---|
| 설명 | 후보 장소의 좌표와 방문 날짜로 그날 비가 올지(강수확률) 확인해야 할 때 부른다. 오늘부터 14일 뒤까지만 조회된다. |
| 외부 API | Visual Crossing Timeline API (전 세계) |
| 입력 | `lat`, `lon` (number, 필수) · `visit_date` (YYYY-MM-DD, 없으면 오늘) |
| 출력 | `date`, `used_default_date`, `precip_prob`(%), `temp_max`, `temp_min`, `conditions`, `note` |
| 실패 | BAD_ARGUMENT · INVALID_KEY · OUT_OF_RANGE · NO_DATA · NO_FORECAST · UPSTREAM_ERROR |

- **기본값**: 날짜가 없으면 오늘로 조회하고 `used_default_date: true` 와 note 로 밝힌다.
- **판단에 쓰는 값**: `precip_prob` — 50% 를 넘으면 실내로 재계획 (`01_도메인_확정.md` R1).
- 그날 하루만 요청한다 (무료 플랜은 조회한 날짜 수만큼 할당량 사용).

### 3. search_nearby_food — 보조 정보 (F3004)

| 항목 | 내용 |
|---|---|
| 설명 | 국내 여행지에서 일정 근처의 식당 · 카페를 함께 알려 줘야 할 때 부른다. 해외 좌표는 지원하지 않는다. |
| 외부 API | 카카오 로컬 키워드 검색 (국내) |
| 입력 | `lat`, `lon` (필수) · `keyword` (기본 "맛집") · `radius_m` (1~20000, 기본 700) |
| 출력 | `keyword`, `places[{name, address, url, distance_m}]` (가까운 순 5곳) |
| 실패 | BAD_ARGUMENT · INVALID_KEY · OUT_OF_REGION · NO_DATA · UPSTREAM_ERROR |

- 검색어 정리: '예쁜 카페 위주'처럼 꾸밈말이 붙으면 그대로 검색해 결과가 0 이 된다 → 카페 · 커피 · 디저트는 '카페', 나머지는 꾸밈말(예쁜 · 맛있는 · 분위기 …)을 빼고 검색 (`food_query`).
- '카페' · '맛집'처럼 넓은 말인데 결과가 없으면 카카오 **분류 검색**(카페 CE7 · 음식점 FD6)으로 한 번 더 찾는다. 특정 가게 이름은 그대로 `NO_DATA`.
- 결과 없음: `NO_DATA` + "반경 Nm 안에 'keyword' 결과가 없습니다."
- 식당은 일정 장소와 **위치로만** 묶는다. 영업 여부 · 가격은 확인할 수 없으므로 지어내지 않는다.

## 로컬 서버 API 툴 — 이 서비스가 가진 기능을 감싼 Tool (`app/agent/local_tools.py`)

외부 API 툴과 같은 공통 응답 형식과 실패 코드를 쓴다. `GET /api/tools` 에서 `kind: "local"` 로 구분된다.

### 4. get_schedule — 그날 내 약속 (심화 F3023)

| 항목 | 내용 |
|---|---|
| 설명 | 여행 날짜가 정해졌을 때 그날 사용자의 다른 약속이 있는지 확인해야 할 때 부른다. 빈 시간에 맞춰 일정을 짠다. |
| 구현체 | `CALENDAR_BACKEND=local`(SQLite, 기본) · `google`(Google Calendar API) — 입출력은 같다 |
| 입력 | `visit_date` (YYYY-MM-DD, 필수) |
| 출력 | `date`, `backend`, `events[{title, start_time, end_time, source, plan_id}]` |
| 실패 | BAD_ARGUMENT · UPSTREAM_ERROR |
| 판단 반영 | judge 가 시간창에서 약속과 겹치는 부분을 빼고 가장 긴 빈 구간에 일정을 넣는다 |

### 5. register_schedule — 저장한 일정을 캘린더에 등록 (심화 F3023)

| 항목 | 내용 |
|---|---|
| 설명 | 사용자가 저장한 여행 일정을 캘린더에 넣어 달라고 할 때 부른다. 일정 번호 하나만 받는다. |
| 입력 | `plan_id` (integer, 필수) — 제목 · 날짜 · 시각은 Tool 인자가 아니라 서버가 저장소에서 읽는다 |
| 출력 | `plan_id`, `title`, `date`, `start_time`, `end_time`, `backend`, `event` |
| 실패 | BAD_ARGUMENT · NOT_FOUND · ALREADY_REGISTERED · UPSTREAM_ERROR |

### 6. search_travel_docs — 여행지 지식 검색 (심화 F3022)

| 항목 | 내용 |
|---|---|
| 설명 | 여행지의 특징 · 베스트 시즌 · 역사처럼 외부 API 로 확인할 수 없는 지식을 물었을 때 부른다. 근거 문서와 출처를 함께 돌려준다. |
| 색인 | 2회차 TripPilot 의 ChromaDB — `travel_meta`(여행지 500건) · `travel_wiki`(위키백과 청크). 같은 임베딩(text-embedding-3-small, 실패 시 같은 해시 벡터) |
| 입력 | `query` (string, 필수) · `k` (1~8, 기본 4) |
| 출력 | `embedding`(openai · fallback), `collections{이름: 건수}`, `hits[{title, source, url, collection, snippet, distance}]` |
| 실패 | BAD_ARGUMENT · NO_INDEX(색인 없음) · NO_DATA · UPSTREAM_ERROR |

## 시험 결과 (2026-11-18, `python tools.py`)

| Tool | 정상 | 실패 확인 |
|---|---|---|
| search_places | 해외(오사카) · 실내 · 국내(종로) | 빈 입력 · 모르는 인자 · 없는 여행지 · 키 없음 |
| get_weather | 기본 날짜(오늘, 오사카 강수 100%) · 3일 뒤(81%) | 범위 밖 · 형식 틀림 · 키 없음 |
| search_nearby_food | 종로 식당 5곳 | 결과 없음 · 해외 좌표 · 키 없음 |

- search_places 의 오사카 좌표가 그대로 get_weather 입력으로 이어짐을 확인했다.
