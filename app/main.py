"""TripPlanner 백엔드 (F3017)

실행 (TripPlanner 폴더에서):
    uvicorn app.main:app --reload            →  http://localhost:8000/docs
화면 (터미널 하나 더):
    python -m http.server 5500 -d frontend   →  http://localhost:5500

    /api/chat    대화 요청 (메인 Agent)
    /api/plans   계획 관리
    /api/logs    실행 로그 조회 · 삭제
    /api/calendar 내 캘린더 약속 (심화 F3023)
    /api/status  키 설정 여부 · 실행 설정      /api/tools  Tool 명세
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

for r in (chat.router, plans.router, logs.router, calendar.router, meta.router):
    app.include_router(r)


@app.get("/", include_in_schema=False)
def home():
    return {"message": "TripPlanner API", "docs": "/docs"}
