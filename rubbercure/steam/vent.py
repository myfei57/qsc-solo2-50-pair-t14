"""硫化结束泄压。

开模前必须把模具腔与母管的残余蒸汽放到安全值以下：带压开模会把制品顶裂、
冲伤分型面。泄压依赖主蒸汽阀关闭 + 疏水阀排放，过程分步推进，压力降到
安全阈值之后才签发凭据。凭据里带残余压力，开模动作据此判定是否放行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import InterlockError
from ..eventbus import Event, EventBus
from .state import SteamPhase

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免与 steam.steam 形成环
    from .steam import SteamUnit

SAFE_RESIDUAL_KPA = 12.0
VENT_RATE_KPA_PER_S = 40.0
VENT_STEP_S = 1.0


@dataclass(frozen=True)
class VentToken:
    """泄压凭据：交给开模动作作为前置条件。"""

    mold_id: str
    residual_kpa: float
    vented_at: float
    safe_below_kpa: float
    hold_s: float

    def is_safe(self) -> bool:
        return self.residual_kpa <= self.safe_below_kpa

    def as_dict(self) -> dict:
        return {
            "mold_id": self.mold_id,
            "residual_kpa": round(self.residual_kpa, 3),
            "vented_at": round(self.vented_at, 3),
            "safe_below_kpa": self.safe_below_kpa,
            "safe": self.is_safe(),
        }


def vent_pressure(
    unit: "SteamUnit",
    mold_id: str,
    bus: EventBus,
    *,
    hold_s: float | None = None,
) -> VentToken:
    """关闭主蒸汽阀并放残压，返回泄压凭据。"""

    hold = unit.limits.vent_hold_s if hold_s is None else float(hold_s)
    if hold < 0:
        raise ValueError("泄压保持时间不能为负")
    unit.transition_state(SteamPhase.VENTING)
    bus.publish(
        Event.of("steam", "steam", "vent_start", subject=mold_id, pressure_kpa=unit.header.pressure_kpa)
    )
    unit.valve.close()
    unit.trap.open()
    unit.clock.sleep(hold)
    guard = 0
    while unit.header.pressure_kpa > SAFE_RESIDUAL_KPA and guard < 10_000:
        unit.header.bleed(VENT_STEP_S, VENT_RATE_KPA_PER_S)
        guard += 1
    unit.trap.close()
    token = VentToken(
        mold_id=mold_id,
        residual_kpa=unit.header.pressure_kpa,
        vented_at=unit.clock.now(),
        safe_below_kpa=SAFE_RESIDUAL_KPA,
        hold_s=hold,
    )
    if not token.is_safe():  # pragma: no cover - 仅在泄压速率被改成 0 时触达
        raise InterlockError(
            f"模具 {mold_id} 泄压后残余 {token.residual_kpa:.3f} kPa 仍高于安全值",
            component="steam",
        )
    bus.publish(
        Event.of("steam", "steam", "vent_done", subject=mold_id, **token.as_dict())
    )
    unit.remember_vent(token)
    return token
