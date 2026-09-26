"""冷却水告警。

进出水温超限、循环流量不足都要单独告警：流量不足会让模温降不下来，水温
过高会让冷却时间不受控。告警对象只做判定与发布，不改变回路状态。
"""

from __future__ import annotations

from ..config import CoolLimits
from ..eventbus import Event, EventBus


class CoolantAlarm:
    """按限值判定冷却回路是否越限。"""

    def __init__(self, bus: EventBus, limits: CoolLimits) -> None:
        self._bus = bus
        self._limits = limits

    def check(self, inlet_c: float, outlet_c: float, flow_m3_h: float) -> dict | None:
        """检查一组冷却读数，越限时发布告警并返回详情。"""

        reasons: list[str] = []
        if outlet_c > self._limits.alarm_outlet_c:
            reasons.append("outlet_high")
        if inlet_c > self._limits.alarm_inlet_c:
            reasons.append("inlet_high")
        if flow_m3_h < self._limits.min_flow_m3_h:
            reasons.append("flow_low")
        if not reasons:
            return None
        detail = {
            "reasons": reasons,
            "inlet_c": round(inlet_c, 3),
            "outlet_c": round(outlet_c, 3),
            "flow_m3_h": round(flow_m3_h, 3),
        }
        self._bus.publish(
            Event.of(
                "cool",
                "cool",
                "coolant_alarm",
                subject="coolant-loop",
                severity="alarm",
                **detail,
            )
        )
        return detail
