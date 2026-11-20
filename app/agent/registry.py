"""Agent 가 쓰는 Tool 목록 (F3001)

    외부 API 툴   tools.py              search_places · get_weather · search_nearby_food
    로컬 서버 API 툴 app/agent/local_tools.py   이 서비스가 가진 기능을 감싼 Tool (심화)

둘을 합쳐 이름으로 찾는다. catalog() 은 Tool 명세(이름 · 설명 · 입력 규격 · 구분)만 돌려준다.
"""
import tools as external
from tools import geocode  # noqa: F401  — 흐름이 직접 쓰는 도우미 (Tool 아님)

TOOLS = dict(external.TOOLS)
KIND = {name: "external" for name in TOOLS}

try:
    from . import local_tools
    for t in local_tools.TOOLS.values():
        TOOLS[t.name] = t
        KIND[t.name] = "local"
except ImportError:
    pass


def catalog():
    return [{"name": t.name, "kind": KIND[t.name], "description": t.description, "parameters": t.parameters}
            for t in TOOLS.values()]
