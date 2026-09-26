"""母管压力调节器。

模温回路的升温段需要一个压力闭环：把母管压力拉到目标值并稳定住。控制器
输出的是阀门开度增量，带积分限幅，饱和时回退积分，避免升温后期的长时间
滞后。调节器只产出建议开度，真正的开度还要经过多写入方仲裁。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PressureRegulator:
    """带积分限幅的 PI 调节器。"""

    kp: float
    ki: float
    output_min: float
    output_max: float
    _integral: float = 0.0

    def update(self, measured_kpa: float, target_kpa: float, dt: float) -> float:
        """按偏差推进一个控制周期，返回建议开度。"""

        if dt <= 0:
            raise ValueError("控制周期必须为正")
        error = target_kpa - measured_kpa
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
