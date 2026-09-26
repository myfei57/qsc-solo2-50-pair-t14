"""冷却水循环。

冷却水把硫化完的模具从工艺温度降到取件温度。循环泵只有运行与停止两态，
流量与进出口水温由现场仪表上报。冷却回路要和蒸汽回路配合：蒸汽未停之前
禁止停冷却，避免骤冷。
"""

from __future__ import annotations

import threading

from ..eventbus import Event, EventBus


class CoolantCirculation:
    """冷却水循环泵与进出口温度。"""

    def __init__(self, bus: EventBus, rated_flow_m3_h: float = 18.0) -> None:
        if rated_flow_m3_h <= 0:
            raise ValueError("冷却水额定流量必须为正")
        self._bus = bus
        self._rated = float(rated_flow_m3_h)
        self._running = False
        self._flow_m3_h = 0.0
        self._inlet_c = 0.0
        self._outlet_c = 0.0
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def flow_m3_h(self) -> float:
        with self._lock:
            return self._flow_m3_h

    def circulation_delta_c(self) -> float:
        with self._lock:
            return self._outlet_c - self._inlet_c

    def start(self, flow_m3_h: float | None = None) -> float:
        """启动循环泵。"""

        with self._lock:
            self._running = True
            self._flow_m3_h = self._rated if flow_m3_h is None else float(flow_m3_h)
            flow = self._flow_m3_h
        self._bus.publish(
            Event.of("cool", "cool", "coolant_started", subject="coolant-pump", flow_m3_h=round(flow, 2))
        )
        return flow

    def sample(self, inlet_c: float, outlet_c: float) -> dict:
        """记录一组进出口温度。"""

        with self._lock:
            self._inlet_c = float(inlet_c)
            self._outlet_c = float(outlet_c)
            return {
                "inlet_c": round(self._inlet_c, 3),
                "outlet_c": round(self._outlet_c, 3),
                "delta_c": round(self.circulation_delta_c(), 3),
            }

    def stop(self) -> float:
        """停循环泵。"""

        with self._lock:
            self._running = False
            self._flow_m3_h = 0.0
        self._bus.publish(Event.of("cool", "cool", "coolant_stopped", subject="coolant-pump"))
        return 0.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "flow_m3_h": round(self._flow_m3_h, 2),
                "inlet_c": round(self._inlet_c, 3),
                "outlet_c": round(self._outlet_c, 3),
                "delta_c": round(self.circulation_delta_c(), 3),
                "rated_flow_m3_h": self._rated,
            }
