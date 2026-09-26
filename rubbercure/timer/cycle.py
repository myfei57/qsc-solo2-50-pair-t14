"""硫化计时推进。

每个控制周期把计时推进一个 ``dt``，同时记下这一周期的模温采样。计时本身
只累计时长；是否欠硫、是否过硫由结束时的进度校验判定。推进过程中不改变
曲线基线，基线在启动时已经锁定，避免中途换配方导致计时口径漂移。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..eventbus import Event, EventBus

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免环
    from .timer import CureTimer


def advance_cure(
    timer: "CureTimer",
    dt: float,
    bus: EventBus,
    *,
    measured_c: float | None = None,
) -> dict:
    """推进一个计时周期。"""

    elapsed = timer.accumulate(dt)
    if measured_c is not None:
        timer.note_sample(float(measured_c), timer.clock_now())
    estimate = timer.estimate()
    bus.publish(
        Event.of(
            "timer",
            "timer",
            "cure_progress",
            subject=timer.mold_id or "idle",
            elapsed_s=estimate["elapsed_s"],
            progress=estimate["progress"],
        )
    )
    return {"elapsed_s": estimate["elapsed_s"], "remaining_s": estimate["remaining_s"], "progress": estimate["progress"]}
