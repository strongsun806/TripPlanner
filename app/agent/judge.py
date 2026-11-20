"""제약 판단 계산 (F3008) — LLM 없이 규칙과 계산만 쓴다

외부 API 로 확인할 수 없는 값(실제 체류 시간, 영업시간, 교통 소요)은 지어내지 않는다.
API 가 준 좌표로 도보 시간을 계산하고, 시간창에서 이동 시간을 뺀 나머지를 장소 수로 나눠
'각 장소에 머물 수 있는 시간'과 '늦어도 언제 출발해야 하는지'를 역산해 제시한다.

    도보 시간(분) = 직선거리(km) × DETOUR ÷ WALK_KMH × 60   (5분 단위 올림 — 넉넉하게)
    장소당 체류(분) = (시간창 − 이동 시간 합) ÷ 장소 수        (5분 단위 내림, MIN_STAY_MIN 이상이어야 한다)
"""
import math

from .. import config


def to_min(hhmm: str) -> int:
    h, m = hhmm[:5].split(":")
    return int(h) * 60 + int(m)


def fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def distance_km(a: dict, b: dict) -> float:
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def walk_min(a: dict, b: dict) -> int:
    raw = distance_km(a, b) * config.DETOUR / config.WALK_KMH * 60
    return max(5, math.ceil(raw / 5) * 5)


# ── 날씨 판단 ────────────────────────────────────────────────
def judge_weather(weather: dict | None, style: str) -> dict:
    """status: ok · rain · unknown. unknown 이면 날씨로는 제외하지 않고 '판단 보류'를 밝힌다."""
    if not weather or not weather.get("ok"):
        code = (weather or {}).get("error", {}).get("code", "NOT_CALLED")
        return {"status": "unknown", "code": code,
                "note": {"OUT_OF_RANGE": "예보 범위(오늘부터 14일 뒤까지) 밖이라 날씨는 판단하지 않았습니다.",
                         "NO_FORECAST": "강수확률이 비어 있어 날씨는 판단하지 않았습니다.",
                         "INVALID_KEY": "날씨 API 키가 없어 날씨를 확인하지 못했습니다."}
                .get(code, "날씨를 확인하지 못해 날씨는 판단하지 않았습니다.")}
    d = weather["data"]
    rainy = d["precip_prob"] > config.RAIN_THRESHOLD
    return {"status": "rain" if rainy and style == "outdoor" else "ok",
            "precip_prob": d["precip_prob"], "threshold": config.RAIN_THRESHOLD, "rainy": rainy,
            "date": d["date"], "conditions": d.get("conditions"),
            "note": f"강수확률 {d['precip_prob']:.0f}% (기준 {config.RAIN_THRESHOLD}%)"
                    + (" — 야외 일정은 제외" if rainy and style == "outdoor" else "")}


# ── 시간창 ───────────────────────────────────────────────────
def free_window(start: int, end: int, busy: list[dict]) -> tuple[int, int, list[dict]]:
    """조회한 일정(busy)과 겹치는 부분을 빼고 가장 긴 빈 구간을 고른다 (심화 F3023)."""
    cuts = sorted(((max(start, to_min(b["start_time"])), min(end, to_min(b["end_time"])), b)
                   for b in busy if to_min(b["start_time"]) < end and start < to_min(b["end_time"])),
                  key=lambda c: (c[0], c[1]))     # 같은 시각 약속이 둘이어도 정렬이 깨지지 않게 (dict 는 비교 불가)
    gaps, t = [], start
    for s, e, _ in cuts:
        if s > t:
            gaps.append((t, s))
        t = max(t, e)
    if t < end:
        gaps.append((t, end))
    if not gaps:
        return start, start, [c[2] for c in cuts]
    best = max(gaps, key=lambda g: g[1] - g[0])
    return best[0], best[1], [c[2] for c in cuts]


# ── 사용자가 뺀 장소 ──
KIND_WORDS = {"병원": ("병원", "의원", "hospital", "healthcare"), "학교": ("학교", "대학", "school", "education"),
              "교회": ("교회", "성당", "church", "place_of_worship.christian"), "절": ("사", "절", "temple"),
              "공원": ("공원", "park"), "박물관": ("박물관", "museum"), "쇼핑": ("쇼핑", "몰", "mall", "shopping")}


def apply_exclude(places: list[dict], words: list[str]) -> tuple[list[dict], list[dict]]:
    """'병원'처럼 종류를 말하면 이름 · 분류로, '오사카성'처럼 이름을 말하면 이름으로 거른다"""
    if not words:
        return places, []
    keep, out = [], []
    for p in places:
        name, cat = p["name"].lower(), (p.get("category") or "").lower()
        hit = next((w for w in words if w.lower() in name or any(
            k.lower() in name or k.lower() in cat for k in KIND_WORDS.get(w, ()) if len(k) > 1)), None)
        (out if hit else keep).append(p if not hit else {"name": p["name"], "reason": f"요청에 따라 제외 ('{hit}')"})
    return keep, out


# ── 동선 · 일정 ──────────────────────────────────────────────
def route(center: dict, places: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """중심에서 가장 가까운 곳부터 '가장 가까운 다음 장소'로 잇는다.
    직전 장소에서 MAX_LEG_MIN 보다 먼 곳은 동선에서 빼고 사유를 남긴다."""
    left = list(places)
    if not left:
        return [], [], []
    first = min(left, key=lambda p: distance_km(center, p))
    order, legs, excluded = [first], [], []
    left.remove(first)
    while left:
        nxt = min(left, key=lambda p: distance_km(order[-1], p))
        left.remove(nxt)
        m = walk_min(order[-1], nxt)
        if m > config.MAX_LEG_MIN:
            excluded.append({"name": nxt["name"], "reason": f"직전 장소에서 도보 {m}분 — 동선 상한 {config.MAX_LEG_MIN}분 초과"})
            continue
        order.append(nxt)
        legs.append(m)
    return order, legs, excluded


def build_itinerary(center: dict, places: list[dict], start: int, end: int) -> dict:
    """시간창 [start, end] 안에 넣을 수 있는 가장 많은 장소(최대 MAX_PLACES)로 일정을 만든다."""
    order, legs, excluded = route(center, places)
    available = end - start
    for n in range(min(config.MAX_PLACES, len(order)), 0, -1):
        travel = sum(legs[: n - 1])
        stay = (available - travel) // n // 5 * 5
        if stay >= config.MIN_STAY_MIN:
            items, t = [], start
            for i, p in enumerate(order[:n]):
                leg = legs[i] if i < n - 1 else None
                items.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "category": p.get("category"),
                              "wiki": p.get("wiki"),
                              "start_time": fmt(t), "end_time": fmt(t + stay), "stay_min": stay,
                              "walk_to_next_min": leg})
                t += stay + (leg or 0)
            unused = order[n:]
            excluded += [{"name": p["name"],
                          "reason": f"더 넣으면 장소당 체류가 {config.MIN_STAY_MIN}분 미만"} for p in unused[:3]]
            return {"ok": True, "items": items, "stay_min": stay, "travel_min": travel,
                    "window": [fmt(start), fmt(end)], "available_min": available,
                    "latest_leave": items[-1]["end_time"], "excluded": excluded, "unused_count": len(unused)}
    reason = (f"시간창이 {available}분이라 한 장소에 {config.MIN_STAY_MIN}분도 머물 수 없습니다."
              if available < config.MIN_STAY_MIN else "이동 시간을 넣으면 시간창에 들어가는 후보가 없습니다.")
    return {"ok": False, "reason": reason, "window": [fmt(start), fmt(end)], "available_min": available,
            "excluded": excluded}
