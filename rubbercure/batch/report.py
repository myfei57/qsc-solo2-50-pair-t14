"""完成上报。

一台硫化机取件后向调度上报一条完成记录，带完成时刻、模具、曲线代际与本
机上报序号。上报本身只组装数据，排序与后续分配交给调度器；上报序号由调
度器统一分配，保证跨机可比。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..eventbus import Event, EventBus


@dataclass(frozen=True)
class CompletionReport:
    """一条完成上报。"""

    press_id: str
    mold_id: str
    product_code: str
    completed_at: float
    curve_generation: int
    sequence: int

    def as_dict(self) -> dict:
        return {
            "press_id": self.press_id,
            "mold_id": self.mold_id,
            "product_code": self.product_code,
            "completed_at": round(self.completed_at, 3),
            "curve_generation": self.curve_generation,
            "sequence": self.sequence,
        }


def report_completion(
    press_id: str,
    mold_id: str,
    product_code: str,
    completed_at: float,
    curve_generation: int,
    sequence: int,
    bus: EventBus,
) -> CompletionReport:
    """组装一条完成上报并发布事件。"""

    report = CompletionReport(
        press_id=press_id,
        mold_id=mold_id,
        product_code=product_code,
        completed_at=float(completed_at),
        curve_generation=int(curve_generation),
        sequence=int(sequence),
    )
    bus.publish(
        Event.of(
            "batch",
            "batch",
            "completion_reported",
            subject=mold_id,
            severity="notice",
            **report.as_dict(),
        )
    )
    return report
