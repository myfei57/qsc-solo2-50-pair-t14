"""蒸汽压力目标值仲裁。

同一个母管压力会被两条控制链同时写入：模温回路的升温段按模温偏差算目标，
蒸汽调节器按压力偏差算目标。两个写入方在不同线程里并发提交，必须由仲裁
器按优先级选出唯一提交值，否则母管压力会在两个目标之间来回摆动，模温跟
着抖。仲裁是并发安全的：最终提交值永远是某一个完整请求，不会是两个请求
的混合。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SetpointRequest:
    """一次压力目标值请求。"""

    writer: str
    target_kpa: float
    priority: int
    sequence: int
    issued_at: float
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "writer": self.writer,
            "target_kpa": round(self.target_kpa, 3),
            "priority": self.priority,
            "sequence": self.sequence,
            "reason": self.reason,
        }


@dataclass
class SetpointOutcome:
    """一次仲裁结果。"""

    winner: str
    target_kpa: float
    rejected: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "winner": self.winner,
            "target_kpa": round(self.target_kpa, 3),
            "rejected": list(self.rejected),
        }


class SetpointArbiter:
    """按优先级仲裁多个写入方的压力目标值。"""

    def __init__(
        self,
        min_kpa: float,
        max_kpa: float,
        default_kpa: float,
        staleness_s: float = 5.0,
    ) -> None:
        if min_kpa >= max_kpa:
            raise ValueError("压力下限必须小于上限")
        self._min = float(min_kpa)
        self._max = float(max_kpa)
        self._staleness = float(staleness_s)
        self._pending: dict[str, SetpointRequest] = {}
        self._sequence = 0
        self._decisions: list[SetpointOutcome] = []
        self._committed = self.clamp(default_kpa)
        self._lock = threading.RLock()

    def clamp(self, target_kpa: float) -> float:
        return max(self._min, min(self._max, float(target_kpa)))

    def request(self, writer: str, target_kpa: float, priority: int, reason: str = "") -> SetpointRequest:
        """提交一次压力目标值请求。"""

        with self._lock:
            self._sequence += 1
            request = SetpointRequest(
                writer=writer,
                target_kpa=self.clamp(target_kpa),
                priority=int(priority),
                sequence=self._sequence,
                issued_at=time.monotonic(),
                reason=reason,
            )
            self._pending[writer] = request
            return request

    def _live_requests(self, now: float | None = None) -> list[SetpointRequest]:
        moment = time.monotonic() if now is None else now
        return [
            request
            for request in self._pending.values()
            if moment - request.issued_at <= self._staleness
        ]

    def resolve(self) -> SetpointOutcome:
        """结算一次仲裁，把最高优先级请求提交为唯一目标值。"""

        with self._lock:
            live = self._live_requests()
            stale = [writer for writer, request in self._pending.items() if request not in live]
            for writer in stale:
                del self._pending[writer]
            if not live:
                outcome = SetpointOutcome(
                    winner="idle", target_kpa=self._committed, rejected=stale
                )
            else:
                live.sort(key=lambda item: (-item.priority, -item.sequence))
                winner = live[0]
                self._committed = winner.target_kpa
                outcome = SetpointOutcome(
                    winner=winner.writer,
                    target_kpa=winner.target_kpa,
                    rejected=[item.writer for item in live[1:]] + stale,
                )
            self._decisions.append(outcome)
            if len(self._decisions) > 256:
                del self._decisions[: len(self._decisions) - 256]
            return outcome

    def committed_kpa(self) -> float:
        with self._lock:
            return self._committed

    def decisions(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [outcome.as_dict() for outcome in self._decisions[-limit:]]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "committed_kpa": round(self._committed, 3),
                "min_kpa": self._min,
                "max_kpa": self._max,
                "pending": [request.as_dict() for request in self._pending.values()],
            }
