"""审计留存与死代码门禁。"""

from __future__ import annotations

import os
import subprocess
import sys

from rubbercure.errors import InterlockError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_alarm_entries_are_captured(platform):
    platform.run_cure("mold-A", cavity_count=2)
    platform.press("press-01").cooling.measure(60.0, 70.0)
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
    unsubscribe = platform.bus.subscribe("steam", lambda event: seen.append(event.action))
    platform.press("press-01").steam.open_steam(platform.config.steam.target_pressure_kpa)
    assert "trap_opened" in seen and "steam_opened" in seen
    unsubscribe()
    assert platform.bus.history("steam")


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
