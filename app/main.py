"""TripPlanner 백엔드 (F3017)

실행 (TripPlanner 폴더에서):
    uvicorn app.main:app --reload            →  http://localhost:8000/docs
화면 (터미널 하나 더):
    python serve_frontend.py                 →  http://localhost:5500
배포 (Render 등):
    서버 하나가 화면(frontend/)까지 같은 주소로 보여 준다  →  https://<서비스>/  ·  /docs
    ACCESS_CODE 를 넣으면 /api 요청은 접속 코드가 맞아야 한다 (/api/status · /api/auth 제외)

    /api/chat    대화 요청 (메인 Agent)
    /api/plans   계획 관리
    /api/logs    실행 로그 조회 · 삭제
    /api/calendar 내 캘린더 약속 (심화 F3023)
    /api/status  키 설정 여부 · 실행 설정      /api/tools  Tool 명세
"""
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, store
from .agent.events import BUS
from .api import calendar, chat, logs, meta, plans
from .logging_agent import LoggingAgent

logging_agent: LoggingAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global logging_agent
    store.init_db()                       # 서버가 뜰 때 테이블이 없으면 만든다
    if config.LOGGING_ENABLED:            # 로깅 Agent 는 버스에 '구독자'로 붙는다. 메인 Agent 는 모른다
        try:
            from .evaluator import evaluate_run
        except ImportError:
            evaluate_run = None
        logging_agent = LoggingAgent(on_run_end=evaluate_run)
        logging_agent.attach(BUS)
    yield
    if logging_agent:
        logging_agent.detach()


app = FastAPI(title="TripPlanner", description="Agent 기반 국내·해외 여행 플래너", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPEN_PATHS = {"/api/status", "/api/auth"}      # 접속 코드 없이도 열리는 곳 (화면이 코드가 필요한지 알아야 한다)


def code_ok(code: str | None) -> bool:
    return not config.ACCESS_CODE or hmac.compare_digest((code or "").encode(), config.ACCESS_CODE.encode())


@app.middleware("http")
async def access_gate(request: Request, call_next):
    """배포할 때만: 키 사용량이 나가는 /api 요청은 접속 코드가 맞아야 한다"""
    path = request.url.path
    if (config.ACCESS_CODE and path.startswith("/api/") and path not in OPEN_PATHS
            and request.method != "OPTIONS" and not code_ok(request.headers.get("x-access-code"))):
        return JSONResponse({"detail": "ACCESS_CODE_REQUIRED"}, status_code=401)
    return await call_next(request)


class AuthIn(BaseModel):
    code: str


@app.post("/api/auth", tags=["meta"], summary="접속 코드 확인")
def auth(body: AuthIn):
    if not code_ok(body.code):
        return JSONResponse({"ok": False, "detail": "접속 코드가 맞지 않아요."}, status_code=401)
    return {"ok": True}


for r in (chat.router, plans.router, logs.router, calendar.router, meta.router):
    app.include_router(r)

# 화면도 같은 주소에서 — 배포하면 서버 하나로 끝난다 (API 경로가 먼저 잡히고, 나머지는 frontend/ 파일)
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
else:
    @app.get("/", include_in_schema=False)
    def home():
        return {"message": "TripPlanner API", "docs": "/docs"}
