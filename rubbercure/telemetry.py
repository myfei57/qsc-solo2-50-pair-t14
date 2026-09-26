"""趋势采样与补点。

模温与蒸汽压力的历史趋势必须连续：硫化曲线基线比对、过硫判定和机群调度
都要按时间窗读取趋势。现场总线抖动会丢点，趋势缓冲按固定周期回填（用前
一个有效值），并把补出来的点标记为 ``filled``，审计与曲线比对据此区分
实测点与补齐点。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    """一条趋势采样。"""

    timestamp: float
    value: float
    filled: bool = False

    def as_dict(self) -> dict:
        return {"ts": round(self.timestamp, 3), "value": round(self.value, 4), "filled": self.filled}


class TrendBuffer:
    """按分量维护的定周期趋势缓冲。"""

    def __init__(self, period_s: float, capacity: int = 2048) -> None:
        if period_s <= 0:
            raise ValueError("采样周期必须为正")
        self._period = float(period_s)
        self._capacity = int(capacity)
        self._series: dict[str, list[Sample]] = {}
        self._lock = threading.RLock()

    def series_names(self) -> list[str]:
        with self._lock:
            return sorted(self._series)

    def append(self, name: str, timestamp: float, value: float) -> Sample:
        """追加实测点。"""

        sample = Sample(timestamp=float(timestamp), value=float(value))
        with self._lock:
            series = self._series.setdefault(name, [])
            series.append(sample)
            if len(series) > self._capacity:
                del series[: len(series) - self._capacity]
        return sample

    def fill_gaps(self, name: str) -> int:
        """在缺失采样周期的位置补点，返回补出的点数。"""

        with self._lock:
            series = self._series.get(name)
            if not series or len(series) < 2:
                return 0
            filled: list[Sample] = [series[0]]
            for current in series[1:]:
                previous = filled[-1]
                gap = current.timestamp - previous.timestamp
                steps = int(round(gap / self._period)) - 1
                for step in range(1, steps + 1):
                    filled.append(
                        Sample(
                            timestamp=previous.timestamp + step * self._period,
                            value=previous.value,
                            filled=True,
                        )
                    )
                filled.append(current)
            added = len(filled) - len(series)
            self._series[name] = filled[-self._capacity :]
        return added

    def window(self, name: str, start: float, end: float) -> list[Sample]:
        """返回 ``[start, end]`` 区间内的采样。"""

        with self._lock:
            series = list(self._series.get(name, ()))
        return [sample for sample in series if start <= sample.timestamp <= end]

    def latest(self, name: str) -> Sample | None:
        with self._lock:
            series = self._series.get(name)
            return series[-1] if series else None

    def average(self, name: str, start: float, end: float) -> float:
        """区间均值；区间内无点返回 0.0。"""

        samples = self.window(name, start, end)
        if not samples:
            return 0.0
        return sum(sample.value for sample in samples) / len(samples)

    def snapshot(self) -> dict:
        """转成控制台趋势接口使用的结构。"""

        with self._lock:
            return {
                name: [sample.as_dict() for sample in samples]
                for name, samples in self._series.items()
            }
