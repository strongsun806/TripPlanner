"""로깅 서브 Agent (F3013)

메인 Agent 가 발행한 이벤트를 '구독'해서 세션 단위 실행 로그로 저장한다.

    · 수집과 저장에 LLM 을 쓰지 않는다. 요약 · 가공하지 않고 발생한 사실을 그대로 덧붙인다.
    · 받는 즉시 발생 순서대로 저장한다 (session_id · run_id · seq).
    · 저장에 실패해도 대화는 정상 동작한다 — 실패는 삼키고 횟수만 센다.
    · 메인 Agent 는 이 파일을 import 하지 않는다. main.py 가 시작할 때 버스에 붙이거나(LOGGING_ENABLED=1) 붙이지 않는다.

요청 1건이 끝나면(run_end) 평가기(심화 F3020)에 넘긴다. 평가는 별도 스레드에서 돌아 응답을 늦추지 않는다.
"""
import threading

from . import store
from .agent.events import EventBus


class LoggingAgent:
    def __init__(self, on_run_end=None):
        self.saved = 0
        self.failed = 0
        self.on_run_end = on_run_end          # 평가기 (없으면 저장만)
        self._bus: EventBus | None = None

    def handle(self, event: dict):
        try:
            store.append_event(event)
            self.saved += 1
        except Exception:
            self.failed += 1
            return
        if event["type"] == "run_end" and self.on_run_end:
            threading.Thread(target=self._safe_eval, args=(event,), daemon=True).start()

    def _safe_eval(self, event):
        try:
            self.on_run_end(event["run_id"], event["session_id"])
        except Exception:
            pass

    def attach(self, bus: EventBus):
        self._bus = bus
        bus.subscribe(self.handle)

    def detach(self):
        if self._bus:
            self._bus.unsubscribe(self.handle)
            self._bus = None
