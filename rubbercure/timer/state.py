"""硫化计时相位状态机。

计时只在模温确认落盘之后启动，完成后进入 COMPLETE 并触发闩锁；按模复位
之后回到 IDLE，等待下一个模次的模具就位。非法迁移（例如从 IDLE 直接跳到
COMPLETE）直接拒绝，避免计时被"手动置为完成"。
"""

from __future__ import annotations

import enum

from ..errors import CureError


class TimerPhase(str, enum.Enum):
    """硫化计时相位。"""

    IDLE = "idle"
    CURING = "curing"
    COMPLETE = "complete"
    RESET = "reset"


class TimerStateMachine:
    """记录相位迁移，非法迁移直接拒绝。"""

    _ALLOWED = {
        TimerPhase.IDLE: (TimerPhase.CURING,),
        TimerPhase.CURING: (TimerPhase.COMPLETE, TimerPhase.IDLE),
        TimerPhase.COMPLETE: (TimerPhase.RESET,),
        TimerPhase.RESET: (TimerPhase.IDLE,),
    }

    def __init__(self) -> None:
        self._phase = TimerPhase.IDLE
        self._history: list[TimerPhase] = [TimerPhase.IDLE]

    @property
    def phase(self) -> TimerPhase:
        return self._phase

    def transition(self, target: TimerPhase, component: str = "timer") -> TimerPhase:
        if target == self._phase:
            return self._phase
        if target not in self._ALLOWED[self._phase]:
            raise CureError(
                f"硫化计时不允许从 {self._phase.value} 迁移到 {target.value}",
                component=component,
            )
        self._phase = target
        self._history.append(target)
        return self._phase

    def history(self) -> list[str]:
        return [phase.value for phase in self._history]

    def snapshot(self) -> dict:
        return {"phase": self._phase.value, "history": self.history()}

    def restore(self, snapshot: dict) -> None:
        """从落盘快照恢复相位。"""

        phase = snapshot.get("phase")
        if not isinstance(phase, str):
            raise CureError("硫化计时快照缺少相位", component="timer")
        try:
            restored = TimerPhase(phase)
        except ValueError as exc:
            raise CureError(f"未知硫化计时相位: {phase}", component="timer") from exc
        self._phase = restored
        self._history = [TimerPhase.IDLE, restored]
