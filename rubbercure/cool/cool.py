"""冷却回路单元。

把循环泵、告警与排放装成一台硫化机的冷却回路，对外暴露降温、采样、排放
与停运四个动作。停运顺序由 ``stop_cooling`` 统一校验，控制台不会绕过它。
"""

from __future__ import annotations

import threading

from ..clock import Clock
from ..config import CoolLimits
from ..eventbus import Event, EventBus
from .alarm import CoolantAlarm
from .circulation import CoolantCirculation
from .drain import drain_coolant
from .state import CoolPhase, CoolStateMachine


class CoolingUnit:
    """一台硫化机的冷却回路。"""

    def __init__(self, clock: Clock, bus: EventBus, limits: CoolLimits) -> None:
        self.clock = clock
        self.limits = limits
        self._bus = bus
        self.phase = CoolPhase.IDLE
        self._state = CoolStateMachine()
        self.circulation = CoolantCirculation(bus, rated_flow_m3_h=limits.rated_flow_m3_h)
        self.alarm = CoolantAlarm(bus, limits)
        self._mold_id: str | None = None
        self._lock = threading.RLock()

    @property
    def mold_id(self) -> str | None:
        with self._lock:
            return self._mold_id

    def set_mold(self, mold_id: str) -> None:
        with self._lock:
            self._mold_id = mold_id

    def transition_state(self, target: CoolPhase) -> CoolPhase:
        self.phase = self._state.transition(target)
        return self.phase

    def start_cooling(self, mold_id: str) -> dict:
        """启动冷却循环。"""

        self.set_mold(mold_id)
        self.transition_state(CoolPhase.CIRCULATING)
        flow = self.circulation.start()
        result = {"mold_id": mold_id, "flow_m3_h": round(flow, 2)}
        self._bus.publish(
            Event.of("cool", "cool", "cooling_started", subject=mold_id, severity="info", **result)
        )
        return result

    def measure(self, inlet_c: float, outlet_c: float) -> dict:
        """记录冷却温度并做越限判定。"""

        sample = self.circulation.sample(inlet_c, outlet_c)
        alarm = self.alarm.check(inlet_c, outlet_c, self.circulation.flow_m3_h)
        sample["alarm"] = alarm
        return sample

    def drain(self) -> dict:
        """排空冷却管路。"""

        return drain_coolant(self, self._bus)

    def snapshot(self) -> dict:
        with self._lock:
            mold_id = self._mold_id
        return {
            "phase": self._state.phase.value,
            "history": self._state.history(),
            "mold_id": mold_id,
            "circulation": self.circulation.snapshot(),
            "limits": self.limits.as_dict(),
        }
