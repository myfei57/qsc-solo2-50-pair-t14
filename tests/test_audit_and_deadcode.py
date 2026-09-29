"""审计留存与死代码门禁。"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from rubbercure.app import CycleInputs
from rubbercure.errors import InterlockError
from rubbercure.eventbus import Event

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_alarm_entries_are_captured(platform):
    platform.run_cure("mold-A", cavity_count=2)
    platform.press("press-01").cooling.measure(60.0, 70.0)
    assert platform.drain_events(timeout_s=2.0)
    alarms = platform.audit.alarms()
    assert alarms
    assert any(item["severity"] == "alarm" for item in alarms)
    assert platform.audit.entries(limit=5)
    tail = platform.audit.tail(3)
    assert tail[0]["ts"] >= tail[-1]["ts"]
    assert platform.audit.replay_from_journal()[0]["component"]
    assert platform.audit.replay_segment(0)


def test_platform_error_is_recorded_with_component(platform):
    unit = platform.press("press-02")
    try:
        unit.require_mold()
    except InterlockError as error:
        record = platform.note_error(error)
    assert record["component"] == "press"
    assert record["action"] == "error"
    assert record["severity"] == "warning"


def test_events_are_published_to_subscribers(platform):
    seen: list[str] = []
    # 用同步订阅保证断言时事件已经投递；生产订阅默认走异步隔离邮箱。
    unsubscribe = platform.bus.subscribe(
        "steam", lambda event: seen.append(event.action), queue_size=0
    )
    platform.press("press-01").steam.open_steam(platform.config.steam.target_pressure_kpa)
    assert "trap_opened" in seen and "steam_opened" in seen
    unsubscribe()
    assert platform.bus.history("steam")


def test_subscriber_failure_is_isolated_and_raised_as_alarm(platform):
    """订阅者抛错/堆积只影响自己：发布方不卡，其它订阅者照收，故障进审计。"""

    def boom(event: Event) -> None:
        raise RuntimeError("告警处理器炸了")

    release = threading.Event()
    started = threading.Event()

    def stuck(event: Event) -> None:
        if not started.is_set():
            started.set()
            release.wait(timeout=5.0)

    unsub_bad = platform.bus.subscribe("cool", boom, name="cool-buggy", queue_size=8)
    unsub_stuck = platform.bus.subscribe(
        "cool", stuck, name="cool-stuck", queue_size=4, enqueue_timeout_s=0.0
    )

    # 发布方连发大量事件必须快速返回，不能被卡死的订阅者拖住。
    for index in range(60):
        platform.bus.publish(Event.of("cool", "cool", "probe", severity="info", seq=index))

    # 控制循环照常推进。
    cycle = platform.cycle(CycleInputs(inlet_c=26.0, steam_target_kpa=100.0, dt=1.0))
    assert len(cycle["presses"]) == platform.fleet.size

    release.set()
    assert platform.drain_events(timeout_s=2.0)

    health = {item["name"]: item for item in platform.bus.health()}
    assert health["cool-buggy"]["errors"] >= 1
    assert health["cool-stuck"]["dropped"] >= 1
    unsub_bad()
    unsub_stuck()

    alarms = platform.audit.alarms()
    actions = {item["action"] for item in alarms if item["component"] == "eventbus"}
    subjects = {item["subject"] for item in alarms if item["component"] == "eventbus"}
    assert "subscriber_error" in actions
    assert "subscriber_overflow" in actions
    assert "cool-buggy" in subjects
    assert "cool-stuck" in subjects


def test_deadcode_gate_reports_no_unused_symbols():
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(ROOT, "tools", "deadcode.py"),
            os.path.join(ROOT, "rubbercure"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"unused_symbols": []' in result.stdout
    assert '"unreachable_modules": []' in result.stdout
