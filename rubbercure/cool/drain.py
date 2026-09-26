"""冷却水排放。

停冷却之前先排空管路：留着存水，冬天会冻裂，检修时也会滴漏。排放按保持
时长推进，排空后返回残余水量比例，供审计留痕。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..eventbus import Event, EventBus
from .state import CoolPhase

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免环
    from .cool import CoolingUnit


def drain_coolant(unit: "CoolingUnit", bus: EventBus, hold_s: float | None = None) -> dict:
    """按保持时长排空冷却管路，返回排放结果。"""

    hold = unit.limits.drain_hold_s if hold_s is None else float(hold_s)
    if hold < 0:
        raise ValueError("排放保持时间不能为负")
    unit.transition_state(CoolPhase.DRAINING)
    bus.publish(Event.of("cool", "cool", "drain_start", subject="coolant-loop", hold_s=hold))
    unit.clock.sleep(hold)
    residual = max(0.0, unit.circulation.flow_m3_h * 0.001)
    result = {"hold_s": round(hold, 3), "residual_m3": round(residual, 4)}
    bus.publish(Event.of("cool", "cool", "drain_done", subject="coolant-loop", **result))
    return result
