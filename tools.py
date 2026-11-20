"""TripPlanner Tool — 외부 API 3종을 Agent 가 부를 수 있는 Tool 로 감싼다 (F3001 ~ F3004)

    search_places      Geoapify   여행지 이름 → 좌표 → 주변 관광 후보 (전 세계)      F3002
    get_weather        VisualCrossing  좌표 + 날짜 → 그날 강수확률 · 기온 (최대 15일)   F3003
    search_nearby_food 카카오 로컬  좌표 → 주변 식당 이름 · 주소 · 링크 (국내)       F3004

실행:  python tools.py   → Tool 목록과 정상 · 실패 결과가 찍힌다

지킬 약속 (tools_start.py 와 같다)
    · 성공도 실패도 ok() · fail() 로 돌려준다. 예외를 밖으로 던지지 않는다
    · 응답 원본을 통째로 넘기지 않는다. 판단에 쓸 값만 뽑는다
    · 비어 있는 값은 "" 나 0 으로 채우지 않는다 (None 으로 둔다)
"""
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import requests
from dotenv import load_dotenv

load_dotenv()

# ── ① 약속: 성공도 실패도 같은 모양 ──────────────────────────
def ok(data):
    return {"ok": True, "data": data}

def fail(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}

# 실패 코드
#   BAD_ARGUMENT    입력이 규격과 다르다 (빈 값 · 형식 틀림 · 모르는 인자)
#   INVALID_KEY     키가 없거나 틀렸다
#   NOT_FOUND       찾는 대상이 없다 (모르는 여행지 이름)
#   NO_DATA         조회는 됐지만 결과가 비었다
#   UPSTREAM_ERROR  외부 API 가 응답하지 않았다 (시간 초과 · 서버 오류)
#   ── 여행 도메인에서 더한 것 ──
#   OUT_OF_RANGE    예보 범위(오늘 ~ 14일 뒤) 밖의 날짜
#   NO_FORECAST     예보는 왔지만 강수확률이 비어 있다 → 날씨 판단을 하지 않는다
#   OUT_OF_REGION   카카오 로컬은 국내만 — 해외 좌표


# ── ② Tool 명함 ──────────────────────────────────────────────
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable

    def run(self, **kwargs):
        try:
            return self.func(**kwargs)
        except TypeError:
            return fail("BAD_ARGUMENT", "입력값이 규약과 다릅니다.")
        except Exception:
            return fail("UPSTREAM_ERROR", "Tool 실행 중 오류입니다.")


# ── ⑤ 키 가리기 (2일차 scrub 과 같은 방식) ─────────────────────
SECRET_NAMES = ["GEOAPIFY_KEY", "VISUALCROSSING_KEY", "KAKAO_REST_KEY", "GMS_KEY", "GMS_API_KEY", "DATA_GO_KR_KEY"]
PARAM = re.compile(r"(?i)\b(key|apikey|api_key|authkey|servicekey|token)=([^&\s'\"]+)")


def scrub(text) -> str:
    """오류 문구 · 로그에 키가 실려 나가지 않게 — 값으로 한 번, 이름=값 모양으로 한 번 가린다"""
    text = str(text)
    for name in SECRET_NAMES:
        secret = os.getenv(name, "")
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(r"(?i)(KakaoAK|Bearer)\s+\S+", r"\1 ***", text)
    return PARAM.sub(lambda m: f"{m.group(1)}=***", text)


# ── 공통: 외부 API 한 번 부르기 ───────────────────────────────
def call_api(url, params=None, headers=None):
    """(본문, None) 또는 (None, fail(...)) 을 돌려준다. 키가 든 주소는 오류 메시지에 넣지 않는다."""
    try:
        res = requests.get(url, params=params, headers=headers, timeout=15)
    except requests.RequestException as e:
        print("[log]", scrub(e))                  # 원인은 서버 로그에만, 키는 가린다
        return None, fail("UPSTREAM_ERROR", "외부 API를 부르지 못했습니다.")
    if res.status_code in (401, 403):
        return None, fail("INVALID_KEY", "API 키가 틀렸거나 권한이 없습니다.")
    if res.status_code >= 400:
        return None, fail("UPSTREAM_ERROR", f"외부 API 오류입니다. (HTTP {res.status_code})")
    try:
        return res.json(), None
    except ValueError:
        return None, fail("UPSTREAM_ERROR", "외부 API 응답을 읽지 못했습니다.")


