"""停运顺序。

停运顺序固定为：停主蒸汽阀、放残压、排冷却水、停冷却循环。先停汽再停冷却
是硬约束：带着蒸汽停冷却会让模具局部骤冷、产生内应力。顺序自查会把任何
提前停冷却的操作直接指出来。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..clock import Clock
from ..cool.stop import stop_cooling
from ..eventbus import EventBus
from ..press.press import CuringPress
from .selfcheck import SequenceRecorder, verify_order

STOP_ORDER: tuple[tuple[str, str, str], ...] = (
    ("steam_stop", "steam_vent", "先停主蒸汽阀再放残压"),
    ("steam_vent", "coolant_drain", "残压放净后才排冷却水"),
    ("coolant_drain", "cool_stop", "排空之后才停冷却循环"),
    ("steam_stop", "cool_stop", "停汽必须先于停冷却"),
)


@dataclass
class StopSequence:
    """停运顺序执行器。"""

    clock: Clock
    bus: EventBus
    press: CuringPress
    recorder: SequenceRecorder = field(init=False)

    def __post_init__(self) -> None:
        self.recorder = SequenceRecorder(self.bus, "stop")

    def run(self) -> dict:
        """按停运顺序执行，返回执行报告。"""

        self.recorder.clear()
        press = self.press
        mold_id = press.mold_id or press.press_id

        closed = press.steam.close_steam()
        self.recorder.note(
            "steam_stop", "steam", mold_id=mold_id, valve_opening_pct=closed["valve_opening_pct"]
        )

        vent = press.steam.vent_pressure(mold_id)
        self.recorder.note(
            "steam_vent", "steam", mold_id=mold_id, residual_kpa=round(vent.residual_kpa, 3)
        )

        drained = press.cooling.drain()
        self.recorder.note(
            "coolant_drain", "cool", mold_id=mold_id, residual_m3=drained["residual_m3"]
        )

        stopped = stop_cooling(press.cooling, press.steam, self.bus)
        self.recorder.note("cool_stop", "cool", mold_id=mold_id, flow_m3_h=stopped["flow_m3_h"])

        return self.report(mold_id)

    def report(self, mold_id: str) -> dict:
        return {
            "mold_id": mold_id,
            "steps": self.recorder.names(),
            "violations": verify_order(self.recorder.names(), STOP_ORDER),
            "steam_pressure_kpa": round(self.press.steam.pressure_kpa, 3),
            "cooling": self.press.cooling.snapshot(),
        }
