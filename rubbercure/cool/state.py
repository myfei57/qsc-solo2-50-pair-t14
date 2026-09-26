"""冷却回路相位状态机。

冷却回路会经历待机、循环、停泵、排空几个相位。停运顺序要求蒸汽先停、
冷却后停：带着蒸汽停冷却会让模具局部骤冷、产生内应力。非法迁移直接拒绝。
"""

from __future__ import annotations

import enum

from ..errors import SequenceError


class CoolPhase(str, enum.Enum):
    """冷却回路相位。"""

    IDLE = "idle"
    CIRCULATING = "circulating"
    DRAINING = "draining"
    STOPPED = "stopped"


class CoolStateMachine:
    """记录相位迁移，非法迁移直接拒绝。"""

    _ALLOWED = {
        CoolPhase.IDLE: (CoolPhase.CIRCULATING,),
        CoolPhase.CIRCULATING: (CoolPhase.DRAINING, CoolPhase.IDLE),
        CoolPhase.DRAINING: (CoolPhase.STOPPED, CoolPhase.CIRCULATING),
        CoolPhase.STOPPED: (CoolPhase.IDLE,),
    }

    def __init__(self) -> None:
        self._phase = CoolPhase.IDLE
        self._history: list[CoolPhase] = [CoolPhase.IDLE]

    @property
    def phase(self) -> CoolPhase:
        return self._phase

    def transition(self, target: CoolPhase, component: str = "cool") -> CoolPhase:
        if target == self._phase:
            return self._phase
        if target not in self._ALLOWED[self._phase]:
            raise SequenceError(
                f"冷却回路不允许从 {self._phase.value} 迁移到 {target.value}",
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
            raise SequenceError("冷却快照缺少相位", component="cool")
        try:
            restored = CoolPhase(phase)
        except ValueError as exc:
            raise SequenceError(f"未知冷却相位: {phase}", component="cool") from exc
        self._phase = restored
        self._history = [CoolPhase.IDLE, restored]
