"""模温容差判定。

模温是否合格由目标值和容差共同决定，且不只判平均温度：多腔模具还要判
腔间温差，某一腔偏低同样会欠硫。判定函数是纯函数，便于硫化计时、控制台
与曲线比对各自复用同一套口径。
"""

from __future__ import annotations


def within_band(measured_c: float, setpoint_c: float, tolerance_c: float) -> bool:
    """判定测量值是否落在容差带内。"""

    if tolerance_c < 0:
        raise ValueError("模温容差不能为负")
    return abs(measured_c - setpoint_c) <= tolerance_c


def spread_ok(spread_c: float, max_spread_c: float) -> bool:
    """判定腔间温差是否在允许范围内。"""

    return spread_c <= max_spread_c


def band_summary(measured_c: float, setpoint_c: float, tolerance_c: float) -> dict:
    """返回一条带判定的模温摘要。"""

    deviation = measured_c - setpoint_c
    return {
        "measured_c": round(measured_c, 3),
        "setpoint_c": setpoint_c,
        "deviation_c": round(deviation, 3),
        "in_band": within_band(measured_c, setpoint_c, tolerance_c),
    }
