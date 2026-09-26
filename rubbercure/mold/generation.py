"""登记代际计数。

模具与传感器映射允许重复登记：换模、修模、换传感器都会重新上报一份映射。
每一份登记都带一个单调递增的代际号，读取方按"代际最大者胜出"解析，绝不
按写入顺序或字典遍历顺序取值——否则换模之后读到旧代际映射，模温就会被
算错腔位，硫化出来的制品不是气泡就是过硫。
"""

from __future__ import annotations

import threading


class GenerationCounter:
    """线程安全地分配单调递增的代际号。"""

    def __init__(self, start: int = 0) -> None:
        self._value = int(start)
        self._lock = threading.RLock()

    def next(self) -> int:
        """分配下一个代际号并返回。"""

        with self._lock:
            self._value += 1
            return self._value

    def current(self) -> int:
        with self._lock:
            return self._value
