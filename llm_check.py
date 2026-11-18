"""기획 검증 2.7 '도구 연결 확인' — LLM(GMS)을 한 번 부르고, Tool 명세를 주면 Tool 을 고르는지 본다

준비 (.env):
    GMS_KEY=발급받은_키
    GMS_BASE_URL=...     (선택) 기본값이 안 맞으면 GMS 사이트에 안내된 주소로 바꾼다
    GMS_MODEL=...        (선택) 기본 gpt-4.1-mini

실행:  python llm_check.py
    ① 평문 호출       → 답이 한 줄 나오면 키 · 주소 · 환경변수 로딩 OK
    ② Tool 선택 확인   → 요청마다 LLM 이 고른 Tool 이름과 인자가 찍힌다
       여기서는 Tool 을 '고르는지'만 본다. 실제 실행 · 결과 통합은 Agent 단계에서 한다.
"""
import json
import os

import requests
from dotenv import load_dotenv

from tools import catalog

load_dotenv()
KEY = os.getenv("GMS_KEY")
BASE_URL = os.getenv("GMS_BASE_URL", "https://gms.ssafy.io/gmsapi/api.openai.com/v1").rstrip("/")
MODEL = os.getenv("GMS_MODEL", "gpt-4.1-mini")


def chat(messages, tools=None):
    body = {"model": MODEL, "messages": messages}
    if tools:
        body["tools"] = tools
    res = requests.post(f"{BASE_URL}/chat/completions",
                        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                        json=body, timeout=60)
    if res.status_code >= 400:                     # 키는 찍지 않고 상태와 본문 앞부분만
        raise RuntimeError(f"HTTP {res.status_code}: {res.text[:200]}")
    return res.json()["choices"][0]["message"]


# 우리 Tool 명세(이름 · 설명 · 입력 규격)를 OpenAI tools 형식으로 감싼다
TOOL_SPECS = [{"type": "function", "function": c} for c in catalog()]

REQUESTS = [
    "다음 주 토요일 오사카에서 오후에 야외 위주로 돌고 싶어. 비 오면 곤란해",        # → search_places
    "위도 37.5729, 경도 126.9794 근처 맛집 알려줘",                                  # → search_nearby_food
    "안녕! 너는 뭘 할 수 있어?",                                                     # → Tool 없음
]

if __name__ == "__main__":
    if not KEY:
        raise SystemExit(".env 에 GMS_KEY 가 없습니다.")
    print(f"주소 {BASE_URL} · 모델 {MODEL}\n")

    print("① 평문 호출")
    try:
        msg = chat([{"role": "user", "content": "한 문장으로 자기소개 해줘"}])
        print("   OK →", msg["content"])
    except Exception as e:
        raise SystemExit(f"   FAIL → {e}\n   주소(GMS_BASE_URL) · 모델(GMS_MODEL) · 키를 GMS 사이트 안내와 대조하세요.")

    print("\n② Tool 선택 확인")
    for text in REQUESTS:
        msg = chat([{"role": "system", "content": f"너는 여행 플래너다. 오늘은 2026-11-18이다. 필요하면 Tool을 골라라."},
                    {"role": "user", "content": text}], tools=TOOL_SPECS)
        calls = msg.get("tool_calls") or []
        print(f"   요청: {text}")
        if calls:
            for c in calls:
                print(f"     → {c['function']['name']}({json.dumps(json.loads(c['function']['arguments']), ensure_ascii=False)})")
        else:
            print(f"     → Tool 없음: {(msg.get('content') or '')[:80]}")
