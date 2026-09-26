"""合模行程位置。

开合模用一个 0–100% 的行程量表示：0% 表示模具完全闭合、达到锁模位，100%
表示完全打开、可以取件。合模与开模的方向相反，位置对象本身只描述行程，
不判断顺序；顺序约束由合模／开模动作在动作前显式检查。
"""

from __future__ import annotations

from dataclasses import dataclass

FULLY_OPEN_PCT = 100.0
CLOSED_PCT = 0.0


@dataclass(frozen=True)
class ClampPosition:
    """一个行程位置。"""

    opening_pct: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.opening_pct <= 100.0:
            raise ValueError(f"行程位置越界: {self.opening_pct}")

    def is_closed(self, tolerance_pct: float = 0.5) -> bool:
        return self.opening_pct <= tolerance_pct

    def is_open(self, tolerance_pct: float = 0.5) -> bool:
        return self.opening_pct >= FULLY_OPEN_PCT - tolerance_pct

    def approach(self, target_pct: float, step_pct: float) -> "ClampPosition":
        """朝目标位置移动一步，不越过目标。"""

        if step_pct <= 0:
            raise ValueError("行程步长必须为正")
        target = max(CLOSED_PCT, min(FULLY_OPEN_PCT, float(target_pct)))
        if abs(target - self.opening_pct) <= step_pct:
            return ClampPosition(target)
        direction = 1.0 if target > self.opening_pct else -1.0
        return ClampPosition(self.opening_pct + direction * step_pct)

    def as_dict(self) -> dict:
        return {
            "opening_pct": round(self.opening_pct, 3),
            "closed": self.is_closed(),
            "open": self.is_open(),
        }
