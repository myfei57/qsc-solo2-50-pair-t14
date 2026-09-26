"""排产推进。

从队列取模次、挑空闲机位、登记分配，是调度器的核心一步。排产只在派工
相位执行；队列空且无机位可派时进入排空相位。整个动作在调度器锁内完成，
避免两个线程同时把同一台机分配两次。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..eventbus import EventBus
from ..mold.registry import MoldRegistry
from .assign import Assignment, assign_next
from .queue import DispatchQueue
from .state import SchedulerPhase

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from ..press.fleet import PressFleet
    from .batch import BatchScheduler


def schedule_next(
    scheduler: "BatchScheduler",
    fleet: "PressFleet",
    queue: DispatchQueue,
    registry: MoldRegistry,
    bus: EventBus,
    assigned_at: float,
) -> Assignment | None:
    """推进一次排产，返回分配结果或 None。"""

    with scheduler.lock:
        scheduler.transition(SchedulerPhase.DISPATCHING)
        assignment = assign_next(fleet, queue, registry, bus, assigned_at)
        if assignment is None and len(queue) == 0:
            scheduler.transition(SchedulerPhase.DRAINED)
        return assignment
