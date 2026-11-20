"""E3 용 — 가짜 후보 첫 곳에 위키백과 문서를 달고, 요약은 네트워크 없이 가짜로 돌려준다"""
import fakes
import tools

_orig = fakes.fake_places


def places_with_wiki(*a, **k):
    r = _orig(*a, **k)
    if r.get("ok") and r["data"]["places"]:
        r["data"]["places"][0]["wiki"] = {"title": "흥인지문", "url": "https://ko.wikipedia.org/wiki/흥인지문"}
        r["data"]["places"][0]["score"] = 3
    return r


fakes.fake_places = places_with_wiki
import app.agent.registry as reg  # noqa: E402
reg.TOOLS["search_places"].func = places_with_wiki
tools._wiki_fetch = lambda host, title: {"type": "standard", "extract": "흥인지문은 서울 한양도성의 동쪽 문이다. 보물이다."}
