"""主蒸汽阀。

主蒸汽阀是升温的唯一蒸汽来源。它的开度直接决定母管进入模具的蒸汽量，
因此泄压时必须先把它关到位再放残压。阀位是不可逆读的现场量：控制器只
通过本对象读写，不允许绕过它直接改母管压力。
"""

from __future__ import annotations

import threading

from ..eventbus import Event, EventBus

MAX_OPENING_PCT = 100.0


class SteamValve:
    """主蒸汽阀。"""

    def __init__(self, bus: EventBus, rated_flow_kg_h: float = 960.0) -> None:
        if rated_flow_kg_h <= 0:
            raise ValueError("主蒸汽阀额定流量必须为正")
        self._bus = bus
        self._rated = float(rated_flow_kg_h)
        self._opening_pct = 0.0
        self._lock = threading.RLock()

    @property
    def opening_pct(self) -> float:
        return self._opening_pct

    def is_open(self, tolerance_pct: float = 1.0) -> bool:
        return self._opening_pct > tolerance_pct

    def flow_kg_h(self) -> float:
        return self._rated * self._opening_pct / MAX_OPENING_PCT

    def set_opening(self, opening_pct: float) -> float:
        """把阀门开到指定开度。"""

        with self._lock:
            self._opening_pct = max(0.0, min(MAX_OPENING_PCT, float(opening_pct)))
            opening = self._opening_pct
        self._bus.publish(
            Event.of(
                "steam",
                "steam",
                "steam_valve_set",
                subject="steam-valve",
                opening_pct=round(opening, 2),
                flow_kg_h=round(self.flow_kg_h(), 2),
            )
        )
        return opening

    def close(self) -> float:
        """把阀门关到位。"""

        with self._lock:
            self._opening_pct = 0.0
        self._bus.publish(Event.of("steam", "steam", "steam_valve_closed", subject="steam-valve"))
        return 0.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "opening_pct": round(self._opening_pct, 2),
                "open": self.is_open(),
                "flow_kg_h": round(self.flow_kg_h(), 2),
                "rated_flow_kg_h": self._rated,
            }
