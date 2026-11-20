"""실행 설정 — 판단 기준값 · 상한 · LLM 접속 정보를 한 곳에 모은다 (명세서 1.2 '실행 설정')

값을 바꿀 때는 여기만 고친다. 문서(01_도메인_확정.md · 03_Agent_설계.md)의 기준값과 같아야 한다.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ── 제약 판단 기준 (F3008) ───────────────────────────────────
RAIN_THRESHOLD = 50          # 강수확률(%)이 이 값을 넘으면 야외 일정은 '비 예보'로 보고 제외한다
WALK_KMH = 4.0               # 도보 속도
DETOUR = 1.3                 # 직선거리 → 실제 걷는 거리 보정 (도로는 직선이 아니다)
MIN_STAY_MIN = 40            # 한 장소에 최소 이만큼은 머물 수 있어야 일정에 넣는다
MAX_PLACES = 3               # 하루 일정에 넣는 장소 수 상한
MAX_LEG_MIN = 60             # 장소 사이 도보가 이보다 길면 그 장소는 동선에서 뺀다
DEFAULT_WINDOW = ("10:00", "18:00")   # 시간을 말하지 않았을 때 쓰는 기본 시간창 (응답에 밝힌다)

# ── 재계획 (F3009) ───────────────────────────────────────────
MAX_REPLAN = 3               # 재계획 상한. 도달하면 멈추고 조건 조정을 되묻는다
RADIUS_STEPS = [5000, 10000, 20000]   # 후보가 없을 때 넓혀 가는 검색 반경(m)
NEAR_RADIUS = 2000           # 동선이 너무 길 때 좁히는 반경(m)

# ── 실행 ────────────────────────────────────────────────────
CANDIDATE_LIMIT = 10         # 후보 Tool 에 요청할 개수
FOOD_RADIUS_M = 700
LOGGING_ENABLED = os.getenv("LOGGING_ENABLED", "1") != "0"   # 0 이면 로깅 Agent 를 붙이지 않는다 (분리 확인용)
AGENT_MODE = os.getenv("AGENT_MODE", "single")               # single | planner_worker (심화 F3021)
# 배포용 접속 코드. 비워 두면(로컬 기본) 누구나 쓰고, 넣으면 /api 요청에 X-Access-Code 헤더가 맞아야 한다
ACCESS_CODE = os.getenv("ACCESS_CODE", "").strip()

# ── LLM (GMS) ───────────────────────────────────────────────
GMS_KEY = os.getenv("GMS_KEY") or os.getenv("GMS_API_KEY")     # 2일차 라이브는 GMS_API_KEY — 둘 다 읽는다
# GPT 토큰이 다 떨어져 GMS 의 Claude 로 바꿨다 (수업 공지). 모델명 · 주소 · 헤더(x-api-key) 세 가지가 바뀐다
# GMS 주소는 코드에 적지 않고 .env (배포는 환경 변수) 에서만 읽는다. 비어 있으면 LLM 을 부르지 않는다
GMS_BASE_URL = (os.getenv("GMS_BASE_URL") or "").strip().rstrip("/")
GMS_MODEL = os.getenv("GMS_MODEL", "claude-haiku-5-5")
# 임베딩(문서 검색 F3022)은 Claude 에 없어서 OpenAI 쪽 주소를 따로 둔다. 실패하면 해시 벡터로 대체된다
# 따로 주지 않으면 같은 게이트웨이의 OpenAI 쪽 주소로 바꿔 쓴다
GMS_EMBED_BASE_URL = (os.getenv("GMS_EMBED_BASE_URL") or GMS_BASE_URL.replace("api.anthropic.com", "api.openai.com")).rstrip("/")


def gms_headers() -> dict:
    """Claude(anthropic) 주소면 x-api-key 헤더를 함께 보낸다 — 모든 LLM 호출이 이 함수를 거친다"""
    return {"x-api-key": GMS_KEY} if GMS_KEY and "anthropic" in GMS_BASE_URL else {}
LLM_TIMEOUT = 40
LLM_RETRIES = int(os.getenv("LLM_RETRIES", "3"))   # 429(요청 한도) · 일시 오류 때 기다렸다 다시 (지수 백오프)

# ── Planner / Worker (심화 F3021) ─────────────────────────────
WORKER_LLM = os.getenv("WORKER_LLM", "1") != "0"   # 0 이면 Worker 는 LLM 없이 실패를 그대로 보고한다 (비교 실험용)
WORKER_RETRY = 2                                   # Worker 가 같은 Tool 을 다시 부르는 상한
