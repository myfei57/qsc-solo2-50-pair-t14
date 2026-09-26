"""开模动作。

硫化结束必须先把蒸汽泄压，再开模取件：带着残余压力开模，制品会被腔内
压力顶裂，模具分型面也可能被冲伤。泄压凭据由蒸汽组件在压力退到安全值
之后签发，开模动作在动作前强制校验。
"""

from __future__ import annotations

from ..errors import InterlockError
from ..eventbus import Event, EventBus
from ..steam.vent import VentToken
from .lift import LiftUnit


def open_mold(lift: LiftUnit, vent: VentToken, bus: EventBus, mold_id: str) -> dict:
    """校验泄压凭据后开模，返回开模结果。"""

    if not vent.is_safe():
        raise InterlockError(
            f"模具 {mold_id} 蒸汽未泄压（残余 {vent.residual_kpa:.3f} kPa），禁止开模",
            component="lift",
        )
    opening = lift.open_clamp(mold_id)
    result = {
        "mold_id": mold_id,
        "opening_pct": round(opening, 3),
        "residual_kpa": round(vent.residual_kpa, 4),
    }
    bus.publish(
        Event.of("lift", "lift", "mold_opened", subject=mold_id, severity="notice", **result)
    )
    return result
