"""模具与传感器的映射登记簿。

一份映射记录某台模具在第 ``generation`` 代际上、每个腔位对应哪一个温度
传感器。映射可以反复登记（修模、换探头、增加腔位），读取方一律按最新
代际解析。模温读取、合模判定和硫化曲线比对都从这里取腔位对应的传感器，
所以"用哪一代"必须是显式的、可审计的，不能靠默认第一个登记。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..errors import MoldError
from .generation import GenerationCounter


@dataclass(frozen=True)
class SensorMapping:
    """某一代际下，一台模具的全部腔位传感器映射。"""

    mold_id: str
    generation: int
    registered_at: float
    sensors: tuple[tuple[int, str], ...] = field(default_factory=tuple)

    def sensor_for(self, cavity: int) -> str:
        """返回指定腔位的传感器编号。"""

        for index, sensor in self.sensors:
            if index == cavity:
                return sensor
        raise MoldError(
            f"模具 {self.mold_id} 第 {cavity} 腔在第 {self.generation} 代际没有映射",
            component="mold",
        )

    def as_dict(self) -> dict:
        return {
            "mold_id": self.mold_id,
            "generation": self.generation,
            "registered_at": round(self.registered_at, 3),
            "sensors": {str(cavity): sensor for cavity, sensor in self.sensors},
        }


class SensorMapBook:
    """按模具保存全部代际的传感器映射，读取时取最新代际。"""

    def __init__(self, counter: GenerationCounter | None = None) -> None:
        self._counter = counter or GenerationCounter()
        self._by_mold: dict[str, list[SensorMapping]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        mold_id: str,
        sensors: dict[int, str],
        registered_at: float,
    ) -> SensorMapping:
        """登记一份新代际映射，返回落库后的记录。"""

        if not mold_id:
            raise MoldError("模具编号不能为空", component="mold")
        if not sensors:
            raise MoldError(f"模具 {mold_id} 的映射不能为空", component="mold")
        pairs: list[tuple[int, str]] = []
        for cavity in sorted(sensors):
            sensor = sensors[cavity]
            if not isinstance(cavity, int) or cavity < 0:
                raise MoldError(f"腔位编号非法: {cavity!r}", component="mold")
            if not sensor:
                raise MoldError(f"模具 {mold_id} 第 {cavity} 腔缺少传感器编号", component="mold")
            pairs.append((cavity, str(sensor)))
        with self._lock:
            generation = self._counter.next()
            mapping = SensorMapping(
                mold_id=mold_id,
                generation=generation,
                registered_at=float(registered_at),
                sensors=tuple(pairs),
            )
            self._by_mold.setdefault(mold_id, []).append(mapping)
        return mapping

    def latest_for(self, mold_id: str) -> SensorMapping:
        """返回该模具代际最新的映射。"""

        with self._lock:
            history = list(self._by_mold.get(mold_id, ()))
        if not history:
            raise MoldError(f"模具 {mold_id} 尚未登记传感器映射", component="mold")
        return max(history, key=lambda item: item.generation)

    def history(self, mold_id: str) -> list[SensorMapping]:
        """返回该模具的全部代际映射，按代际升序。"""

        with self._lock:
            history = list(self._by_mold.get(mold_id, ()))
        return sorted(history, key=lambda item: item.generation)

    def sensor_for(self, mold_id: str, cavity: int) -> str:
        """按最新代际解析腔位对应的传感器。"""

        return self.latest_for(mold_id).sensor_for(cavity)

    def known_molds(self) -> list[str]:
        with self._lock:
            return sorted(self._by_mold)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                mold_id: [mapping.as_dict() for mapping in history]
                for mold_id, history in sorted(self._by_mold.items())
            }
