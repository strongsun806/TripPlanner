"""TripPlanner 백엔드 (F3017)

실행 (TripPlanner 폴더에서):
    uvicorn app.main:app --reload            →  http://localhost:8000/docs
화면 (터미널 하나 더):
    python -m http.server 5500 -d frontend   →  http://localhost:5500

지금은 계획 관리(/api/plans)만 있다. 대화(/api/chat) · 실행 로그(/api/logs)는 Agent 를 만든 뒤 붙인다.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import store
from .api import plans


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()                       # 서버가 뜰 때 테이블이 없으면 만든다
    yield


app = FastAPI(title="TripPlanner", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(plans.router)


@app.get("/", include_in_schema=False)
def home():
    return {"message": "TripPlanner API", "docs": "/docs"}
