"""顺序记录与自查。

四个顺序（硫化、开模、调度、停运）都共用同一套记录与自查：每执行一步就把
步骤名、组件与细节记进去并发布事件，运行完成后按内置的顺序规则逐条对照。
顺序规则是显式的 (先, 后, 说明) 三元组，任何一步提前都会在自查结果里被
指出，而不是靠注释约束约定。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..eventbus import Event, EventBus
from ..ns.topology import Topology


@dataclass(frozen=True)
class OrderStep:
    """一条已执行步骤。"""

    name: str
    component: str
    index: int
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "component": self.component,
            "index": self.index,
            "detail": self.detail,
        }


class SequenceRecorder:
    """记录顺序步骤并发布事件。"""

    def __init__(self, bus: EventBus, topic: str, component: str = "sequence") -> None:
        self._bus = bus
        self._topic = topic
        self._component = component
        self._steps: list[OrderStep] = []

    @property
    def topic(self) -> str:
        return self._topic

    def note(self, step: str, component: str, **detail: object) -> OrderStep:
        """记录一步并发布事件。"""

        record = OrderStep(
            name=step,
            component=component,
            index=len(self._steps) + 1,
            detail=dict(detail),
        )
        self._steps.append(record)
        self._bus.publish(
            Event.of(
                self._topic,
                self._component,
                f"{self._topic}_step",
                subject=step,
                step_index=record.index,
                component_name=component,
                **detail,
            )
        )
        return record

    def names(self) -> list[str]:
        return [record.name for record in self._steps]

    def clear(self) -> None:
        self._steps.clear()

    def as_list(self) -> list[dict]:
        return [record.as_dict() for record in self._steps]


def verify_order(
    executed: list[str], rules: tuple[tuple[str, str, str], ...]
) -> list[str]:
    """按执行记录自查顺序约束，返回违规说明列表（空表示合规）。"""

    violations: list[str] = []
    for earlier, later, description in rules:
        if earlier not in executed or later not in executed:
            violations.append(f"{description}：步骤 {earlier} 或 {later} 未执行")
            continue
        if executed.index(earlier) > executed.index(later):
            violations.append(f"{description}：实际先执行了 {later}")
    return violations


def verify_topology(topology: Topology, rules: tuple[tuple[str, str], ...]) -> list[str]:
    """确认链路拓扑里确实声明了这些上下游关系。"""

    missing: list[str] = []
    for upstream, downstream in rules:
        if not topology.requires_before(upstream, downstream):
            missing.append(f"拓扑缺少 {upstream} -> {downstream}")
    return missing
