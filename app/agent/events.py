"""실행 이벤트 발행 (F3012)

메인 Agent 는 EventBus 에 이벤트를 '발행'만 한다. 누가 구독하는지(로깅 Agent 가 붙어 있는지) 모른다.
구독자가 하나도 없어도, 구독자가 오류를 내도 메인 Agent 는 그대로 돈다.

이벤트 한 건의 모양
    {"session_id", "run_id", "seq", "ts", "type", "node", "data"}

type
    run_start · run_end            요청 1건의 시작 · 끝
    node_start · node_end          단계(analyze · plan · execute · judge · respond) 진입 · 종료
    decision                       흐름을 바꾸는 판단과 그 이유 (의도 분류, 계획, 재계획 여부 …)
    tool_call · tool_result        Tool 호출 요청과 응답, 소요 시간(ms)
    tool_cache_hit                 이미 받은 결과를 다시 쓴 경우 (재계획 시 재사용 — F3009)
    error                          단계 안에서 난 오류
"""
import threading
import time
import uuid
from datetime import datetime
from typing import Callable

Subscriber = Callable[[dict], None]


class EventBus:
    def __init__(self):
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber):
        with self._lock:
            self._subscribers.append(fn)

    def unsubscribe(self, fn: Subscriber):
        with self._lock:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

    def publish(self, event: dict):
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:          # 구독자의 실패가 발행자(메인 Agent)를 멈추게 하지 않는다
                pass


BUS = EventBus()


class RunEmitter:
    """요청 1건(run) 동안 쓰는 발행 도우미. 순서 번호(seq)를 붙이고, 메인 Agent 가 화면에 돌려줄
    실행 단계 목록(steps)도 스스로 모은다 — 로깅 Agent 에 기대지 않기 위해서다."""

    def __init__(self, session_id: str, bus: EventBus = BUS):
        self.session_id = session_id
        self.run_id = uuid.uuid4().hex[:12]
        self.bus = bus
        self.seq = 0
        self.steps: list[dict] = []

    def emit(self, type_: str, node: str | None = None, **data):
        from tools import scrub                         # 오류 문구에 키가 섞여 로그 · 화면으로 나가지 않게
        for k in ("message", "brief", "error"):
            if isinstance(data.get(k), str):
                data[k] = scrub(data[k])
        self.seq += 1
        event = {"session_id": self.session_id, "run_id": self.run_id, "seq": self.seq,
                 "ts": datetime.now().isoformat(timespec="milliseconds"),
                 "type": type_, "node": node, "data": data}
        self.steps.append(event)
        self.bus.publish(event)
        return event


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self.t0) * 1000)
