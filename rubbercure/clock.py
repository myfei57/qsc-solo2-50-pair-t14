"""时钟抽象。

生产环境使用 :class:`SystemClock`（真实挂钟 + 单调时钟）；校验、复现和
单元验证使用 :class:`ManualClock`，让升温曲线、硫化计时和冷却延时在不真实
等待的前提下推进。凡是被联锁读取的时长（模温确认保持、泄压保持、冷却
排放延时）都必须能被显式推进，否则顺序约束在验证环境里根本观察不到。
"""

from __future__ import annotations

import time


class Clock:
    """时钟基类，只实现各实现共享的格式化逻辑。"""

    def now(self) -> float:
        """返回 Unix 时间戳（秒）。"""

        raise NotImplementedError

    def monotonic(self) -> float:
        """返回单调递增秒数，用于测量间隔。"""

        raise NotImplementedError

    def sleep(self, seconds: float) -> None:
        """等待给定秒数。"""

        raise NotImplementedError

    def utc_iso(self) -> str:
        """把当前时间格式化成审计日志使用的时间戳。"""

        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now()))


class SystemClock(Clock):
    """真实时钟。"""

    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class ManualClock(Clock):
    """可手动推进的时钟，``sleep`` 直接推进自身时间。"""

    def __init__(self, start: float = 1_760_000_000.0) -> None:
        self._now = float(start)
        self._mono = 0.0

    def now(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> float:
        """把两个时间轴同时推进 ``seconds`` 秒，返回推进后的挂钟时间。"""

        if seconds < 0:
            raise ValueError("时钟不能回退")
        self._now += seconds
        self._mono += seconds
        return self._now
