"""疏水阀。

升温必须先开疏水阀把管路与模具腔内的冷凝水排掉，再开主蒸汽阀：带水升温
会形成水锤并让模温迟迟上不去，制品表面还会出现水渍。疏水阀只有开／闭两
态，但开度会随排量线性变化，控制台据此判断是否真的在排水。
"""

from __future__ import annotations

import threading

from ..eventbus import Event, EventBus

MAX_DRAIN_PCT = 100.0


class DrainTrap:
    """疏水阀：排水与排气共用同一条排放通道。"""

    def __init__(self, bus: EventBus, rated_flow_kg_h: float = 240.0) -> None:
        if rated_flow_kg_h <= 0:
            raise ValueError("疏水阀额定流量必须为正")
        self._bus = bus
        self._rated = float(rated_flow_kg_h)
        self._opening_pct = 0.0
        self._lock = threading.RLock()

    @property
    def opening_pct(self) -> float:
        return self._opening_pct

    def is_open(self, tolerance_pct: float = 1.0) -> bool:
        return self._opening_pct > tolerance_pct

    def drain_flow_kg_h(self) -> float:
        return self._rated * self._opening_pct / MAX_DRAIN_PCT

    def open(self, opening_pct: float = MAX_DRAIN_PCT) -> float:
        """开疏水阀到指定开度。"""

        with self._lock:
            self._opening_pct = max(0.0, min(MAX_DRAIN_PCT, float(opening_pct)))
            opening = self._opening_pct
        self._bus.publish(
            Event.of(
                "steam",
                "steam",
                "trap_opened",
                subject="drain-trap",
                opening_pct=round(opening, 2),
                drain_flow_kg_h=round(self.drain_flow_kg_h(), 2),
            )
        )
        return opening

    def close(self) -> float:
        """关疏水阀。"""

        with self._lock:
            self._opening_pct = 0.0
        self._bus.publish(Event.of("steam", "steam", "trap_closed", subject="drain-trap"))
        return 0.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "opening_pct": round(self._opening_pct, 2),
                "open": self.is_open(),
                "drain_flow_kg_h": round(self.drain_flow_kg_h(), 2),
                "rated_flow_kg_h": self._rated,
            }
