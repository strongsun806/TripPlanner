"""도메인 확정용 — 외부 API 3종이 실제로 호출되는지 확인한다 (1일차 16시까지)

준비:
    1. .env.example 을 .env 로 복사하고 GEOAPIFY_KEY · KAKAO_REST_KEY 를 넣는다
    2. python api_check.py
    3. 세 줄 모두 OK 면 도메인 확정. FAIL 이면 사유를 읽고 고친다

여기서는 '호출이 되는가'와 '다음 단계에 쓸 값이 있는가'만 본다.
Tool 모양(ok/fail, 실패 코드)은 tools.py 에서 만든다.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()
GEO_KEY = os.getenv("GEOAPIFY_KEY")
KAKAO_KEY = os.getenv("KAKAO_REST_KEY")
VC_KEY = os.getenv("VISUALCROSSING_KEY")

# 해외 · 국내 한 곳씩 — 같은 코드가 둘 다 되는지 본다
OSAKA = (135.5023, 34.6937)      # (경도, 위도)
SEOUL = (126.9780, 37.5665)


def check(name, func):
    try:
        print(f"OK    {name} → {func()}")
    except Exception as e:            # 오류 원문에는 키가 든 주소가 섞일 수 있어 종류와 앞부분만 찍는다
        print(f"FAIL  {name} → {type(e).__name__}: {str(e)[:80]}")


def geoapify(lon, lat):
    """① 후보 목록 — Geoapify Places (전 세계 관광지)"""
    res = requests.get(
        "https://api.geoapify.com/v2/places",
        params={"categories": "tourism.sights",
                "filter": f"circle:{lon},{lat},3000",     # 반경 3km
                "limit": 3, "lang": "ko", "apiKey": GEO_KEY},
        timeout=10)
    res.raise_for_status()
    feats = res.json()["features"]
    return [(f["properties"]["place_id"][:10] + "…", f["properties"].get("name"))
            for f in feats]                                # 다음 Tool 입력 = 좌표 · place_id


def open_meteo(lon, lat):
    """② 판단 근거 — Open-Meteo 일별 강수확률 (키 없음, 최대 16일)"""
    res = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lon,
                "daily": "precipitation_probability_max,temperature_2m_max",
                "forecast_days": 16, "timezone": "auto"},
        timeout=30)                                        # 첫 응답이 느릴 때가 있어 넉넉히
    res.raise_for_status()
    d = res.json()["daily"]
    return f"{len(d['time'])}일치, 첫날 {d['time'][0]} 강수확률 {d['precipitation_probability_max'][0]}%"


def visual_crossing(lon, lat):
    """② 판단 근거 (대안) — Visual Crossing 일별 강수확률 (키 필요, 최대 15일)"""
    res = requests.get(
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{lat},{lon}",
        params={"unitGroup": "metric", "include": "days",
                "elements": "datetime,precipprob,tempmax",
                "contentType": "json", "key": VC_KEY},
        timeout=30)
    res.raise_for_status()
    days = res.json()["days"]
    return f"{len(days)}일치, 첫날 {days[0]['datetime']} 강수확률 {days[0]['precipprob']}%"


def kakao(lon, lat):
    """③ 보조 정보 — 카카오 로컬 주변 식당 (국내)"""
    res = requests.get(
        "https://dapi.kakao.com/v2/local/search/keyword.json",
        headers={"Authorization": f"KakaoAK {KAKAO_KEY}"},
        params={"query": "맛집", "x": lon, "y": lat, "radius": 1000, "size": 3},
        timeout=10)
    res.raise_for_status()
    return [(d["place_name"], d["place_url"]) for d in res.json()["documents"]]


if __name__ == "__main__":
    if not (GEO_KEY and KAKAO_KEY and VC_KEY):
        print("키가 없습니다. .env 에 GEOAPIFY_KEY · VISUALCROSSING_KEY · KAKAO_REST_KEY 를 넣으세요.\n")
    check("Geoapify 오사카 관광지", lambda: geoapify(*OSAKA))
    check("Geoapify 서울 관광지  ", lambda: geoapify(*SEOUL))
    # check("Open-Meteo 오사카 날씨", lambda: open_meteo(*OSAKA))  # 교육장 네트워크에서 차단됨(연결 강제 종료) → Visual Crossing 사용
    check("VisualCrossing 오사카 ", lambda: visual_crossing(*OSAKA))
    check("카카오 서울 주변 식당 ", lambda: kakao(*SEOUL))
