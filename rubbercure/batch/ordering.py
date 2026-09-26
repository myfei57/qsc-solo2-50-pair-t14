"""完成上报排序。

多台硫化机在不同线程里并发上报"本模完成"，调度必须按可复现的顺序处理，
否则同一次运行两次排序结果不同，下一模分配会漂移。排序键固定为完成时刻、
硫化机编号、上报序号：完成时刻相同就按机号，机号相同就按上报序号，任何
一次比较都不会读到"当前线程先跑完"这种不可复现的事实。
"""

from __future__ import annotations

from .report import CompletionReport


def order_completions(
    reports: list[CompletionReport], window_s: float | None = None
) -> list[CompletionReport]:
    """把完成上报排成可复现的顺序。"""

    selected = list(reports)
    if window_s is not None and window_s > 0:
        if not selected:
            return []
        latest = max(report.completed_at for report in selected)
        selected = [
            report for report in selected if latest - report.completed_at <= window_s
        ]
    return sorted(
        selected,
        key=lambda report: (report.completed_at, report.press_id, report.sequence),
    )


def ordering_keys(reports: list[CompletionReport]) -> list[tuple]:
    """返回排序键序列，供审计与测试对照。"""

    return [
        (round(report.completed_at, 3), report.press_id, report.sequence)
        for report in order_completions(reports)
    ]
