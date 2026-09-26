"""模具台账。

模具必须先登记才能在产线上流转：登记记录模具编号、制品型号、腔数与代际。
合模与硫化都要求模具处于"已登记且未报废"的状态，台账因此成为整条硫化链
的第一道前置条件。同一台模具可以重复登记（修模、换型），代际同样单调
递增，报废则把状态置为 ``retired``。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..errors import MoldError
from .generation import GenerationCounter

ACTIVE = "active"
RETIRED = "retired"


@dataclass(frozen=True)
class MoldRecord:
    """一台模具的登记记录。"""

    mold_id: str
    product_code: str
    cavity_count: int
    generation: int
    registered_at: float
    status: str = ACTIVE

    def is_active(self) -> bool:
        return self.status == ACTIVE

    def as_dict(self) -> dict:
        return {
            "mold_id": self.mold_id,
            "product_code": self.product_code,
            "cavity_count": self.cavity_count,
            "generation": self.generation,
            "registered_at": round(self.registered_at, 3),
            "status": self.status,
        }


class MoldRegistry:
    """线程安全的模具台账。"""

    def __init__(self, counter: GenerationCounter | None = None) -> None:
        self._counter = counter or GenerationCounter()
        self._records: dict[str, MoldRecord] = {}
        self._lock = threading.RLock()

    def register(
        self,
        mold_id: str,
        product_code: str,
        cavity_count: int,
        registered_at: float,
    ) -> MoldRecord:
        """登记（或重新登记）一台模具。"""

        if not mold_id:
            raise MoldError("模具编号不能为空", component="mold")
        if cavity_count < 1:
            raise MoldError(f"模具 {mold_id} 的腔数必须为正", component="mold")
        with self._lock:
            generation = self._counter.next()
            record = MoldRecord(
                mold_id=mold_id,
                product_code=product_code,
                cavity_count=int(cavity_count),
                generation=generation,
                registered_at=float(registered_at),
                status=ACTIVE,
            )
            self._records[mold_id] = record
        return record

    def resolve(self, mold_id: str) -> MoldRecord:
        """解析模具记录；未登记时报错。"""

        with self._lock:
            record = self._records.get(mold_id)
        if record is None:
            raise MoldError(f"模具 {mold_id} 未登记", component="mold")
        return record

    def require_active(self, mold_id: str) -> MoldRecord:
        """要求模具已登记且未报废，否则拒绝下游动作。"""

        record = self.resolve(mold_id)
        if not record.is_active():
            raise MoldError(f"模具 {mold_id} 已报废，禁止投用", component="mold")
        return record

    def retire(self, mold_id: str, reason: str = "模具检修") -> MoldRecord:
        """把模具标记为报废。"""

        with self._lock:
            record = self._records.get(mold_id)
            if record is None:
                raise MoldError(f"模具 {mold_id} 未登记", component="mold")
            retired = MoldRecord(
                mold_id=record.mold_id,
                product_code=record.product_code,
                cavity_count=record.cavity_count,
                generation=record.generation,
                registered_at=record.registered_at,
                status=RETIRED,
            )
            self._records[mold_id] = retired
        return retired

    def active(self) -> list[MoldRecord]:
        with self._lock:
            records = list(self._records.values())
        return sorted(
            (record for record in records if record.is_active()),
            key=lambda item: item.mold_id,
        )

    def describe(self) -> list[dict]:
        with self._lock:
            records = sorted(self._records.values(), key=lambda item: item.mold_id)
        return [record.as_dict() for record in records]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                mold_id: record.as_dict() for mold_id, record in sorted(self._records.items())
            }
