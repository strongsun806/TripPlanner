"""오프라인 시험용 가짜 LLM · 가짜 외부 API — 키와 네트워크 없이 Agent 흐름만 확인한다

install() 을 부르면 Tool 함수와 LLM 호출이 아래 가짜로 바뀐다.
    RAIN[날짜] = 강수확률     FAIL_FOOD["on"] = True 면 식당 Tool 이 INVALID_KEY 로 실패
    ANALYSES[메시지] = Analysis(...)  — analyze 단계가 돌려줄 값
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["GMS_KEY"] = "offline-test"
os.environ.setdefault("GMS_BASE_URL", "https://gms.example/gmsapi/api.anthropic.com/v1")   # 시험용 가짜 주소
os.environ.setdefault("DB_PATH", str(ROOT / "data" / "test_offline.db"))
if Path(os.environ["DB_PATH"]).name.startswith("test_"):    # 시험용 DB 는 매번 비우고 시작 (지난 실행의 약속이 섞이지 않게)
    Path(os.environ["DB_PATH"]).unlink(missing_ok=True)

import tools  # noqa: E402
from app.agent import llm  # noqa: E402
from app.agent.events import BUS  # noqa: E402
from app.agent.llm import Analysis, Conditions  # noqa: E402
from app.agent.runner import run_agent  # noqa: E402

TODAY = date.today()
SAT = (TODAY + timedelta(days=(5 - TODAY.weekday()) % 7 or 7)).isoformat()

# ── 가짜 외부 API ────────────────────────────────────────────
RAIN = {}                         # 날짜 → 강수확률
CALLS = []                        # 실제로 '부른' Tool 기록 (캐시 재사용 확인용)
FAIL_FOOD = {"on": False}
FOOD_EMPTY = set()                # 이 검색어로는 식당 결과가 없다 (Worker 재시도 확인용)
PLACES_EMPTY_UNDER = {"m": 0}     # 이 반경보다 작으면 후보가 없다

OSAKA = {"destination": "오사카시, 일본", "lat": 34.6937, "lon": 135.5023, "country_code": "jp"}
JONGNO = {"destination": "종로구, 서울", "lat": 37.5729, "lon": 126.9794, "country_code": "kr"}


def fake_geocode(destination):
    return tools.ok(JONGNO if "종로" in destination or "서울" in destination else OSAKA)


def fake_places(destination, style="outdoor", limit=10, radius_m=5000):
    CALLS.append(("search_places", style, radius_m))
    if radius_m < PLACES_EMPTY_UNDER["m"]:
        return tools.fail("NO_DATA", f"반경 {radius_m // 1000}km 안에 {style} 후보가 없습니다.")
    c = fake_geocode(destination)["data"]
    names = {"outdoor": ["공원 A", "성 B", "강변 C", "언덕 D"], "indoor": ["박물관 A", "수족관 B", "미술관 C"]}[style]
    places = [{"place_id": f"{style}{i}", "name": n, "lat": c["lat"] + 0.004 * i, "lon": c["lon"] + 0.003 * i,
               "category": style, "country_code": c["country_code"]} for i, n in enumerate(names)]
    return tools.ok({"destination": c["destination"], "center": {"lat": c["lat"], "lon": c["lon"]},
                     "country_code": c["country_code"], "style": style, "radius_m": radius_m, "places": places})


def fake_weather(lat, lon, visit_date=None):
    CALLS.append(("get_weather", visit_date))
    d = visit_date or TODAY.isoformat()
    country = "kr" if lon < 130 and lat > 33 else "jp"     # RAIN[(날짜, "jp")] 처럼 지역별로도 줄 수 있다
    prob = RAIN.get((d, country), RAIN.get(d, 10))
    return tools.ok({"date": d, "used_default_date": visit_date is None, "precip_prob": prob,
                     "temp_max": 15, "temp_min": 8, "conditions": "흐림", "note": "예보 기준"})


def fake_food(lat, lon, keyword="맛집", radius_m=700):
    CALLS.append(("search_nearby_food",))
    if FAIL_FOOD["on"]:
        return tools.fail("INVALID_KEY", "KAKAO_REST_KEY가 없습니다.")
    if keyword in FOOD_EMPTY:
        return tools.fail("NO_DATA", f"반경 {radius_m}m 안에 '{keyword}' 결과가 없습니다.")
    return tools.ok({"keyword": keyword, "places": [
        {"name": "식당 가", "address": "서울 종로구", "url": "http://place.map.kakao.com/1", "distance_m": 120}]})



# ── 가짜 LLM ─────────────────────────────────────────────────
ANALYSES = {}


def fake_analyze(message, previous, last_summary, turns):
    return ANALYSES[message]




def A(intent, reason="test", reset=True, **changes):
    return Analysis(intent=intent, reason=reason, reset=reset, changes=Conditions(**changes))


def install():
    import app.agent.graph as graph
    import app.agent.registry as reg
    tools.geocode = fake_geocode
    graph.geocode = fake_geocode
    reg.TOOLS["search_places"].func = fake_places
    reg.TOOLS["get_weather"].func = fake_weather
    reg.TOOLS["search_nearby_food"].func = fake_food
    llm.analyze = fake_analyze
    llm.respond = lambda msg, facts: f"[응답] 상태={facts.get('상태')} 일정={[i['name'] for i in facts.get('추천_일정', [])]}"
    llm.smalltalk = lambda msg, turns: "[인사] 여행지와 날짜를 말해 주세요."
    from app.agent.llm import WorkerDecision
    llm.worker_decide = lambda tool, spec, args, fixed, error, attempt: (
        WorkerDecision(action="retry", new_args={"keyword": "식당"}, reason="검색어를 더 일반적인 말로")
        if tool == "search_nearby_food" and args.get("keyword") != "식당" and "keyword" not in fixed
        else WorkerDecision(action="replan_request", reason="인자를 고쳐서는 해결할 수 없음"))