# ── ③-1 후보 목록 — Geoapify (F3002) ─────────────────────────
GEO_KEY_NAME = "GEOAPIFY_KEY"

# 선호(야외 · 실내)를 Geoapify 카테고리로 바꾼다. 재계획 때 outdoor → indoor 로 바꿔 부른다
CATEGORIES = {
    "outdoor": "tourism.sights,tourism.attraction,leisure.park,natural,beach,heritage",
    "indoor": "entertainment.museum,entertainment.culture,entertainment.aquarium,entertainment.zoo,"
              "tourism.attraction,commercial.shopping_mall",
}

# 여행 일정에 넣으면 안 되는 곳 — 지도 데이터에 '사적'으로 잡혀 있어도 사람이 구경 가는 곳이 아니다
# (병원 · 학교 · 관공서 · 주차장, 건물이 사라진 '…터' 표석 등)
NOT_A_SIGHT = re.compile(r"(병원|의원|보건소|요양|학교|대학교|유치원|어린이집|주차장|아파트|오피스텔|사무소|"
                         r"우체국|경찰서|소방서|파출소|주민센터|은행|약국|교회|성당|hospital|clinic|school|parking)",
                         re.IGNORECASE)
SITE_MARKER = re.compile(r"((?<!센)터|표석|표지석|집터|옛터)$")       # '센터'는 남긴다
FORMER = re.compile(r"\(\d{4}\s*[~-]\s*\d{4}\)")                   # '(1887~2008)' — 지금은 없는 건물
SKIP_CATEGORY = ("healthcare", "education", "office", "service", "parking", "building.residential")


def is_sight(name: str, categories: list[str]) -> bool:
    if NOT_A_SIGHT.search(name) or SITE_MARKER.search(name.strip()) or FORMER.search(name):
        return False
    return not any(c.startswith(SKIP_CATEGORY) for c in categories or [])


def wiki_link(raw: dict) -> dict | None:
    """OSM 태그의 위키백과 문서 — 'ko:흥인지문' → 링크. 문서가 있는 곳은 이름난 곳이라 후보 순위를 올린다"""
    w = raw.get("wikipedia") or raw.get("wikipedia:ko")
    if w:
        lang, _, title = w.partition(":") if ":" in w else ("ko", "", w)
        title = title or w
        return {"title": title, "url": f"https://{lang or 'ko'}.wikipedia.org/wiki/{title.replace(' ', '_')}"}
    if raw.get("wikidata"):
        return {"title": raw["wikidata"], "url": f"https://www.wikidata.org/wiki/{raw['wikidata']}"}
    return None


_WIKI_CACHE: dict[str, str | None] = {}


def _wiki_fetch(host: str, title: str) -> dict | None:
    try:
        r = requests.get(f"{host}/api/rest_v1/page/summary/{title}", timeout=4,
                         headers={"User-Agent": "TripPlanner/1.0 (SSAFY AI project)"})
        if r.ok:
            j = r.json()
            if j.get("type") == "standard" and j.get("extract"):
                return j
    except requests.RequestException:
        pass
    return None


