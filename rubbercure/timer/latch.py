"""硫化完成闩锁。

计时完成后要对"这一模"置位闩锁：解锁开模、通知调度下一模就位。闩锁必须
按模独立——A 模的完成闩锁不能顺手把 B 模也放行，否则机群调度会误判哪台
机可以开模。复位只在对应模次完成取件之后发生。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class LatchEntry:
    """一条按模的闩锁记录。"""

    mold_id: str
    set_at: float
    generation: int
    released: bool = False

    def as_dict(self) -> dict:
        return {
            "mold_id": self.mold_id,
            "set_at": round(self.set_at, 3),
            "generation": self.generation,
            "released": self.released,
        }


class CureLatch:
    """按模独立的硫化完成闩锁组。"""

    def __init__(self) -> None:
        self._entries: dict[str, LatchEntry] = {}
        self._generation = 0
        self._lock = threading.RLock()

    def set(self, mold_id: str, set_at: float) -> LatchEntry:
        """为指定模具置位闩锁。"""

        with self._lock:
            self._generation += 1
            entry = LatchEntry(
                mold_id=mold_id, set_at=float(set_at), generation=self._generation
            )
            self._entries[mold_id] = entry
            return entry

    def reset(self, mold_id: str, reset_at: float) -> LatchEntry | None:
        """复位指定模具的闩锁，返回复位后的记录。"""

        with self._lock:
            entry = self._entries.get(mold_id)
            if entry is None:
                return None
            released = LatchEntry(
                mold_id=entry.mold_id,
                set_at=float(reset_at),
                generation=entry.generation,
                released=True,
            )
            self._entries[mold_id] = released
            return released

    def is_set(self, mold_id: str) -> bool:
        """判断指定模具是否处于已置位且未复位的状态。"""

        with self._lock:
            entry = self._entries.get(mold_id)
        return entry is not None and not entry.released

    def set_molds(self) -> list[str]:
        """返回当前所有已置位未复位的模具。"""

        with self._lock:
            return sorted(
                mold_id for mold_id, entry in self._entries.items() if not entry.released
            )

    def release_all(self) -> int:
        """复位全部闩锁，返回复位数量。"""

        with self._lock:
            released = [mold_id for mold_id, entry in self._entries.items() if not entry.released]
            self._entries.clear()
        return len(released)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "generation": self._generation,
                "entries": {mold_id: entry.as_dict() for mold_id, entry in sorted(self._entries.items())},
                "set_molds": self.set_molds(),
            }
