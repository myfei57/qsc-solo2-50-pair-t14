"""待硫化任务队列。

模具登记之后进入调度队列，等空闲机位。队列按优先级排序、同优先级按入队
先后（FIFO），保证同一批制品按登记顺序投产。队列有容量上限，超出直接
拒绝入队，避免无界积压。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..errors import RubberCureError


@dataclass(frozen=True)
class QueueEntry:
    """一条待投产任务。"""

    mold_id: str
    product_code: str
    thickness_mm: float
    priority: int
    sequence: int
    enqueued_at: float

    def as_dict(self) -> dict:
        return {
            "mold_id": self.mold_id,
            "product_code": self.product_code,
            "thickness_mm": round(self.thickness_mm, 3),
            "priority": self.priority,
            "sequence": self.sequence,
            "enqueued_at": round(self.enqueued_at, 3),
        }


class DispatchQueue:
    """有界优先队列。"""

    def __init__(self, max_queue: int) -> None:
        if max_queue < 1:
            raise RubberCureError("队列容量必须为正", component="batch")
        self._max = int(max_queue)
        self._entries: list[QueueEntry] = []
        self._sequence = 0
        self._lock = threading.RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def push(
        self,
        mold_id: str,
        product_code: str,
        thickness_mm: float,
        enqueued_at: float,
        priority: int = 50,
    ) -> QueueEntry:
        """入队一条任务。"""

        with self._lock:
            if len(self._entries) >= self._max:
                raise RubberCureError(f"调度队列已满（{self._max}）", component="batch")
            self._sequence += 1
            entry = QueueEntry(
                mold_id=mold_id,
                product_code=product_code,
                thickness_mm=float(thickness_mm),
                priority=int(priority),
                sequence=self._sequence,
                enqueued_at=float(enqueued_at),
            )
            self._entries.append(entry)
            return entry

    def _ordered(self) -> list[QueueEntry]:
        return sorted(self._entries, key=lambda item: (-item.priority, item.sequence))

    def pop_next(self) -> QueueEntry | None:
        """取出优先级最高、同优先级最早入队的一条任务。"""

        with self._lock:
            if not self._entries:
                return None
            entry = self._ordered()[0]
            self._entries.remove(entry)
            return entry

    def remove(self, mold_id: str) -> bool:
        """按模具编号撤出任务。"""

        with self._lock:
            for entry in list(self._entries):
                if entry.mold_id == mold_id:
                    self._entries.remove(entry)
                    return True
        return False

    def entries(self) -> list[dict]:
        with self._lock:
            return [entry.as_dict() for entry in self._ordered()]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "size": len(self._entries),
                "capacity": self._max,
                "entries": [entry.as_dict() for entry in self._ordered()],
            }
