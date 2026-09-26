"""硫化计时单元。

把计时相位、已执行时长、计划时长、曲线代际和模温采样装成一台硫化机的
计时回路。启动、推进、结束三个动作分别由独立函数实现，计时单元只保存
状态与提供只读估算，控制台与调度据此判断进度与剩余时间。
"""

from __future__ import annotations

import threading

from ..config import TimerLimits
from ..errors import CureError
from ..eventbus import Event, EventBus
from ..store.jsonfile import JsonStore
from .estimate import estimate_snapshot
from .state import TimerPhase, TimerStateMachine

STATE_KEY = "cure_timer"


class CureTimer:
    """一台硫化机的硫化计时回路。"""

    def __init__(self, bus: EventBus, store: JsonStore, limits: TimerLimits) -> None:
        self.limits = limits
        self._bus = bus
        self._store = store
        self._state = TimerStateMachine()
        self._mold_id: str | None = None
        self._product_code = ""
        self._generation = 0
        self._planned_s = 0.0
        self._elapsed_s = 0.0
        self._samples: list[float] = []
        self._clock_now = 0.0
        self._lock = threading.RLock()

    @property
    def phase(self) -> TimerPhase:
        return self._state.phase

    @property
    def mold_id(self) -> str | None:
        with self._lock:
            return self._mold_id

    @property
    def planned_s(self) -> float:
        with self._lock:
            return self._planned_s

    @property
    def elapsed_s(self) -> float:
        with self._lock:
            return self._elapsed_s

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def set_clock_now(self, moment: float) -> None:
        with self._lock:
            self._clock_now = float(moment)

    def clock_now(self) -> float:
        with self._lock:
            return self._clock_now

    def begin(
        self,
        mold_id: str,
        product_code: str,
        planned_s: float,
        generation: int,
    ) -> dict:
        """进入计时相位并锁定计划时长与曲线代际。"""

        if planned_s <= 0:
            raise ValueError("计划硫化时长必须为正")
        with self._lock:
            self._state.transition(TimerPhase.CURING)
            self._mold_id = mold_id
            self._product_code = product_code
            self._generation = int(generation)
            self._planned_s = float(planned_s)
            self._elapsed_s = 0.0
            self._samples = []
        return {
            "mold_id": mold_id,
            "product_code": product_code,
            "planned_s": round(planned_s, 3),
            "curve_generation": generation,
            "phase": self._state.phase.value,
        }

    def accumulate(self, dt: float) -> float:
        """累计已执行时长。"""

        if dt < 0:
            raise ValueError("计时步长不能为负")
        with self._lock:
            if self._state.phase is not TimerPhase.CURING:
                raise CureError("非计时相位不能累计时长", component="timer")
            self._elapsed_s += float(dt)
            return self._elapsed_s

    def note_sample(self, measured_c: float, at: float) -> None:
        """记下本周期模温采样与时刻。"""

        with self._lock:
            self._samples.append(float(measured_c))
            self._clock_now = float(at)

    def estimate(self) -> dict:
        with self._lock:
            planned = self._planned_s
            elapsed = self._elapsed_s
        if planned <= 0:
            return {"elapsed_s": 0.0, "planned_s": 0.0, "remaining_s": 0.0, "progress": 0.0}
        return estimate_snapshot(elapsed, planned)

    def window_stats(self) -> dict:
        """返回计时窗口内的模温统计。"""

        with self._lock:
            samples = list(self._samples)
        if not samples:
            return {"count": 0, "min_c": 0.0, "max_c": 0.0, "average_c": 0.0}
        return {
            "count": len(samples),
            "min_c": round(min(samples), 3),
            "max_c": round(max(samples), 3),
            "average_c": round(sum(samples) / len(samples), 3),
        }

    def finish(self) -> dict:
        """结束计时并返回本次结果。"""

        with self._lock:
            self._state.transition(TimerPhase.COMPLETE)
            planned = self._planned_s
            elapsed = self._elapsed_s
            mold_id = self._mold_id
            generation = self._generation
        return {
            "mold_id": mold_id,
            "elapsed_s": round(elapsed, 3),
            "planned_s": round(planned, 3),
            "curve_generation": generation,
        }

    def reset(self, mold_id: str) -> dict:
        """按模复位计时，回到空闲等待下一模。"""

        with self._lock:
            if self._mold_id is not None and self._mold_id != mold_id:
                raise CureError(
                    f"复位对象 {mold_id} 与当前模次 {self._mold_id} 不一致", component="timer"
                )
            self._state.transition(TimerPhase.RESET)
            self._state.transition(TimerPhase.IDLE)
            self._mold_id = None
            self._planned_s = 0.0
            self._elapsed_s = 0.0
            self._samples = []
        self._bus.publish(
            Event.of("timer", "timer", "cure_reset", subject=mold_id, severity="info")
        )
        return {"mold_id": mold_id, "phase": self._state.phase.value}

    def persist_state(self) -> str:
        """把计时相位与进度落盘，返回内容摘要。"""

        with self._lock:
            snapshot = self._state.snapshot()
            snapshot["mold_id"] = self._mold_id
            snapshot["planned_s"] = round(self._planned_s, 4)
            snapshot["elapsed_s"] = round(self._elapsed_s, 4)
        digest = self._store.write(STATE_KEY, snapshot)
        return digest

    def restore(self) -> bool:
        """从磁盘恢复计时状态。"""

        snapshot = self._store.read(STATE_KEY)
        if not snapshot:
            return False
        self._state.restore(snapshot)
        with self._lock:
            mold_id = snapshot.get("mold_id")
            self._mold_id = str(mold_id) if mold_id else None
            planned = snapshot.get("planned_s")
            elapsed = snapshot.get("elapsed_s")
            self._planned_s = float(planned) if isinstance(planned, (int, float)) else 0.0
            self._elapsed_s = float(elapsed) if isinstance(elapsed, (int, float)) else 0.0
        return True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "phase": self._state.phase.value,
                "history": self._state.history(),
                "mold_id": self._mold_id,
                "product_code": self._product_code,
                "curve_generation": self._generation,
                "planned_s": round(self._planned_s, 3),
                "elapsed_s": round(self._elapsed_s, 3),
                "remaining_s": self.estimate()["remaining_s"],
                "progress": self.estimate()["progress"],
                "window": self.window_stats(),
            }
