"""模具台账、传感器映射与曲线基线都按最新代际解析。"""

from __future__ import annotations

import pytest

from rubbercure.errors import MoldError
from rubbercure.mold.mapping import SensorMapBook
from rubbercure.mold.registry import MoldRegistry
from rubbercure.mold.summary import summarize_molds
from rubbercure.temp.read import read_mold_temp
from rubbercure.timer.curve import CurveBook


def test_mold_registry_lifecycle():
    registry = MoldRegistry()
    record = registry.register("m1", "RB-215", 4, 100.0)
    assert record.generation == 1
    assert registry.require_active("m1").cavity_count == 4
    registry.register("m1", "RB-215", 6, 200.0)
    assert registry.resolve("m1").cavity_count == 6
    assert registry.resolve("m1").generation == 2
    registry.retire("m1", "检修报废")
    assert not registry.resolve("m1").is_active()
    with pytest.raises(MoldError):
        registry.require_active("m1")
    with pytest.raises(MoldError):
        registry.resolve("missing")
    assert registry.active() == []


def test_sensor_map_resolves_latest_generation():
    book = SensorMapBook()
    book.register("m2", {0: "s0-a", 1: "s1-a"}, 10.0)
    second = book.register("m2", {0: "s0-b", 1: "s1-b"}, 20.0)
    assert second.generation == 2
    assert book.latest_for("m2").generation == 2
    assert book.sensor_for("m2", 0) == "s0-b"
    assert [item.generation for item in book.history("m2")] == [1, 2]
    assert book.known_molds() == ["m2"]
    with pytest.raises(MoldError):
        book.latest_for("unknown")
    with pytest.raises(MoldError):
        book.register("m3", {}, 30.0)


def test_read_mold_temp_maps_cavities_through_mapping():
    book = SensorMapBook()
    mapping = book.register("m4", {0: "c0", 1: "c1"}, 5.0)
    reading = read_mold_temp("m4", mapping, {"c0": 150.0, "c1": 152.0}, 6.0)
    assert reading.by_cavity == {0: 150.0, 1: 152.0}
    assert reading.average_c() == 151.0
    assert reading.spread_c() == 2.0
    assert reading.minimum_c() == 150.0
    assert reading.maximum_c() == 152.0
    with pytest.raises(MoldError):
        read_mold_temp("m4", mapping, {"c0": 150.0}, 6.0)
    with pytest.raises(MoldError):
        read_mold_temp("other", mapping, {"c0": 150.0, "c1": 151.0}, 6.0)


def test_curve_book_uses_latest_generation_for_baseline():
    book = CurveBook()
    book.register("RB-215", 600.0, 1.0)
    book.register("RB-215", 720.0, 2.0)
    assert book.latest_for("RB-215").generation == 2
    assert book.baseline("RB-215", 6.0) == 720.0
    assert book.baseline("RB-215", 12.0) > 720.0
    with pytest.raises(Exception):
        book.latest_for("nope")


def test_mold_summary_groups_active_and_retired():
    registry = MoldRegistry()
    registry.register("a", "RB-215", 4, 1.0)
    registry.register("b", "RB-215", 4, 2.0)
    registry.register("c", "RB-310", 2, 3.0)
    registry.retire("c")
    summary = summarize_molds(registry)
    assert summary["total"] == 3
    assert summary["active"] == 2
    assert summary["retired"] == 1
    assert summary["by_product"]["RB-215"]["active"] == 2
