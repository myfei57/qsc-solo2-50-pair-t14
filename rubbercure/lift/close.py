"""合模动作。

合模前必须先排气：模具腔内残存空气会在合模时被压缩成气囊，硫化出来的
制品表面出现缺胶与气泡。排气凭据由蒸汽／排气组件签发，合模动作在动作前
强制校验，缺凭据或凭据过期都拒绝合模。
"""

from __future__ import annotations

from ..errors import InterlockError
from ..eventbus import Event, EventBus
from ..steam.purge import PurgeToken
from .lift import LiftUnit


def close_mold(
    lift: LiftUnit,
    purge: PurgeToken,
    bus: EventBus,
    mold_id: str,
    *,
    now: float | None = None,
    max_age_s: float | None = None,
) -> dict:
    """校验排气凭据后合模，返回合模结果。"""

    if not purge.is_valid():
        raise InterlockError(f"模具 {mold_id} 未完成排气，禁止合模", component="lift")
    if now is not None and max_age_s is not None and purge.is_stale(now, max_age_s):
        raise InterlockError(
            f"模具 {mold_id} 的排气凭据已超过 {max_age_s:.0f}s，需重新排气",
            component="lift",
        )
    opening = lift.close_clamp(mold_id)
    result = {
        "mold_id": mold_id,
        "opening_pct": round(opening, 3),
        "clamp_force_kn": round(lift.clamp_force_kn(), 2),
        "purge_generation": purge.generation,
    }
    bus.publish(
        Event.of("lift", "lift", "mold_clamped", subject=mold_id, severity="info", **result)
    )
    return result
