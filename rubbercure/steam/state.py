"""蒸汽相位状态机。

一台硫化机在一次循环里会依次经历排气、升温、保温、泄压几个相位。顺序
约束都落在相位迁移上：没有排气不允许升温，没有泄压不允许回到空闲并开模。
非法迁移直接拒绝，避免"带着压力直接跳回空闲"。
"""

from __future__ import annotations

import enum

from ..errors import SequenceError


class SteamPhase(str, enum.Enum):
    """蒸汽回路相位。"""

    IDLE = "idle"
    PURGING = "purging"
    HEATING = "heating"
    HOLDING = "holding"
    VENTING = "venting"


class SteamStateMachine:
    """记录相位迁移，非法迁移直接拒绝。"""

    _ALLOWED = {
        SteamPhase.IDLE: (SteamPhase.PURGING, SteamPhase.VENTING),
        SteamPhase.PURGING: (SteamPhase.HEATING, SteamPhase.VENTING, SteamPhase.IDLE),
        SteamPhase.HEATING: (SteamPhase.HOLDING, SteamPhase.VENTING),
        SteamPhase.HOLDING: (SteamPhase.HEATING, SteamPhase.VENTING),
        SteamPhase.VENTING: (SteamPhase.IDLE, SteamPhase.PURGING),
    }

    def __init__(self) -> None:
        self._phase = SteamPhase.IDLE
        self._history: list[SteamPhase] = [SteamPhase.IDLE]

    @property
    def phase(self) -> SteamPhase:
        return self._phase

    def transition(self, target: SteamPhase, component: str = "steam") -> SteamPhase:
        if target == self._phase:
            return self._phase
        if target not in self._ALLOWED[self._phase]:
            raise SequenceError(
                f"蒸汽回路不允许从 {self._phase.value} 迁移到 {target.value}",
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
            raise SequenceError("蒸汽快照缺少相位", component="steam")
        try:
            restored = SteamPhase(phase)
        except ValueError as exc:
            raise SequenceError(f"未知蒸汽相位: {phase}", component="steam") from exc
        self._phase = restored
        self._history = [SteamPhase.IDLE, restored]
