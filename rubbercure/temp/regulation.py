"""模温闭环。

模温回路每个周期做四件事：把最新采样整理成按腔位的模温、按温差算出压力
目标修正量、把目标提交给蒸汽压力仲裁器、按工况判定是否进入容差带。进入
容差带并保持足够时长之后才能写模温确认，确认落盘之后硫化计时才被放行。
"""

from __future__ import annotations

import threading
from ..config import TempLimits
from ..eventbus import Event, EventBus
from ..steam.steam import SteamUnit
from .controller import TempController
from .persist import TempPersister, TemperatureConfirmation
from .read import MoldTempReading
from .target import TempTarget
from .tolerance import band_summary, spread_ok
from .warmup import WarmupTrace, execute_warmup, plan_warmup

DEFAULT_WARMUP_STEP_S = 5.0


class TempLoop:
    """模温闭环回路。"""

    def __init__(
        self,
        limits: TempLimits,
        controller: TempController,
        persister: TempPersister,
        steam: SteamUnit,
        bus: EventBus,
        controller_name: str = "temp_loop",
        priority: int = 70,
    ) -> None:
        self.limits = limits
        self.controller = controller
        self.persister = persister
        self.steam = steam
        self.bus = bus
        self.controller_name = controller_name
        self.priority = priority
        self._target: TempTarget | None = None
        self._trace: WarmupTrace | None = None
        self._lock = threading.RLock()

    def target(self) -> TempTarget:
        if self._target is None:
            self._target = TempTarget(
                setpoint_c=self.limits.setpoint_c,
                tolerance_c=self.limits.tolerance_c,
                warmup_rate_c_per_min=self.limits.warmup_rate_c_per_min,
            )
        return self._target

    def step(self, reading: MoldTempReading, dt: float) -> dict:
        """推进一个模温控制周期。"""

        with self._lock:
            measured = reading.average_c()
            correction = self.controller.update(measured, self.limits.setpoint_c, dt)
            target_kpa = self.steam.limits.target_pressure_kpa + correction
            self.steam.submit_setpoint(self.controller_name, target_kpa, self.priority)
            outcome = self.steam.arbiter.resolve()
        band = band_summary(measured, self.limits.setpoint_c, self.limits.tolerance_c)
        spread = reading.spread_c()
        target = self.target()
        return {
            "mold_id": reading.mold_id,
            "generation": reading.generation,
            "measured_c": band["measured_c"],
            "setpoint_kpa": round(outcome.target_kpa, 3),
            "committed_by": outcome.winner,
            "in_band": band["in_band"],
            "target_in_band": target.is_in_band(measured),
            "spread_c": round(spread, 3),
            "spread_ok": spread_ok(spread, self.limits.max_cavity_spread_c),
        }

    def warmup(self, start_c: float, clock, step_s: float = DEFAULT_WARMUP_STEP_S) -> dict:
        """按升温曲线把模温推到容差带下沿。"""

        plan = plan_warmup(start_c, self.limits.setpoint_c, self.limits.warmup_rate_c_per_min)
        trace = WarmupTrace()
        final = execute_warmup(plan, clock, step_s, trace.note)
        with self._lock:
            self._trace = trace
        self.bus.publish(
            Event.of(
                "temp",
                "temp",
                "warmup_done",
                subject=self.controller_name,
                final_c=round(final, 3),
                peak_c=round(trace.peak_c(), 3),
            )
        )
        return {"plan": plan.describe(), "final_c": round(final, 3), "trace": trace.as_dict()}

    def confirm(self, reading: MoldTempReading, hold_s: float) -> TemperatureConfirmation:
        """写模温确认（落盘）。"""

        return self.persister.confirm(reading, hold_s)

    def is_confirmed(self) -> bool:
        return self.persister.is_durable()

    def warmup_trace(self) -> dict:
        with self._lock:
            trace = self._trace
        return trace.as_dict() if trace else {"points": [], "peak_c": 0.0}

    def snapshot(self) -> dict:
        with self._lock:
            controller = self.controller.snapshot()
        return {
            "target": self.target().as_dict(),
            "controller": controller,
            "confirmation": self.persister.snapshot(),
            "warmup": self.warmup_trace(),
        }
