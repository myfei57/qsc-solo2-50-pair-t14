"""开合模相位状态机。

合模与开模是会互相打断的：合模过程中可能被迫中途排气，开模过程中可能
先要泄压。状态机只允许合法相位迁移，非法迁移直接抛错，防止"在没泄压的
情况下跳到已开模"。控制器重启后可能带来一份中间相位，此时允许按恢复
路径继续推进。
"""

from __future__ import annotations

import enum

from ..errors import SequenceError


class LiftPhase(str, enum.Enum):
    """开合模相位。"""

    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    OPENING = "opening"


class LiftStateMachine:
    """记录相位迁移，非法迁移直接拒绝。"""

    _ALLOWED = {
        LiftPhase.OPEN: (LiftPhase.CLOSING, LiftPhase.CLOSED),
        LiftPhase.CLOSING: (LiftPhase.CLOSED, LiftPhase.OPEN),
        LiftPhase.CLOSED: (LiftPhase.OPENING, LiftPhase.CLOSING),
        LiftPhase.OPENING: (LiftPhase.OPEN, LiftPhase.CLOSED),
    }

    def __init__(self) -> None:
        self._phase = LiftPhase.OPEN
        self._history: list[LiftPhase] = [LiftPhase.OPEN]

    @property
    def phase(self) -> LiftPhase:
        return self._phase

    def transition(self, target: LiftPhase, component: str = "lift") -> LiftPhase:
        if target == self._phase:
            return self._phase
        if target not in self._ALLOWED[self._phase]:
            raise SequenceError(
                f"开合模不允许从 {self._phase.value} 迁移到 {target.value}",
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
            raise SequenceError("开合模快照缺少相位", component="lift")
        try:
            restored = LiftPhase(phase)
        except ValueError as exc:
            raise SequenceError(f"未知开合模相位: {phase}", component="lift") from exc
        self._phase = restored
        self._history = [LiftPhase.OPEN, restored]
