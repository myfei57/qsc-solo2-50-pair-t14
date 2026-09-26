"""蒸汽与开合模：排气、升温、泄压、开模的硬顺序。"""

from __future__ import annotations

import pytest

from rubbercure.errors import InterlockError, SequenceError
from rubbercure.lift.open import open_mold
from rubbercure.lift.position import ClampPosition
from rubbercure.lift.state import LiftPhase, LiftStateMachine
from rubbercure.steam.vent import SAFE_RESIDUAL_KPA, VentToken


def test_vent_before_open_is_enforced(platform):
    unit = platform.press("press-01")
    unit.load_mold("mold-V")
    unit.lift.close_clamp("mold-V")
    unsafe = VentToken(
        mold_id="mold-V",
        residual_kpa=200.0,
        vented_at=platform.clock.now(),
        safe_below_kpa=SAFE_RESIDUAL_KPA,
        hold_s=8.0,
    )
    assert not unsafe.is_safe()
    with pytest.raises(InterlockError):
        open_mold(unit.lift, unsafe, platform.bus, "mold-V")


def test_vent_drops_pressure_below_safe_threshold(platform):
    unit = platform.press("press-01")
    unit.steam.header.charge(900.0, 30.0)
    assert unit.steam.pressure_kpa > SAFE_RESIDUAL_KPA
    token = unit.steam.vent_pressure("mold-V")
    assert token.is_safe()
    assert unit.steam.pressure_kpa <= SAFE_RESIDUAL_KPA
    assert unit.steam.vent_token() is token


def test_purge_requires_main_valve_closed(platform):
    unit = platform.press("press-01")
    unit.steam.set_valve(60.0)
    with pytest.raises(InterlockError):
        unit.steam.purge_cavity("mold-P", generation=1)


def test_clamp_position_never_overshoots_target():
    position = ClampPosition(100.0)
    steps = []
    while not position.is_closed():
        position = position.approach(0.0, 30.0)
        steps.append(position.opening_pct)
    assert steps[-1] == 0.0
    assert steps == sorted(steps, reverse=True)
    assert ClampPosition(0.0).is_closed()
    assert ClampPosition(100.0).is_open()


def test_clamp_position_rejects_out_of_range():
    with pytest.raises(ValueError):
        ClampPosition(140.0)


def test_lift_state_machine_rejects_illegal_transition():
    machine = LiftStateMachine()
    assert machine.phase is LiftPhase.OPEN
    with pytest.raises(SequenceError):
        machine.transition(LiftPhase.OPENING)
    machine.transition(LiftPhase.CLOSING)
    assert machine.history() == ["open", "closing"]
