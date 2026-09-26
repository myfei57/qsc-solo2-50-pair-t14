"""模温闭环控制器。

模温回路的被控量是模具平均温度，输出是蒸汽压力目标的修正量。控制器带
积分限幅，饱和时回退积分，避免升温后期在容差带边缘反复超调。控制器只
产出建议值，真正的压力目标还要经过多写入方仲裁。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TempController:
    """带积分限幅的 PI 控制器。"""

    kp: float
    ki: float
    output_min: float
    output_max: float
    _integral: float = 0.0

    def update(self, measured_c: float, setpoint_c: float, dt: float) -> float:
        """按温差推进一个控制周期，返回压力修正量。"""

        if dt <= 0:
            raise ValueError("控制周期必须为正")
        error = setpoint_c - measured_c
        self._integral += error * dt
        raw = self.kp * error + self.ki * self._integral
        clamped = max(self.output_min, min(self.output_max, raw))
        if clamped != raw:
            self._integral -= error * dt
        return clamped

    def reset(self) -> None:
        self._integral = 0.0

    @property
    def integral(self) -> float:
        return self._integral

    def snapshot(self) -> dict:
        return {
            "kp": self.kp,
            "ki": self.ki,
            "integral": round(self._integral, 4),
            "output_min": self.output_min,
            "output_max": self.output_max,
        }
