"""模温升温曲线规划。

从当前模温升到工艺目标需要按升温速率分段推进，停机时也要按同样方式退出。
把过程抽象成可枚举的分段，控制器在每段之间落盘中间状态；验证环境里也能
在不真实等待的前提下走完整条曲线，并保留采样轨迹供审计比对。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..clock import Clock


@dataclass(frozen=True)
class WarmupPlan:
    """一段线性升温曲线。"""

    start_c: float
    target_c: float
    rate_c_per_min: float

    def __post_init__(self) -> None:
        if self.rate_c_per_min <= 0:
            raise ValueError("升温速率必须为正")

    @property
    def duration_s(self) -> float:
        return abs(self.target_c - self.start_c) / self.rate_c_per_min * 60.0

    def value_at(self, elapsed_s: float) -> float:
        """返回曲线上 ``elapsed_s`` 时刻的模温。"""

        if elapsed_s <= 0:
            return self.start_c
        if elapsed_s >= self.duration_s:
            return self.target_c
        progress = elapsed_s / self.duration_s
        return self.start_c + (self.target_c - self.start_c) * progress

    def steps(self, step_s: float) -> list[float]:
        """把曲线切成不超过 ``step_s`` 的采样时刻（含 0 与终点）。"""

        if step_s <= 0:
            raise ValueError("分段步长必须为正")
        marks = [0.0]
        elapsed = 0.0
        while elapsed < self.duration_s:
            elapsed = min(elapsed + step_s, self.duration_s)
            marks.append(elapsed)
        return marks

    def describe(self) -> dict:
        return {
            "start_c": round(self.start_c, 3),
            "target_c": round(self.target_c, 3),
            "rate_c_per_min": self.rate_c_per_min,
            "duration_s": round(self.duration_s, 3),
        }


def plan_warmup(start_c: float, target_c: float, rate_c_per_min: float) -> WarmupPlan:
    """按当前模温与目标模温规划升温曲线。"""

    return WarmupPlan(
        start_c=float(start_c),
        target_c=float(target_c),
        rate_c_per_min=float(rate_c_per_min),
    )


def execute_warmup(
    plan: WarmupPlan,
    clock: Clock,
    step_s: float,
    on_step: object = None,
) -> float:
    """按分段推进曲线，返回终点温度。

    ``on_step`` 是可选回调，签名为 ``(elapsed_s, value_c)``；模温回路用它
    记录中间轨迹，审计据此刻画升温过程。
    """

    marks = plan.steps(step_s)
    previous = marks[0]
    for mark in marks[1:]:
        clock.sleep(mark - previous)
        previous = mark
        if callable(on_step):
            on_step(mark, plan.value_at(mark))
    return plan.value_at(marks[-1])


@dataclass
class WarmupTrace:
    """升温曲线执行轨迹。"""

    points: list[tuple[float, float]] = field(default_factory=list)

    def note(self, elapsed_s: float, value_c: float) -> None:
        self.points.append((round(elapsed_s, 3), round(value_c, 3)))

    def peak_c(self) -> float:
        return max((value for _elapsed, value in self.points), default=0.0)

    def as_dict(self) -> dict:
        return {"points": [list(point) for point in self.points], "peak_c": self.peak_c()}
