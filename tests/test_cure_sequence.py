"""硫化顺序：模具登记、排气、合模、升温、确认、计时。"""

from __future__ import annotations

import pytest

from rubbercure.errors import InterlockError
from rubbercure.lift.close import close_mold
from rubbercure.steam.purge import PurgeToken
from rubbercure.store.jsonfile import JsonStore
from rubbercure.temp.controller import TempController
from rubbercure.temp.persist import TempPersister
from rubbercure.temp.read import MoldTempReading
from rubbercure.timer.start import start_cure


def test_cure_sequence_runs_steps_in_order(platform):
    report = platform.run_cure("mold-A", cavity_count=4)
    assert report["steps"] == [
        "mold_registered",
        "mapping_registered",
        "cavity_purge",
        "lift_close",
        "steam_open",
        "temp_confirm",
        "timer_start",
    ]
    assert report["violations"] == []
    assert report["topology_missing"] == []
    assert report["timer"]["phase"] == "curing"
    assert report["timer"]["planned_s"] > 0


def test_purge_generation_matches_registered_mapping(platform):
    platform.run_cure("mold-B", cavity_count=3)
    mapping = platform.maps.latest_for("mold-B")
    assert len(mapping.sensors) == 3
    token = platform.press("press-01").steam.purge_token()
    assert token is not None
    assert token.generation == mapping.generation


def test_close_mold_rejects_invalid_purge_token(platform):
    unit = platform.press("press-01")
    token = PurgeToken(
        mold_id="mold-X",
        generation=1,
        purged_at=0.0,
        hold_s=20.0,
        min_hold_s=20.0,
        valid=False,
    )
    with pytest.raises(InterlockError):
        close_mold(unit.lift, token, platform.bus, "mold-X")


def test_close_mold_rejects_stale_purge_token(platform):
    unit = platform.press("press-01")
    token = PurgeToken(
        mold_id="mold-Y",
        generation=1,
        purged_at=platform.clock.now(),
        hold_s=20.0,
        min_hold_s=20.0,
    )
    assert not token.is_stale(platform.clock.now(), 180.0)
    with pytest.raises(InterlockError):
        close_mold(
            unit.lift,
            token,
            platform.bus,
            "mold-Y",
            now=platform.clock.now() + 3600.0,
            max_age_s=180.0,
        )


def test_steam_opens_drain_trap_before_main_valve(platform):
    unit = platform.press("press-01")
    result = unit.steam.open_steam(platform.config.steam.target_pressure_kpa)
    assert result["order"] == ["trap_open", "steam_valve_open"]
    assert unit.steam.trap.is_open()
    assert unit.steam.valve.is_open()
    assert unit.steam.phase.value == "heating"


def test_timer_refuses_to_start_without_durable_temp_confirmation(tmp_path, clock, platform):
    store = JsonStore(str(tmp_path / "isolated"))
    persister = TempPersister(clock, platform.bus, store, platform.config.temp)
    timer = platform.press("press-02").timer
    with pytest.raises(InterlockError):
        start_cure(timer, persister, "mold-Z", 600.0, platform.bus)


def test_timer_starts_once_confirmation_is_durable(platform, tmp_path, clock):
    unit = platform.press("press-03")
    reading = MoldTempReading(
        mold_id="mold-D",
        generation=1,
        sampled_at=clock.now(),
        by_cavity={0: platform.config.temp.setpoint_c, 1: platform.config.temp.setpoint_c},
    )
    unit.temp_persister.confirm(reading, platform.config.temp.confirm_hold_s)
    assert unit.temp_loop.is_confirmed()
    started = start_cure(
        unit.timer,
        unit.temp_persister,
        "mold-D",
        600.0,
        platform.bus,
        product_code="RB-215",
        generation=1,
    )
    assert started["planned_s"] == 600.0
    assert unit.timer.phase.value == "curing"
    assert isinstance(TempController(kp=1.0, ki=0.1, output_min=-1.0, output_max=1.0).snapshot(), dict)
