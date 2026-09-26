"""模温目标与容差带。

模温目标由制品工艺给定，允许一个小容差带：低于下限硫化不足（欠硫），
高于上限则过硫。目标值本身不做控制，控制由模温回路完成；这里只负责把
目标、容差和升温速率组织成一个可读的对象，供控制台、曲线比对和计时
判定共用。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TempTarget:
    """一个模温目标。"""

    setpoint_c: float
    tolerance_c: float
    warmup_rate_c_per_min: float

    def __post_init__(self) -> None:
        if self.tolerance_c < 0:
            raise ValueError("模温容差不能为负")
        if self.warmup_rate_c_per_min <= 0:
            raise ValueError("升温速率必须为正")

    def band(self) -> tuple[float, float]:
        """返回 (下限, 上限)。"""

        return (self.setpoint_c - self.tolerance_c, self.setpoint_c + self.tolerance_c)

    def is_in_band(self, measured_c: float) -> bool:
        low, high = self.band()
        return low <= measured_c <= high

    def clamp(self, value_c: float) -> float:
        low, high = self.band()
        return max(low, min(high, float(value_c)))

    def as_dict(self) -> dict:
        low, high = self.band()
        return {
            "setpoint_c": self.setpoint_c,
            "tolerance_c": self.tolerance_c,
            "band_low_c": round(low, 3),
            "band_high_c": round(high, 3),
            "warmup_rate_c_per_min": self.warmup_rate_c_per_min,
        }
