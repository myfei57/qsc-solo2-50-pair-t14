"""并发写入方：压力目标仲裁与完成上报排序都必须可复现。"""

from __future__ import annotations

import threading

import pytest

from rubbercure.batch.ordering import order_completions, ordering_keys
from rubbercure.batch.report import CompletionReport
from rubbercure.steam.setpoint import SetpointArbiter


def test_setpoint_arbiter_commits_a_single_complete_request():
    arbiter = SetpointArbiter(min_kpa=80.0, max_kpa=760.0, default_kpa=640.0)
    submitted: list[float] = []
    lock = threading.Lock()

    def writer(name: str, target: float, priority: int) -> None:
        for _ in range(200):
            arbiter.request(name, target, priority)
            outcome = arbiter.resolve()
            with lock:
                submitted.append(outcome.target_kpa)

    threads = [
        threading.Thread(target=writer, args=("temp_loop", 658.0, 70)),
        threading.Thread(target=writer, args=("header_loop", 628.0, 60)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(round(value, 3) for value in submitted) <= {628.0, 658.0}
    final = arbiter.resolve()
    assert final.target_kpa in (628.0, 658.0)
    assert final.winner in ("temp_loop", "header_loop", "idle")
    assert arbiter.committed_kpa() in (628.0, 658.0)
    assert arbiter.decisions(5)


def test_order_completions_is_independent_of_input_order():
    reports = [
        CompletionReport("press-03", "m3", "RB-215", 10.0, 1, 3),
        CompletionReport("press-01", "m1", "RB-215", 10.0, 1, 1),
        CompletionReport("press-02", "m2", "RB-215", 10.0, 1, 2),
        CompletionReport("press-01", "m4", "RB-215", 9.0, 1, 0),
    ]
    forward = ordering_keys(reports)
    backward = ordering_keys(list(reversed(reports)))
    assert forward == backward
    assert [item[1] for item in forward] == ["press-01", "press-01", "press-02", "press-03"]
    assert order_completions(reports)[0].mold_id == "m4"


def test_order_completions_window_filters_stale_reports():
    reports = [
        CompletionReport("press-01", "m1", "RB-215", 1.0, 1, 1),
        CompletionReport("press-02", "m2", "RB-215", 100.0, 1, 2),
        CompletionReport("press-03", "m3", "RB-215", 101.0, 1, 3),
    ]
    windowed = order_completions(reports, window_s=5.0)
    assert [item.mold_id for item in windowed] == ["m2", "m3"]


def test_platform_concurrency_probes_are_stable(platform):
    setpoint = platform.concurrency_setpoint_probe(rounds=16)
    assert setpoint["rounds"] == 16
    assert setpoint["distinct_targets"] <= 2
    assert setpoint["committed_kpa"] > 0

    reports = platform.concurrency_report_probe(rounds=6)
    assert reports["stable"] is True
    assert reports["reports"] == 6 * platform.fleet.size


def test_concurrency_probe_rejects_non_positive_rounds(platform):
    with pytest.raises(ValueError):
        platform.concurrency_setpoint_probe(rounds=0)
    with pytest.raises(ValueError):
        platform.concurrency_report_probe(rounds=0)
