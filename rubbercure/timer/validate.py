"""硫化进度校验。

计时结束时按计划时长给出三种判定：提前结束（欠硫）、落在窗口内（合格）、
超时（过硫）。判定只读时长，不改状态；结果直接进审计，供工艺追溯。
"""

from __future__ import annotations

from ..config import TimerLimits


def validate_progress(elapsed_s: float, planned_s: float, limits: TimerLimits) -> dict:
    """按计划时长与工艺窗口判定硫化进度。"""

    if planned_s <= 0:
        raise ValueError("计划硫化时长必须为正")
    ratio = float(elapsed_s) / float(planned_s)
    if elapsed_s < planned_s * (1.0 - limits.shortfall_ratio):
        verdict = "short"
    elif elapsed_s > planned_s * (1.0 + limits.overrun_ratio):
        verdict = "over"
    else:
        verdict = "ok"
    return {
        "elapsed_s": round(float(elapsed_s), 3),
        "planned_s": round(float(planned_s), 3),
        "ratio": round(ratio, 4),
        "verdict": verdict,
        "within_window": verdict == "ok",
    }
