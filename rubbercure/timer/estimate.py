"""硫化剩余时间估算。

控制台与调度都需要"还剩多久"：调度据此安排下一模就位，控制台据此画进度。
估算只依赖已执行时长与计划时长，不改变任何状态；进度比例被夹在 [0, 1]，
过硫（超时）由校验函数单独判定，不能靠进度条吞掉。
"""

from __future__ import annotations


def progress_ratio(elapsed_s: float, planned_s: float) -> float:
    """返回已执行比例，夹在 [0, 1]。"""

    if planned_s <= 0:
        raise ValueError("计划硫化时长必须为正")
    return max(0.0, min(1.0, float(elapsed_s) / float(planned_s)))


def estimate_remaining(elapsed_s: float, planned_s: float) -> float:
    """返回剩余秒数，已超时返回 0。"""

    if planned_s <= 0:
        raise ValueError("计划硫化时长必须为正")
    return max(0.0, float(planned_s) - float(elapsed_s))


def estimate_snapshot(elapsed_s: float, planned_s: float) -> dict:
    """返回一条估算摘要。"""

    return {
        "elapsed_s": round(float(elapsed_s), 3),
        "planned_s": round(float(planned_s), 3),
        "remaining_s": round(estimate_remaining(elapsed_s, planned_s), 3),
        "progress": round(progress_ratio(elapsed_s, planned_s), 4),
    }
