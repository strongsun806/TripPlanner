"""서비스 상태 · Tool 명세

    GET /api/status   키 설정 여부(값은 내보내지 않는다 — NF3002), 실행 설정, 로깅 상태
    GET /api/tools    등록된 Tool 명세 (F3001)
"""
import os

from fastapi import APIRouter

from .. import config
from ..agent.registry import catalog

router = APIRouter(prefix="/api", tags=["meta"])

KEYS = {"GMS_KEY": "LLM (GMS)", "GEOAPIFY_KEY": "후보 장소 (Geoapify)",
        "VISUALCROSSING_KEY": "날씨 (Visual Crossing)", "KAKAO_REST_KEY": "주변 식당 (카카오 로컬)"}


@router.get("/status", summary="키 설정 여부와 실행 설정")
def status():
    from ..main import logging_agent
    return {
        "keys": [{"name": k, "label": v,
                  "set": bool(os.getenv(k) or (k == "GMS_KEY" and os.getenv("GMS_API_KEY")))
                         and (k != "GMS_KEY" or bool(config.GMS_BASE_URL))} for k, v in KEYS.items()],
        "settings": {"rain_threshold": config.RAIN_THRESHOLD, "walk_kmh": config.WALK_KMH, "detour": config.DETOUR,
                     "min_stay_min": config.MIN_STAY_MIN, "max_places": config.MAX_PLACES,
                     "max_replan": config.MAX_REPLAN, "default_window": config.DEFAULT_WINDOW,
                     "agent_mode": config.AGENT_MODE, "model": config.GMS_MODEL},
        "access_required": bool(config.ACCESS_CODE),
        "logging": {"enabled": logging_agent is not None,
                    "saved": logging_agent.saved if logging_agent else 0,
                    "failed": logging_agent.failed if logging_agent else 0},
    }


@router.get("/tools", summary="등록된 Tool 명세")
def tools():
    return catalog()