def wiki_summary(wiki: dict | None, name: str | None = None) -> dict | None:
    """위키백과 요약 첫 문장 (출처: 위키백과, CC BY-SA). 한국어 문서를 먼저 — 태그가 영어 문서면 장소 이름으로
    한국어 위키백과를 한 번 더 찾는다. 못 가져오면 None — 일정은 그대로 진행한다.
    돌려주는 값: {"about": 첫 문장, "url": 실제로 읽은 문서 주소}"""
    if not wiki or "wikipedia.org/wiki/" not in wiki.get("url", ""):
        return None
    url = wiki["url"]
    key = f"{url}|{name}"
    if key in _WIKI_CACHE:
        return _WIKI_CACHE[key]
    host, title = url.split("/wiki/", 1)
    tries = [(host, title)]
    if "ko.wikipedia" not in host and name and re.search(r"[가-힣]", name):
        tries.insert(0, ("https://ko.wikipedia.org", name.replace(" ", "_")))
    out = None
    for h, t in tries:
        j = _wiki_fetch(h, t)
        if j:
            first = re.split(r"(?<=\.)\s", j["extract"].strip(), maxsplit=1)[0]
            out = {"about": (first[:117] + "…") if len(first) > 120 else first, "url": f"{h}/wiki/{t}"}
            break
    _WIKI_CACHE[key] = out
    return out


MINOR = ("tourism.attraction.artwork", "tourism.sights.memorial", "tourism.attraction.viewpoint")


def place_score(cats: list[str], wiki: dict | None) -> int:
    """이름난 관광지일수록 높게 — 위키백과 문서 > 명소 · 박물관 분류 > 나머지. 조형물 · 기념 표지는 낮게"""
    score = 3 if wiki else 0
    if any(c.startswith(MINOR) for c in cats) and not wiki:
        return score - 2
    if any(c.startswith(("tourism.attraction", "tourism.sights.castle", "tourism.sights.place_of_worship.temple",
                         "entertainment.museum", "heritage")) for c in cats):
        score += 2
    elif any(c.startswith(("tourism.sights", "entertainment", "leisure.park")) for c in cats):
        score += 1
    return score

def pick_name(props):
    """해외 장소는 현지어로 온다 → 한국어 · 영어 이름이 있으면 그걸 먼저 쓴다"""
    raw = props.get("datasource", {}).get("raw", {})
    return raw.get("name:ko") or raw.get("name:en") or props.get("name")

_GEOCODE_CACHE: dict[str, dict] = {}

def geocode(destination: str):
    """여행지 이름 → 중심 좌표. Tool 이 아니라 실행 흐름이 직접 쓰는 도우미다
    (모델 판단이 필요 없는 작업은 Tool 로 두지 않는다 — 명세서 2.3). 같은 이름은 한 번만 부른다."""
    if not destination or not destination.strip():
        return fail("BAD_ARGUMENT", "destination은 필수입니다.")
    name = destination.strip()
    if name in _GEOCODE_CACHE:
        return ok(_GEOCODE_CACHE[name])
    key = os.getenv(GEO_KEY_NAME)
    if not key:
        return fail("INVALID_KEY", f"{GEO_KEY_NAME}가 없습니다.")
    body, err = call_api("https://api.geoapify.com/v1/geocode/search",
                         {"text": name, "limit": 1, "lang": "ko", "format": "json", "apiKey": key})
    if err:
        return err
    if not body.get("results"):
        return fail("NOT_FOUND", f"'{name}'의 위치를 찾지 못했습니다.")
    c = body["results"][0]
    _GEOCODE_CACHE[name] = {"destination": c.get("formatted"), "lat": c["lat"], "lon": c["lon"],
                            "country_code": c.get("country_code")}
    return ok(_GEOCODE_CACHE[name])


