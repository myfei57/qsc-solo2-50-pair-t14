"""蒸汽升温与停汽顺序。

升温顺序固定为"先开疏水阀、再开主蒸汽阀"：先排水再进汽，母管里才不会
夹带冷凝水。停汽顺序反过来先关主蒸汽阀、再关疏水阀，把残余冷凝水放净。
顺序错误会在管线里形成水锤，也会让模温迟迟上不去。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..eventbus import Event, EventBus
from .state import SteamPhase

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免与 steam.steam 形成环
    from .steam import SteamUnit


def open_steam(unit: "SteamUnit", bus: EventBus, target_kpa: float) -> dict:
    """按"先疏水、后蒸汽"的顺序升温，返回升温结果。"""

    unit.transition_state(SteamPhase.PURGING)
    trap_opening = unit.trap.open()
    unit.transition_state(SteamPhase.HEATING)
    valve_opening = unit.set_valve(unit.opening_for_target(target_kpa))
    result = {
        "target_kpa": round(unit.limits.clamp_pressure(target_kpa), 3),
        "trap_opening_pct": round(trap_opening, 2),
        "valve_opening_pct": round(valve_opening, 2),
        "order": ["trap_open", "steam_valve_open"],
    }
    bus.publish(
        Event.of(
            "steam",
            "steam",
            "steam_opened",
            subject="steam-header",
            trap_opening_pct=result["trap_opening_pct"],
            valve_opening_pct=result["valve_opening_pct"],
            target_kpa=result["target_kpa"],
        )
    )
    return result


def close_steam(unit: "SteamUnit", bus: EventBus) -> dict:
    """按"先关蒸汽、后关疏水"的顺序停汽，返回停汽结果。"""

    valve_opening = unit.close_valve()
    trap_opening = unit.trap.close()
    result = {
        "valve_opening_pct": round(valve_opening, 2),
        "trap_opening_pct": round(trap_opening, 2),
        "order": ["steam_valve_close", "trap_close"],
    }
    bus.publish(
        Event.of(
            "steam",
            "steam",
            "steam_closed",
            subject="steam-header",
            valve_opening_pct=result["valve_opening_pct"],
            trap_opening_pct=result["trap_opening_pct"],
        )
    )
    return result
