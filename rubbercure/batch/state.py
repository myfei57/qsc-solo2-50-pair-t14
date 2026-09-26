"""调度相位状态机。

调度器在待机、派工、排空三个相位之间流转：待机等待任务，派工把任务分配给
空闲机位，排空在队列清空后收尾。非法迁移直接拒绝，避免在排空相位继续派工。
"""

from __future__ import annotations

import enum

from ..errors import SequenceError


class SchedulerPhase(str, enum.Enum):
    """调度器相位。"""

    IDLE = "idle"
    DISPATCHING = "dispatching"
    DRAINED = "drained"


class SchedulerStateMachine:
    """记录相位迁移，非法迁移直接拒绝。"""

    _ALLOWED = {
        SchedulerPhase.IDLE: (SchedulerPhase.DISPATCHING,),
        SchedulerPhase.DISPATCHING: (SchedulerPhase.IDLE, SchedulerPhase.DRAINED),
        SchedulerPhase.DRAINED: (SchedulerPhase.IDLE,),
    }

    def __init__(self) -> None:
        self._phase = SchedulerPhase.IDLE
        self._history: list[SchedulerPhase] = [SchedulerPhase.IDLE]

    @property
    def phase(self) -> SchedulerPhase:
        return self._phase

    def transition(self, target: SchedulerPhase, component: str = "batch") -> SchedulerPhase:
        if target == self._phase:
            return self._phase
        if target not in self._ALLOWED[self._phase]:
            raise SequenceError(
                f"调度器不允许从 {self._phase.value} 迁移到 {target.value}",
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
            raise SequenceError("调度器快照缺少相位", component="batch")
        try:
            restored = SchedulerPhase(phase)
        except ValueError as exc:
            raise SequenceError(f"未知调度器相位: {phase}", component="batch") from exc
        self._phase = restored
        self._history = [SchedulerPhase.IDLE, restored]
