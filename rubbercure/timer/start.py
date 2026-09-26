"""硫化计时启动。

计时启动的唯一前置条件是"模温确认已落盘"。这条约束把温度回路与计时回路
绑在一起：没有落盘确认就启动计时，等于在模具还没到温的情况下开始计时，
制品必然欠硫。启动动作在动作前强制校验，校验不通过直接拒绝并写审计。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import InterlockError
from ..eventbus import Event, EventBus
from ..temp.persist import TempPersister

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免环
    from .timer import CureTimer


def start_cure(
    timer: "CureTimer",
    persister: TempPersister,
    mold_id: str,
    planned_s: float,
    bus: EventBus,
    *,
    product_code: str = "",
    generation: int = 0,
) -> dict:
    """校验模温确认已落盘后启动硫化计时。"""

    confirmation = persister.require_durable()
    if confirmation.mold_id != mold_id:
        raise InterlockError(
            f"模温确认属于 {confirmation.mold_id}，与计时对象 {mold_id} 不一致",
            component="timer",
        )
    started = timer.begin(mold_id, product_code, planned_s, generation)
    bus.publish(
        Event.of(
            "timer",
            "timer",
            "cure_started",
            subject=mold_id,
            severity="notice",
            planned_s=round(planned_s, 3),
            curve_generation=generation,
            temp_confirmation_digest=confirmation.digest[:12],
        )
    )
    return started