def search_places(destination: str, style: str = "outdoor", limit: int = 10, radius_m: int = 5000):
    if not destination or not destination.strip():
        return fail("BAD_ARGUMENT", "destination은 필수입니다.")
    if style not in CATEGORIES:
        return fail("BAD_ARGUMENT", f"style은 {list(CATEGORIES)} 중 하나입니다.")
    if not 1 <= limit <= 20:
        return fail("BAD_ARGUMENT", "limit은 1~20입니다.")
    if not 500 <= radius_m <= 30000:
        return fail("BAD_ARGUMENT", "radius_m은 500~30000입니다.")
    key = os.getenv(GEO_KEY_NAME)
    if not key:
        return fail("INVALID_KEY", f"{GEO_KEY_NAME}가 없습니다.")

    # 1) 여행지 이름 → 중심 좌표
    center = geocode(destination)
    if not center["ok"]:
        return center
    c = center["data"]
    lat, lon = c["lat"], c["lon"]

    # 2) 중심 반경 안의 후보 — 재계획 때 반경을 넓히거나 좁혀 다시 부른다
    # 넉넉히 받아(최대 60) 관광지가 아닌 곳을 거르고, 위키백과 문서가 있는 이름난 곳을 앞에 둔다
    body, err = call_api("https://api.geoapify.com/v2/places",
                         {"categories": CATEGORIES[style], "filter": f"circle:{lon},{lat},{radius_m}",
                          "bias": f"proximity:{lon},{lat}", "limit": min(limit * 4, 60), "lang": "ko", "apiKey": key})
    if err:
        return err
    places, dropped, seen = [], 0, set()
    for f in body.get("features", []):
        p = f["properties"]
        name = pick_name(p)
        if not name or name in seen:          # 이름 없는 장소 · 같은 이름 중복은 뺀다
            continue
        cats = p.get("categories") or []
        if not is_sight(name, cats):
            dropped += 1
            continue
        seen.add(name)
        wiki = wiki_link(p.get("datasource", {}).get("raw", {}))
        places.append({"place_id": p["place_id"], "name": name,
                       "lat": p["lat"], "lon": p["lon"],
                       "category": cats[0] if cats else None,
                       "country_code": p.get("country_code"),
                       "wiki": wiki, "score": place_score(cats, wiki)})
    places.sort(key=lambda x: -x["score"])    # 안정 정렬 — 같은 점수면 가까운 순서(bias) 그대로
    places = places[:limit]
    if not places:
        return fail("NO_DATA", f"'{destination}' 반경 {radius_m // 1000}km 안에 {style} 후보가 없습니다.")
    return ok({"destination": c["destination"], "center": {"lat": lat, "lon": lon},
               "country_code": c["country_code"], "style": style, "radius_m": radius_m, "places": places,
               "filtered_out": dropped})


# ── ③-2 판단 근거 — Visual Crossing (F3003) ──────────────────
VC_KEY_NAME = "VISUALCROSSING_KEY"
FORECAST_DAYS = 14                            # 오늘 + 14일 = 15일치

