"""蒸汽加热单元。

把母管、疏水阀、主蒸汽阀、目标值仲裁器与压力调节器装成一台硫化机的蒸汽
回路，对外暴露排气、升温、泄压、停汽四个动作，以及压力闭环的一步推进。
所有顺序约束都在这个门面上执行，控制台与排产不会绕过它直接改阀位。
"""

from __future__ import annotations

import threading

from ..clock import Clock
from ..config import SteamLimits
from ..eventbus import Event, EventBus
from ..store.jsonfile import JsonStore
from .header import SteamHeader
from .pressure import PressureRegulator
from .purge import PurgeToken, purge_cavity
from .sequence import close_steam, open_steam
from .setpoint import SetpointArbiter
from .state import SteamPhase, SteamStateMachine
from .trap import DrainTrap
from .valve import SteamValve
from .vent import VentToken, vent_pressure

STATE_KEY = "steam_state"


class SteamUnit:
    """一台硫化机的蒸汽回路。"""

    def __init__(
        self,
        clock: Clock,
        bus: EventBus,
        store: JsonStore,
        limits: SteamLimits,
    ) -> None:
        self.clock = clock
        self.limits = limits
        self._bus = bus
        self._store = store
        self._state = SteamStateMachine()
        self.header = SteamHeader(bus_pressure_kpa=0.0)
        self.trap = DrainTrap(bus, rated_flow_kg_h=limits.trap_rated_flow_kg_h)
        self.valve = SteamValve(bus, rated_flow_kg_h=limits.valve_rated_flow_kg_h)
        self.arbiter = SetpointArbiter(
            min_kpa=limits.min_pressure_kpa,
            max_kpa=limits.max_pressure_kpa,
            default_kpa=limits.target_pressure_kpa,
        )
        self.regulator = PressureRegulator(
            kp=0.09, ki=0.02, output_min=0.0, output_max=100.0
        )
        self._purge_token: PurgeToken | None = None
        self._vent_token: VentToken | None = None
        self._lock = threading.RLock()

    @property
    def phase(self) -> SteamPhase:
        return self._state.phase

    @property
    def pressure_kpa(self) -> float:
        return self.header.pressure_kpa

    def transition_state(self, target: SteamPhase) -> SteamPhase:
        return self._state.transition(target)

    def opening_for_target(self, target_kpa: float) -> float:
        """把压力目标折算成阀门开度。"""

        clamped = self.limits.clamp_pressure(target_kpa)
        span = max(1.0, self.limits.max_pressure_kpa - self.limits.min_pressure_kpa)
        return 100.0 * (clamped - self.limits.min_pressure_kpa) / span

    def set_valve(self, opening_pct: float) -> float:
        return self.valve.set_opening(opening_pct)

    def close_valve(self) -> float:
        return self.valve.close()

    def remember_purge(self, token: PurgeToken) -> None:
        with self._lock:
            self._purge_token = token

    def remember_vent(self, token: VentToken) -> None:
        with self._lock:
            self._vent_token = token

    def purge_token(self) -> PurgeToken | None:
        with self._lock:
            return self._purge_token

    def vent_token(self) -> VentToken | None:
        with self._lock:
            return self._vent_token

    def is_purged(self) -> bool:
        token = self.purge_token()
        return token is not None and token.is_valid()

    def is_vented(self) -> bool:
        token = self.vent_token()
        return token is not None and token.is_safe()

    def purge_cavity(self, mold_id: str, generation: int = 0) -> PurgeToken:
        """排气并返回凭据。"""

        return purge_cavity(self, mold_id, self._bus, generation=generation)

    def vent_pressure(self, mold_id: str) -> VentToken:
        """泄压并返回凭据。"""

        return vent_pressure(self, mold_id, self._bus)

    def open_steam(self, target_kpa: float) -> dict:
        """按"先疏水、后蒸汽"升温。"""

        return open_steam(self, self._bus, target_kpa)

    def close_steam(self) -> dict:
        """按"先蒸汽、后疏水"停汽。"""

        return close_steam(self, self._bus)

    def submit_setpoint(self, writer: str, target_kpa: float, priority: int) -> None:
        """由一个写入方提交压力目标值。"""

        self.arbiter.request(writer, target_kpa, priority, reason=f"writer={writer}")

    def advance(self, dt: float) -> dict:
        """推进一个控制周期：结算目标值、调节阀位、母管充压。"""

        outcome = self.arbiter.resolve()
        if self.valve.opening_pct <= 0.0 and self._state.phase is SteamPhase.HEATING:
            opening = self.opening_for_target(outcome.target_kpa)
            self.valve.set_opening(opening)
        bias = self.regulator.update(self.header.pressure_kpa, outcome.target_kpa, dt)
        if self._state.phase in (SteamPhase.HEATING, SteamPhase.HOLDING):
            self.valve.set_opening(self.valve.opening_pct + bias * 0.05)
        pressure = self.header.charge(self.valve.flow_kg_h(), dt)
        return {
            "winner": outcome.winner,
            "target_kpa": round(outcome.target_kpa, 3),
            "pressure_kpa": round(pressure, 3),
            "valve_opening_pct": round(self.valve.opening_pct, 2),
            "trap_opening_pct": round(self.trap.opening_pct, 2),
        }

    def persist_state(self) -> str:
        """把相位与压力落盘，返回内容摘要。"""

        snapshot = self._state.snapshot()
        snapshot["pressure_kpa"] = round(self.header.pressure_kpa, 4)
        snapshot["revision"] = len(snapshot["history"])
        digest = self._store.write(STATE_KEY, snapshot)
        self._bus.publish(
            Event.of("steam", "steam", "state_durable", subject=STATE_KEY, digest=digest[:12])
        )
        return digest

    def restore(self) -> bool:
        """从磁盘恢复相位与压力。"""

        snapshot = self._store.read(STATE_KEY)
        if not snapshot:
            return False
        self._state.restore(snapshot)
        pressure = snapshot.get("pressure_kpa")
        if isinstance(pressure, (int, float)):
            self.header.relax_toward(float(pressure), 1.0)
        return True

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "phase": self._state.phase.value,
                "history": self._state.history(),
                "header": self.header.snapshot(),
                "valve": self.valve.snapshot(),
                "trap": self.trap.snapshot(),
                "arbiter": self.arbiter.snapshot(),
                "arbiter_decisions": self.arbiter.decisions(10),
                "regulator": self.regulator.snapshot(),
                "purged": self.is_purged(),
                "vented": self.is_vented(),
            }
