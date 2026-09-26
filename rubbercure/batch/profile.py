"""投产配方解析。

把模具登记的制品型号、制品厚度与最新代际曲线合成一份可执行的投产配方：
计划硫化时长按曲线基线计算，腔数与模具台账一致。配方是调度的输入，也是
审计里"这一模按哪份配方投产"的证据。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..mold.registry import MoldRegistry
from ..timer.curve import CurveBook


@dataclass(frozen=True)
class BatchProfile:
    """一份投产配方。"""

    mold_id: str
    product_code: str
    thickness_mm: float
    cavity_count: int
    planned_s: float
    curve_generation: int

    @classmethod
    def resolve(
        cls,
        mold_id: str,
        thickness_mm: float,
        registry: MoldRegistry,
        curves: CurveBook,
    ) -> "BatchProfile":
        """按模具台账与曲线基线解析投产配方。"""

        record = registry.require_active(mold_id)
        curve = curves.latest_for(record.product_code)
        return cls(
            mold_id=mold_id,
            product_code=record.product_code,
            thickness_mm=float(thickness_mm),
            cavity_count=record.cavity_count,
            planned_s=curves.baseline(record.product_code, thickness_mm),
            curve_generation=curve.generation,
        )

    def as_dict(self) -> dict:
        return {
            "mold_id": self.mold_id,
            "product_code": self.product_code,
            "thickness_mm": round(self.thickness_mm, 3),
            "cavity_count": self.cavity_count,
            "planned_s": round(self.planned_s, 3),
            "curve_generation": self.curve_generation,
        }
