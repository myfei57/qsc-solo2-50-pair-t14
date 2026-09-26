"""开模顺序。

开模顺序固定为：计时完成、蒸汽泄压、开模到位、取件、按模复位完成闩锁。
先泄压再开模是硬约束，取件之后才能复位闩锁——顺序错了要么顶裂制品，要么
让调度误判哪台机可以接下一模。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..clock import Clock
from ..eventbus import EventBus
from ..lift.open import open_mold
from ..press.press import CuringPress
from ..press.transitions import PressPhase
from ..timer.done import complete_cure
from ..timer.latch import CureLatch
from .selfcheck import SequenceRecorder, verify_order

OPEN_ORDER: tuple[tuple[str, str, str], ...] = (
    ("timer_complete", "cooling_start", "计时完成后先启动模温冷却"),
    ("cooling_start", "steam_vent", "冷却启动后才允许泄压"),
    ("steam_vent", "lift_open", "先泄压再开模"),
    ("lift_open", "part_removed", "开模到位后才取件"),
    ("part_removed", "latch_reset", "取件后按模复位完成闩锁"),
)


@dataclass
class OpenSequence:
    """开模顺序执行器。"""

    clock: Clock
    bus: EventBus
    latch: CureLatch
    press: CuringPress
    recorder: SequenceRecorder = field(init=False)

    def __post_init__(self) -> None:
        self.recorder = SequenceRecorder(self.bus, "openmold")

    def run(self) -> dict:
        """按开模顺序执行，返回执行报告。"""

        self.recorder.clear()
        press = self.press
        mold_id = press.require_mold()

        if press.phase in (PressPhase.CURING, PressPhase.COOLING):
            press.transition(PressPhase.VENTING)
        finished = complete_cure(press.timer, self.latch, self.bus)
        self.recorder.note(
            "timer_complete",
            "timer",
            mold_id=mold_id,
            verdict=finished["verdict"],
            latch_set=finished["latch_set"],
        )

        cooling = press.cooling.start_cooling(mold_id)
        self.recorder.note("cooling_start", "cool", mold_id=mold_id, flow_m3_h=cooling["flow_m3_h"])

        vent = press.steam.vent_pressure(mold_id)
        self.recorder.note(
            "steam_vent", "steam", mold_id=mold_id, residual_kpa=round(vent.residual_kpa, 3)
        )

        press.transition(PressPhase.OPENING)
        opened = open_mold(press.lift, vent, self.bus, mold_id)
        self.recorder.note(
            "lift_open", "lift", mold_id=mold_id, opening_pct=opened["opening_pct"]
        )

        press.clear_mold()
        self.recorder.note("part_removed", "press", mold_id=mold_id)

        entry = self.latch.reset(mold_id, self.clock.now())
        self.recorder.note(
            "latch_reset",
            "timer",
            mold_id=mold_id,
            released=bool(entry and entry.released),
        )

        press.finalize_cycle()
        press.idle()
        return self.report(mold_id)

    def report(self, mold_id: str) -> dict:
        return {
            "mold_id": mold_id,
            "steps": self.recorder.names(),
            "violations": verify_order(self.recorder.names(), OPEN_ORDER),
            "latch": self.latch.snapshot(),
            "press_phase": self.press.phase.value,
        }
