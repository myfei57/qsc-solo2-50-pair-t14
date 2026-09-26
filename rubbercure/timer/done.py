"""硫化完成。

计时结束要按计划时长给出进度判定，并对"这一模"置位完成闩锁。闩锁按模
独立，置位之后开模与调度才知道哪台机可以放行。过硫或欠硫都会写告警级别
的审计，但只有合格窗口内才认为可以正常取件。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..eventbus import Event, EventBus
from .latch import CureLatch
from .validate import validate_progress

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免环
    from .timer import CureTimer


def complete_cure(timer: "CureTimer", latch: CureLatch, bus: EventBus) -> dict:
    """结束计时并为本模置位完成闩锁。"""

    finished = timer.finish()
    verdict = validate_progress(finished["elapsed_s"], finished["planned_s"], timer.limits)
    mold_id = timer.mold_id or "unknown"
    severity = "notice" if verdict["within_window"] else "warning"
    entry = latch.set(mold_id, timer.clock_now())
    result = {
        "mold_id": mold_id,
        "elapsed_s": verdict["elapsed_s"],
        "verdict": verdict["verdict"],
        "latch_generation": entry.generation,
        "latch_set": latch.is_set(mold_id),
    }
    bus.publish(
        Event.of(
            "timer",
            "timer",
            "cure_finished",
            subject=mold_id,
            severity=severity,
            **result,
        )
    )
    return result