def get_weather(lat: float, lon: float, visit_date: str | None = None):
    # 기준 날짜가 없으면 오늘로 본다 — 기본값을 썼다는 사실을 응답에 남긴다 (F3002 기본값 · 추정 표시)
    today = date.today()
    used_default = visit_date is None
    try:
        day = today if used_default else date.fromisoformat(visit_date)
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return fail("BAD_ARGUMENT", "visit_date는 YYYY-MM-DD, lat · lon은 숫자입니다.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return fail("BAD_ARGUMENT", "좌표 범위가 틀렸습니다.")
    if not today <= day <= today + timedelta(days=FORECAST_DAYS):
        return fail("OUT_OF_RANGE", f"예보는 {today} ~ {today + timedelta(days=FORECAST_DAYS)} 만 있습니다.")
    key = os.getenv(VC_KEY_NAME)
    if not key:
        return fail("INVALID_KEY", f"{VC_KEY_NAME}가 없습니다.")

    # 그날 하루만 요청한다 — 무료 플랜은 받은 날짜 수만큼 할당량을 쓴다
    body, err = call_api(
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{lat},{lon}/{day}",
        {"unitGroup": "metric", "include": "days", "lang": "ko",
         "elements": "datetime,precipprob,tempmax,tempmin,conditions", "contentType": "json", "key": key})
    if err:
        return err
    days = body.get("days") or []
    if not days:
        return fail("NO_DATA", f"{day} 예보가 없습니다.")
    d = days[0]
    if d.get("precipprob") is None:            # 비어 있으면 0 으로 채우지 않는다
        return fail("NO_FORECAST", f"{day} 강수확률이 비어 있어 날씨를 판단하지 않습니다.")
    return ok({"date": d["datetime"], "used_default_date": used_default,
               "precip_prob": d["precipprob"], "temp_max": d.get("tempmax"),
               "temp_min": d.get("tempmin"), "conditions": d.get("conditions"),
               "note": "예보 기준" + (" · 날짜를 받지 못해 오늘로 조회" if used_default else "")})


# ── ③-3 보조 정보 — 카카오 로컬 (F3004) ──────────────────────
KAKAO_KEY_NAME = "KAKAO_REST_KEY"

def in_korea(lat, lon):
    return 33.0 <= lat <= 38.7 and 124.5 <= lon <= 131.9   # 대략의 국내 범위

CAFE_WORDS = ("카페", "커피", "디저트", "베이커리", "빵집", "cafe", "coffee")
FILLER_WORDS = ("예쁜", "이쁜", "맛있는", "분위기", "좋은", "유명한", "근처", "괜찮은", "핫한", "감성", "위주", "적당한")


def food_query(keyword: str) -> tuple[str, str]:
    """'예쁜 카페 위주' 같은 말을 검색어로 — (검색어, 카카오 분류 코드). 카페 → CE7, 나머지 → FD6"""
    k = (keyword or "").strip()
    if any(w in k.lower() for w in CAFE_WORDS):
        return "카페", "CE7"
    for w in FILLER_WORDS:
        k = k.replace(w, " ")
    k = " ".join(k.split()) or "맛집"
    return k, "FD6"


def search_nearby_food(lat: float, lon: float, keyword: str = "맛집", radius_m: int = 700):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return fail("BAD_ARGUMENT", "lat · lon은 숫자입니다.")
    if not keyword:
        return fail("BAD_ARGUMENT", "keyword는 비울 수 없습니다.")
    if not 1 <= radius_m <= 20000:
        return fail("BAD_ARGUMENT", "radius_m은 1~20000입니다.")
    if not in_korea(lat, lon):
        return fail("OUT_OF_REGION", "카카오 로컬은 국내 좌표만 검색합니다.")
    key = os.getenv(KAKAO_KEY_NAME)
    if not key:
        return fail("INVALID_KEY", f"{KAKAO_KEY_NAME}가 없습니다.")

    query, group = food_query(keyword)
    auth = {"Authorization": f"KakaoAK {key}"}
    body, err = call_api("https://dapi.kakao.com/v2/local/search/keyword.json",
                         {"query": query, "x": lon, "y": lat, "radius": radius_m, "sort": "distance", "size": 5},
                         headers=auth)
    if err:
        return err
    docs = body.get("documents", [])
    if not docs:                               # 검색어로 없으면 분류(카페 · 음식점)로 한 번 더
        body, err = call_api("https://dapi.kakao.com/v2/local/search/category.json",
                             {"category_group_code": group, "x": lon, "y": lat, "radius": radius_m,
                              "sort": "distance", "size": 5}, headers=auth)
        if err:
            return err
        docs = body.get("documents", [])
    if not docs:                               # 결과 없음의 응답 형식 (F3004)
        return fail("NO_DATA", f"반경 {radius_m}m 안에 '{query}' 결과가 없습니다.")
    keyword = query
    return ok({"keyword": keyword, "places": [
        {"name": d["place_name"],
         "address": d.get("road_address_name") or d.get("address_name") or None,
         "url": d.get("place_url") or None,
         "distance_m": int(d["distance"]) if d.get("distance") else None}
        for d in docs]})


# ── ④ 등록: Agent 에게 넘기는 Tool 목록 (F3001) ───────────────
TOOLS = {t.name: t for t in [
    Tool(name="search_places",
         description="사용자가 여행지(도시 · 동네 이름)를 말했고 그곳에서 갈 만한 장소 후보가 필요할 때 부른다. "
                     "비가 와서 실내로 바꿔야 하면 style='indoor'로 다시 부른다.",
         parameters={"type": "object",
                     "properties": {"destination": {"type": "string", "description": "여행지 이름. 예: 오사카, 서울 종로"},
                                    "style": {"type": "string", "enum": ["outdoor", "indoor"], "default": "outdoor"},
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                                    "radius_m": {"type": "integer", "minimum": 500, "maximum": 30000, "default": 5000,
                                                 "description": "여행지 중심에서 찾을 반경(m)"}},
                     "required": ["destination"]},
         func=search_places),
    Tool(name="get_weather",
         description="후보 장소의 좌표와 방문 날짜로 그날 비가 올지(강수확률) 확인해야 할 때 부른다. "
                     "오늘부터 14일 뒤까지만 조회된다.",
         parameters={"type": "object",
                     "properties": {"lat": {"type": "number"}, "lon": {"type": "number"},
                                    "visit_date": {"type": "string", "format": "date",
                                                   "description": "YYYY-MM-DD. 없으면 오늘"}},
                     "required": ["lat", "lon"]},
         func=get_weather),
    Tool(name="search_nearby_food",
         description="국내 여행지에서 일정 근처의 식당 · 카페를 함께 알려 줘야 할 때 부른다. 해외 좌표는 지원하지 않는다.",
         parameters={"type": "object",
                     "properties": {"lat": {"type": "number"}, "lon": {"type": "number"},
                                    "keyword": {"type": "string", "default": "맛집"},
                                    "radius_m": {"type": "integer", "minimum": 1, "maximum": 20000, "default": 700}},
                     "required": ["lat", "lon"]},
         func=search_nearby_food),
]}

def catalog():
    """Agent 에게 넘기는 것은 이름 · 설명 · 입력 규격뿐"""
    return [{"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in TOOLS.values()]


# ── 시험 호출 (검증 4.1) ────────────────────────────────────
if __name__ == "__main__":
    for c in catalog():
        print("[Tool]", c["name"], "—", c["description"])
    print()

    def trial(tool, label, **args):
        r = TOOLS[tool].run(**args)
        shown = r["data"] if r["ok"] else f'{r["error"]["code"]} — {r["error"]["message"]}'
        print(f"{r['ok']!s:<5} {tool:<18} {label:<12} → {str(shown)[:150]}")
        return r

    def without_key(key_name, tool, **args):        # 키 없음: 잠깐 비우고 부른다
        saved = os.environ.pop(key_name, None)
        trial(tool, "키 없음", **args)
        if saved is not None:
            os.environ[key_name] = saved

    # 후보 목록
    r = trial("search_places", "정상(해외)", destination="오사카", limit=3)
    trial("search_places", "정상(실내)", destination="오사카", style="indoor", limit=3)
    seoul = trial("search_places", "정상(국내)", destination="서울 종로", limit=3)
    trial("search_places", "빈 입력", destination="")
    trial("search_places", "모르는 인자", destination="오사카", foo=1)
    trial("search_places", "없는 여행지", destination="zzqxwv없는곳")
    without_key(GEO_KEY_NAME, "search_places", destination="오사카")
    print()

    # 날씨 — 후보 목록이 준 좌표를 그대로 넘긴다 (F3002 → F3003 연결)
    lat, lon = (r["data"]["center"]["lat"], r["data"]["center"]["lon"]) if r["ok"] else (34.69, 135.50)
    trial("get_weather", "기본 날짜", lat=lat, lon=lon)
    trial("get_weather", "3일 뒤", lat=lat, lon=lon, visit_date=str(date.today() + timedelta(days=3)))
    trial("get_weather", "범위 밖", lat=lat, lon=lon, visit_date=str(date.today() + timedelta(days=21)))
    trial("get_weather", "형식 틀림", lat=lat, lon=lon, visit_date="내일")
    without_key(VC_KEY_NAME, "get_weather", lat=lat, lon=lon)
    print()

    # 주변 식당
    slat, slon = (seoul["data"]["center"]["lat"], seoul["data"]["center"]["lon"]) if seoul["ok"] else (37.5729, 126.9794)
    trial("search_nearby_food", "정상", lat=slat, lon=slon)
    trial("search_nearby_food", "결과 없음", lat=slat, lon=slon, keyword="zzqxwv없는가게")
    trial("search_nearby_food", "해외 좌표", lat=lat, lon=lon)
    without_key(KAKAO_KEY_NAME, "search_nearby_food", lat=slat, lon=slon)
