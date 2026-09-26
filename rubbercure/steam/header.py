"""蒸汽母管。

母管压力是整条升温回路的被控量：阀门开度决定注入量，模具与管路散热决定
损失量，两者共同决定压力曲线。母管压力必须能落盘并被下游读取——模温
确认、硫化计时和泄压判定都依赖它。
"""

from __future__ import annotations

import threading

MAX_HEADER_KPA = 800.0


class SteamHeader:
    """蒸汽母管压力容器。"""

    def __init__(self, bus_pressure_kpa: float = 0.0, ambient_kpa: float = 0.0) -> None:
        self._pressure = max(0.0, min(MAX_HEADER_KPA, float(bus_pressure_kpa)))
        self._ambient = float(ambient_kpa)
        self._lock = threading.RLock()

    @property
    def pressure_kpa(self) -> float:
        return self._pressure

    def charge(self, inflow_kg_h: float, dt: float, loss_per_s: float = 0.35) -> float:
        """按注入流量与散热损失推进一个周期，返回新压力。"""

        if dt <= 0:
            raise ValueError("控制周期必须为正")
        with self._lock:
            gain = inflow_kg_h * dt * 0.012
            loss = loss_per_s * dt
            self._pressure = max(
                0.0, min(MAX_HEADER_KPA, self._pressure + gain - loss)
            )
            return self._pressure

    def bleed(self, dt: float, rate_kpa_per_s: float) -> float:
        """按泄压速率放压，返回新压力。"""

        if dt < 0:
            raise ValueError("泄压时长不能为负")
        if rate_kpa_per_s <= 0:
            raise ValueError("泄压速率必须为正")
        with self._lock:
            self._pressure = max(0.0, self._pressure - rate_kpa_per_s * dt)
            return self._pressure

    def relax_toward(self, target_kpa: float, gain: float) -> float:
        """向环境压力松弛，用于长时间静置。"""

        with self._lock:
            self._pressure = max(
                0.0, self._pressure + (float(target_kpa) - self._pressure) * gain
            )
            return self._pressure

    def reset(self) -> float:
        with self._lock:
            self._pressure = self._ambient
            return self._pressure

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "pressure_kpa": round(self._pressure, 3),
                "ambient_kpa": self._ambient,
                "max_kpa": MAX_HEADER_KPA,
            }
