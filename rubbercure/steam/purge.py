"""模具腔排气。

合模前必须把模具腔里的空气排掉：残存空气在合模时被压缩成气囊，制品表面
会出现缺胶、气泡与流痕。排气依赖疏水阀这条排放通道，过程里主蒸汽阀必须
保持关闭，边排气边进汽等于没排。排气完成后签发凭据，合模动作据此放行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import InterlockError
from ..eventbus import Event, EventBus
from .state import SteamPhase

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免与 steam.steam 形成环
    from .steam import SteamUnit


@dataclass(frozen=True)
class PurgeToken:
    """排气凭据：交给合模动作作为前置条件。"""

    mold_id: str
    generation: int
    purged_at: float
    hold_s: float
    min_hold_s: float
    valid: bool = True

    def is_valid(self) -> bool:
        """排气保持时间达到工艺下限才算有效。"""

        return bool(self.valid) and self.hold_s >= self.min_hold_s

    def is_stale(self, now: float, max_age_s: float) -> bool:
        return (now - self.purged_at) > max_age_s

    def as_dict(self) -> dict:
        return {
            "mold_id": self.mold_id,
            "generation": self.generation,
            "purged_at": round(self.purged_at, 3),
            "hold_s": round(self.hold_s, 3),
            "valid": self.is_valid(),
        }


def purge_cavity(
    unit: "SteamUnit",
    mold_id: str,
    bus: EventBus,
    *,
    generation: int = 0,
    hold_s: float | None = None,
) -> PurgeToken:
    """打开疏水阀把腔内空气排净，返回排气凭据。"""

    if unit.valve.is_open():
        raise InterlockError(f"模具 {mold_id} 排气期间主蒸汽阀必须关闭", component="steam")
    hold = unit.limits.purge_hold_s if hold_s is None else float(hold_s)
    if hold < 0:
        raise ValueError("排气保持时间不能为负")
    unit.transition_state(SteamPhase.PURGING)
    bus.publish(
        Event.of("steam", "steam", "purge_start", subject=mold_id, hold_s=hold, generation=generation)
    )
    unit.trap.open()
    unit.clock.sleep(hold)
    drain_flow = unit.trap.drain_flow_kg_h()
    unit.trap.close()
    token = PurgeToken(
        mold_id=mold_id,
        generation=generation,
        purged_at=unit.clock.now(),
        hold_s=hold,
        min_hold_s=unit.limits.purge_hold_s,
    )
    bus.publish(
        Event.of(
            "steam",
            "steam",
            "purge_done",
            subject=mold_id,
            drain_flow_kg_h=round(drain_flow, 2),
            **token.as_dict(),
        )
    )
    unit.remember_purge(token)
    return token
