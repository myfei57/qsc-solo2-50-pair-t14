"""硫化曲线基线与代际。

硫化时长不是常数，而是由制品工艺曲线给出：曲线按制品型号登记，并随工艺
调整、配方迭代、客户改版产生新代际。计时必须以代际最新的曲线为基线计算，
否则用旧配方计时，轻则欠硫、重则过硫。基线登记同样单调递增，读取按代际
最大者胜出。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..errors import CureError
from ..mold.generation import GenerationCounter

REFERENCE_THICKNESS_MM = 6.0


@dataclass(frozen=True)
class CureCurve:
    """一份制品硫化曲线基线。"""

    product_code: str
    generation: int
    nominal_s: float
    registered_at: float

    def __post_init__(self) -> None:
        if self.nominal_s <= 0:
            raise CureError(f"曲线 {self.product_code} 的标称硫化时长必须为正", component="timer")

    def cure_seconds(self, thickness_mm: float) -> float:
        """按制品厚度折算硫化时长：厚度越大，时间越长。"""

        if thickness_mm <= 0:
            raise CureError("制品厚度必须为正", component="timer")
        return self.nominal_s * (thickness_mm / REFERENCE_THICKNESS_MM) ** 1.4

    def as_dict(self) -> dict:
        return {
            "product_code": self.product_code,
            "generation": self.generation,
            "nominal_s": round(self.nominal_s, 3),
            "registered_at": round(self.registered_at, 3),
        }


class CurveBook:
    """按制品型号保存全部代际曲线，读取时取最新代际。"""

    def __init__(self, counter: GenerationCounter | None = None) -> None:
        self._counter = counter or GenerationCounter()
        self._by_product: dict[str, list[CureCurve]] = {}
        self._lock = threading.RLock()

    def register(self, product_code: str, nominal_s: float, registered_at: float) -> CureCurve:
        """登记一份新代际曲线。"""

        if not product_code:
            raise CureError("制品型号不能为空", component="timer")
        with self._lock:
            generation = self._counter.next()
            curve = CureCurve(
                product_code=product_code,
                generation=generation,
                nominal_s=float(nominal_s),
                registered_at=float(registered_at),
            )
            self._by_product.setdefault(product_code, []).append(curve)
        return curve

    def latest_for(self, product_code: str) -> CureCurve:
        """返回该制品型号代际最新的曲线。"""

        with self._lock:
            history = list(self._by_product.get(product_code, ()))
        if not history:
            raise CureError(f"制品 {product_code} 尚未登记硫化曲线", component="timer")
        return max(history, key=lambda item: item.generation)

    def baseline(self, product_code: str, thickness_mm: float) -> float:
        """按最新代际曲线算出该制品的标称硫化时长。"""

        return self.latest_for(product_code).cure_seconds(thickness_mm)

    def history(self, product_code: str) -> list[CureCurve]:
        with self._lock:
            history = list(self._by_product.get(product_code, ()))
        return sorted(history, key=lambda item: item.generation)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                product_code: [curve.as_dict() for curve in history]
                for product_code, history in sorted(self._by_product.items())
            }
