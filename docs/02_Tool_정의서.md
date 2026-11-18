# Tool 정의서 (F3001 ~ F3004)

> 구현: `tools.py` · 확인: `python tools.py` (결과 캡처 `images/4-1_tool_check.png`, 2026-11-18)

## 공통 응답 형식

```json
{"ok": true,  "data": { ... }}
{"ok": false, "error": {"code": "NO_DATA", "message": "사람이 읽을 사유"}}
```

- 성공도 실패도 이 모양으로 돌려준다. 예외는 `Tool.run()` 이 잡아 실패로 바꾼다.
- 응답 원본을 넘기지 않고 판단에 쓸 값만 뽑는다. 비어 있는 값은 `None` 으로 둔다.
- 키가 든 요청 주소는 오류 메시지에 넣지 않는다 (NF3002).

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

## Tool 목록

### 1. search_places — 후보 목록 (F3002)

| 항목 | 내용 |
|---|---|
| 설명 (Agent 가 읽음) | 사용자가 여행지(도시 · 동네 이름)를 말했고 그곳에서 갈 만한 장소 후보가 필요할 때 부른다. 비가 와서 실내로 바꿔야 하면 style='indoor'로 다시 부른다. |
| 외부 API | Geoapify Geocoding API → Places API (전 세계) |
| 입력 | `destination` (string, 필수) · `style` ("outdoor" \| "indoor", 기본 outdoor) · `limit` (1~20, 기본 10) |
| 출력 | `destination`(정식 주소), `center{lat, lon}`, `country_code`, `style`, `places[{place_id, name, lat, lon, category, country_code}]` |
| 실패 | BAD_ARGUMENT · INVALID_KEY · NOT_FOUND · NO_DATA · UPSTREAM_ERROR |
| 다음 Tool 로 넘기는 값 | `center` 또는 `places[].lat/lon` → get_weather · search_nearby_food |

- style → 카테고리: outdoor = 관광명소 · 공원 · 자연 · 해변 / indoor = 박물관 · 문화시설 · 아쿠아리움 · 쇼핑몰
- 이름: 해외 장소는 현지어로 오므로 `name:ko` → `name:en` → `name` 순으로 고른다. 이름이 없는 장소는 뺀다.
- 검색 반경: 여행지 중심 5km.

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

- 결과 없음: `NO_DATA` + "반경 Nm 안에 'keyword' 결과가 없습니다."
- 식당은 일정 장소와 **위치로만** 묶는다. 영업 여부 · 가격은 확인할 수 없으므로 지어내지 않는다.

## 시험 결과 (2026-11-18, `python tools.py`)

| Tool | 정상 | 실패 확인 |
|---|---|---|
| search_places | 해외(오사카) · 실내 · 국내(종로) | 빈 입력 · 모르는 인자 · 없는 여행지 · 키 없음 |
| get_weather | 기본 날짜(오늘, 오사카 강수 100%) · 3일 뒤(81%) | 범위 밖 · 형식 틀림 · 키 없음 |
| search_nearby_food | 종로 식당 5곳 | 결과 없음 · 해외 좌표 · 키 없음 |

- search_places 의 오사카 좌표가 그대로 get_weather 입력으로 이어짐을 확인했다.
