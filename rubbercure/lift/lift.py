"""开合模本体。

合模与开模是两条方向相反的动作，但它们共享同一套行程状态。合模到位后
锁模力必须达标，硫化计时才有意义；开模前必须确认蒸汽已泄压、模具内无
残余压力，否则制品会被顶裂。行程推进按固定步长分段，验证环境里可以逐段
观察中间位置。
"""

from __future__ import annotations

import threading

from ..errors import InterlockError, SequenceError
from ..eventbus import Event, EventBus
from .position import CLOSED_PCT, FULLY_OPEN_PCT, ClampPosition
from .state import LiftPhase, LiftStateMachine

TRAVEL_STEP_PCT = 25.0
MIN_CLAMP_FORCE_KN = 180.0
MAX_CLAMP_FORCE_KN = 1450.0


class LiftUnit:
    """硫化机开合模本体：行程、锁模力与模具承载。"""

    def __init__(self, bus: EventBus, step_pct: float = TRAVEL_STEP_PCT) -> None:
        self._bus = bus
        self._step = float(step_pct)
        if self._step <= 0:
            raise ValueError("行程步长必须为正")
        self._state = LiftStateMachine()
        self._position = ClampPosition(FULLY_OPEN_PCT)
        self._carried_mold: str | None = None
        self._lock = threading.RLock()

    @property
    def opening_pct(self) -> float:
        return self._position.opening_pct

    @property
    def phase(self) -> LiftPhase:
        return self._state.phase

    @property
    def carried_mold(self) -> str | None:
        return self._carried_mold

    def clamp_force_kn(self) -> float:
        """按当前行程折算锁模力：越接近全闭，锁模力越大。"""

        closure = 1.0 - self._position.opening_pct / FULLY_OPEN_PCT
        return MIN_CLAMP_FORCE_KN + (MAX_CLAMP_FORCE_KN - MIN_CLAMP_FORCE_KN) * closure

    def is_closed(self) -> bool:
        return self._position.is_closed()

    def is_open(self) -> bool:
        return self._position.is_open()

    def require_closed(self, component: str = "lift") -> None:
        """要求模具已合模到位。"""

        if not self.is_closed():
            raise InterlockError(
                f"合模未到位（当前 {self.opening_pct:.1f}%），禁止下游动作",
                component=component,
            )

    def _drive(self, target_pct: float) -> float:
        """按步长把行程推到目标位置，返回终点行程。"""

        while abs(self._position.opening_pct - target_pct) > 1e-9:
            self._position = self._position.approach(target_pct, self._step)
        return self._position.opening_pct

    def close_clamp(self, mold_id: str) -> float:
        """合模到锁模位。"""

        with self._lock:
            self._state.transition(LiftPhase.CLOSING)
            self._bus.publish(
                Event.of("lift", "lift", "close_start", mold_id=mold_id, opening_pct=self.opening_pct)
            )
            self._carried_mold = mold_id
            opening = self._drive(CLOSED_PCT)
            self._state.transition(LiftPhase.CLOSED)
        self._bus.publish(
            Event.of(
                "lift",
                "lift",
                "close_done",
                mold_id=mold_id,
                opening_pct=opening,
                clamp_force_kn=round(self.clamp_force_kn(), 2),
            )
        )
        return opening

    def open_clamp(self, mold_id: str) -> float:
        """开模到取件位。"""

        with self._lock:
            if self._carried_mold is not None and self._carried_mold != mold_id:
                raise SequenceError(
                    f"开模对象 {mold_id} 与在机模具 {self._carried_mold} 不一致",
                    component="lift",
                )
            self._state.transition(LiftPhase.OPENING)
            self._bus.publish(
                Event.of("lift", "lift", "open_start", mold_id=mold_id, opening_pct=self.opening_pct)
            )
            opening = self._drive(FULLY_OPEN_PCT)
            self._state.transition(LiftPhase.OPEN)
            self._carried_mold = None
        self._bus.publish(
            Event.of("lift", "lift", "open_done", mold_id=mold_id, opening_pct=opening)
        )
        return opening

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "phase": self._state.phase.value,
                "history": self._state.history(),
                "position": self._position.as_dict(),
                "clamp_force_kn": round(self.clamp_force_kn(), 2),
                "carried_mold": self._carried_mold,
            }
