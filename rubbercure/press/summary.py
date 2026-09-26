"""机群汇总。

控制台与调度都要一份机群概览：多少台在硫化、多少台空闲、各相位分布如何。
汇总只读机群快照，不改变任何状态。
"""

from __future__ import annotations

from .fleet import PressFleet


def summarize_fleet(fleet: PressFleet) -> dict:
    """按相位统计机群状态。"""

    by_phase: dict[str, list[str]] = {}
    loaded: list[str] = []
    for unit in fleet.all():
        snapshot = unit.snapshot()
        by_phase.setdefault(snapshot["phase"], []).append(unit.press_id)
        if snapshot["mold_id"]:
            loaded.append(unit.press_id)
    return {
        "size": fleet.size,
        "by_phase": {phase: sorted(ids) for phase, ids in sorted(by_phase.items())},
        "idle": sorted(unit.press_id for unit in fleet.idle_presses()),
        "loaded": sorted(loaded),
    }
