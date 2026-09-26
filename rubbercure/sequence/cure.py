"""硫化顺序。

硫化顺序按设计文档固定为：模具登记、传感器映射登记、合模前排气、合模锁模、
通汽升温、模温确认落盘、启动硫化计时。每一步都发事件，运行完成后按记录
自查：任何一步提前（例如没排气就合模、没落盘确认就计时）都会被
``verify_order`` 指出来。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..batch.profile import BatchProfile
from ..clock import Clock
from ..config import PlatformConfig
from ..eventbus import EventBus
from ..lift.close import close_mold
from ..mold.mapping import SensorMapBook
from ..mold.registry import MoldRegistry
from ..press.press import CuringPress
from ..press.transitions import PressPhase
from ..temp.read import read_mold_temp
from ..timer.curve import CurveBook
from ..timer.start import start_cure
from .selfcheck import SequenceRecorder, verify_order, verify_topology

CURE_ORDER: tuple[tuple[str, str, str], ...] = (
    ("mold_registered", "mapping_registered", "模具登记必须先于传感器映射登记"),
    ("mapping_registered", "cavity_purge", "排气必须按已登记的映射代际进行"),
    ("cavity_purge", "lift_close", "合模前必须先排气"),
    ("lift_close", "steam_open", "合模到位后才允许通汽升温"),
    ("steam_open", "temp_confirm", "通汽升温后才可能确认模温"),
    ("temp_confirm", "timer_start", "模温确认落盘后才允许启动硫化计时"),
    ("mold_registered", "lift_close", "模具登记必须先于合模"),
)

TOPOLOGY_RULES: tuple[tuple[str, str], ...] = (
    ("mold", "lift"),
    ("lift", "steam"),
    ("steam", "temp"),
    ("temp", "timer"),
)


@dataclass(frozen=True)
class CureRunPlan:
    """一次硫化的输入。"""

    mold_id: str
    product_code: str
    cavity_count: int
    thickness_mm: float
    sensors: dict[int, str]
    raw_readings: dict[str, float]
    target_kpa: float
    warmup_start_c: float
    temp_hold_s: float


@dataclass
class CureSequence:
    """硫化顺序执行器。"""

    clock: Clock
    bus: EventBus
    config: PlatformConfig
    registry: MoldRegistry
    maps: SensorMapBook
    curves: CurveBook
    press: CuringPress
    last_confirmation: dict = field(default_factory=dict)
    recorder: SequenceRecorder = field(init=False)

    def __post_init__(self) -> None:
        self.recorder = SequenceRecorder(self.bus, "cure")

    def run(self, plan: CureRunPlan) -> dict:
        """按硫化顺序执行，返回执行报告。"""

        self.recorder.clear()
        press = self.press
        now = self.clock.now()

        record = self.registry.register(
            plan.mold_id, plan.product_code, plan.cavity_count, now
        )
        self.recorder.note(
            "mold_registered", "mold", mold_id=plan.mold_id, generation=record.generation
        )

        mapping = self.maps.register(plan.mold_id, plan.sensors, now)
        self.recorder.note(
            "mapping_registered", "mold", mold_id=plan.mold_id, generation=mapping.generation
        )

        press.load_mold(plan.mold_id)
        profile = BatchProfile.resolve(
            plan.mold_id, plan.thickness_mm, self.registry, self.curves
        )

        purge = press.steam.purge_cavity(plan.mold_id, generation=mapping.generation)
        self.recorder.note(
            "cavity_purge", "steam", mold_id=plan.mold_id, hold_s=round(purge.hold_s, 3)
        )

        press.transition(PressPhase.CLAMPING)
        closed = close_mold(
            press.lift,
            purge,
            self.bus,
            plan.mold_id,
            now=self.clock.now(),
            max_age_s=self.config.steam.purge_token_max_age_s,
        )
        self.recorder.note(
            "lift_close", "lift", mold_id=plan.mold_id, clamp_force_kn=closed["clamp_force_kn"]
        )

        press.transition(PressPhase.HEATING)
        steam = press.steam.open_steam(plan.target_kpa)
        self.recorder.note(
            "steam_open",
            "steam",
            mold_id=plan.mold_id,
            valve_opening_pct=steam["valve_opening_pct"],
            trap_opening_pct=steam["trap_opening_pct"],
        )

        warmup = press.temp_loop.warmup(plan.warmup_start_c, self.clock)
        reading = read_mold_temp(
            plan.mold_id,
            self.maps.latest_for(plan.mold_id),
            plan.raw_readings,
            self.clock.now(),
        )
        confirmation = press.temp_loop.confirm(reading, plan.temp_hold_s)
        self.recorder.note(
            "temp_confirm",
            "temp",
            mold_id=plan.mold_id,
            confirmed_c=round(confirmation.confirmed_c, 3),
            generation=confirmation.generation,
        )
        self.last_confirmation = confirmation.as_dict()

        press.lift.require_closed()
        press.timer.set_clock_now(self.clock.now())
        started = start_cure(
            press.timer,
            press.temp_persister,
            plan.mold_id,
            profile.planned_s,
            self.bus,
            product_code=profile.product_code,
            generation=profile.curve_generation,
        )
        self.recorder.note(
            "timer_start",
            "timer",
            mold_id=plan.mold_id,
            planned_s=started["planned_s"],
            curve_generation=started["curve_generation"],
            confirmed=press.temp_loop.is_confirmed(),
        )
        press.transition(PressPhase.CURING)
        return self.report(plan, warmup)

    def report(self, plan: CureRunPlan, warmup: dict) -> dict:
        """汇总本次硫化执行情况。"""

        return {
            "mold_id": plan.mold_id,
            "steps": self.recorder.names(),
            "violations": verify_order(self.recorder.names(), CURE_ORDER),
            "trace": self.recorder.as_list(),
            "warmup": warmup,
            "timer": self.press.timer.snapshot(),
        }

    def verify_topology(self, topology) -> list[str]:
        """确认链路拓扑里声明了硫化的上下游关系。"""

        return verify_topology(topology, TOPOLOGY_RULES)
