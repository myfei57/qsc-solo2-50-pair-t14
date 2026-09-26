"""机群调度顺序。

调度顺序固定为：完成上报、顺序确认、下一模分配、写审计。完成上报来自各台
硫化机，顺序确认把并发上报排成可复现顺序，再据此挑空闲机位分配下一模，
最后把这次派工写进审计。顺序确认必须先于分配，否则下一模会跟着线程调度
随机挑机。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..batch.batch import BatchScheduler
from ..batch.ordering import order_completions, ordering_keys
from ..batch.schedule import schedule_next
from ..clock import Clock
from ..eventbus import Event, EventBus
from ..mold.registry import MoldRegistry
from ..press.fleet import PressFleet
from ..press.press import CuringPress
from .selfcheck import SequenceRecorder, verify_order

DISPATCH_ORDER: tuple[tuple[str, str, str], ...] = (
    ("completion_report", "order_confirm", "完成上报后才做顺序确认"),
    ("order_confirm", "mold_assigned", "顺序确认后才分配下一模"),
    ("mold_assigned", "audit_note", "分配之后才写调度审计"),
)


@dataclass
class DispatchSequence:
    """机群调度顺序执行器。"""

    clock: Clock
    bus: EventBus
    registry: MoldRegistry
    scheduler: BatchScheduler
    fleet: PressFleet
    press: CuringPress
    recorder: SequenceRecorder = field(init=False)

    def __post_init__(self) -> None:
        self.recorder = SequenceRecorder(self.bus, "dispatch")

    def run(self) -> dict:
        """按调度顺序执行，返回执行报告。"""

        self.recorder.clear()
        press = self.press
        timer = press.timer.snapshot()
        mold_id = str(timer.get("mold_id") or press.press_id)
        product_code = str(timer.get("product_code") or "")
        generation = int(timer.get("curve_generation") or 0)

        report = self.scheduler.record_report(
            press.press_id,
            mold_id,
            product_code,
            self.clock.now(),
            generation,
        )
        self.recorder.note(
            "completion_report",
            "batch",
            press_id=press.press_id,
            mold_id=mold_id,
            sequence=report.sequence,
        )

        ordered = order_completions(
            self.scheduler.recent_reports(),
            self.scheduler.limits.dispatch_window_s,
        )
        self.recorder.note(
            "order_confirm", "batch", confirmed=len(ordered), window_s=self.scheduler.limits.dispatch_window_s
        )

        assignment = schedule_next(
            self.scheduler,
            self.fleet,
            self.scheduler.queue,
            self.registry,
            self.bus,
            self.clock.now(),
        )
        self.recorder.note(
            "mold_assigned",
            "batch",
            press_id=assignment.press_id if assignment else "",
            mold_id=assignment.mold_id if assignment else "",
        )

        self.bus.publish(
            Event.of(
                "batch",
                "batch",
                "dispatch_ordered",
                subject=mold_id,
                severity="info",
                ordered=[item.mold_id for item in ordered],
                ordering=ordering_keys(ordered),
                assigned=(assignment.as_dict() if assignment else None),
            )
        )
        self.recorder.note("audit_note", "audit", mold_id=mold_id)
        return self.report(mold_id, assignment)

    def report(self, mold_id: str, assignment) -> dict:
        return {
            "mold_id": mold_id,
            "steps": self.recorder.names(),
            "violations": verify_order(self.recorder.names(), DISPATCH_ORDER),
            "queued": self.scheduler.queue.entries(),
            "assigned": assignment.as_dict() if assignment else None,
            "scheduler_phase": self.scheduler.phase.value,
        }
