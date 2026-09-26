"""硫化机群。

车间按多台硫化机组织生产，每台机有自己的回路与落盘目录（互不覆盖），但
共用同一套审计总线与模具台账。机群对外提供按编号取机、列出空闲机与整体
快照三件事，调度据此挑机、控制台据此渲染机群。
"""

from __future__ import annotations

import os

from ..clock import Clock
from ..config import PlatformConfig
from ..errors import RubberCureError
from ..eventbus import EventBus
from ..store.jsonfile import JsonStore
from .press import CuringPress
from .transitions import PressPhase


class PressFleet:
    """一个车间的硫化机群。"""

    def __init__(self, presses: list[CuringPress]) -> None:
        if not presses:
            raise RubberCureError("机群至少需要一台硫化机", component="press")
        self._presses = {press.press_id: press for press in presses}
        if len(self._presses) != len(presses):
            raise RubberCureError("机群中存在重复的硫化机编号", component="press")

    @classmethod
    def from_config(
        cls,
        clock: Clock,
        bus: EventBus,
        storage_root: str,
        config: PlatformConfig,
    ) -> "PressFleet":
        """按配置规模建立机群，每台机一个独立落盘目录。"""

        presses: list[CuringPress] = []
        for index in range(1, config.press.fleet_size + 1):
            press_id = f"press-{index:02d}"
            store = JsonStore(os.path.join(storage_root, press_id))
            presses.append(CuringPress(press_id, clock, bus, store, config))
        return cls(presses)

    @property
    def size(self) -> int:
        return len(self._presses)

    def press_ids(self) -> list[str]:
        return sorted(self._presses)

    def press(self, press_id: str) -> CuringPress:
        """按编号取硫化机。"""

        unit = self._presses.get(press_id)
        if unit is None:
            raise RubberCureError(f"硫化机 {press_id} 不存在", component="press")
        return unit

    def all(self) -> list[CuringPress]:
        return [self._presses[press_id] for press_id in self.press_ids()]

    def idle_presses(self) -> list[CuringPress]:
        """返回处于待机或已取件相位的硫化机。"""

        return [
            unit
            for unit in self.all()
            if unit.phase in (PressPhase.IDLE, PressPhase.DONE)
        ]

    def snapshot(self) -> dict:
        return {unit.press_id: unit.snapshot() for unit in self.all()}
