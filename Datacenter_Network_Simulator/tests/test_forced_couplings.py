"""S0-9 / S1-10 (doc 32 §9.2, §14): forced alarms whose coupling targets a
store-published point. A forced CRAH Alarm_HighTemp reaches the wire and the
room; a forced CDU Alarm_HighSupplyTemp reaches the wire and the servers on its
cold-plate loop.

The engine's +6 K landed on Supply_Air_Temp, which the store overwrites every
tick, so the forced bit was the only tell and the room never warmed — CR-1's
abrupt ground truth depended on a coupling that did not exist. It is now applied
store-side on the engine's severity ramp, and the CHW valve drives to 100.
"""
from __future__ import annotations

import logging

import pytest

from test_fault_publish_truth import _rig


SP = 22.0


def _room_supply(r):
    return r.store._room_supply_temp(r.servers()[0])


def _wire(r, name, point="Supply_Air_Temp"):
    return r.bac[name].get_snapshot()[point]


@pytest.fixture
def hall(tmp_path):
    # Seven units, uniform air: room supply is the mean of the running units'
    # published discharge, so one unit at +6 K lifts it by 6/7 K.
    r = _rig(tmp_path, n_crah=7, mode="uniform", probes=False)
    r.tick(200)
    return r


def test_forced_hightemp_reaches_the_wire_and_warms_the_room(hall):
    r = hall
    name = r.crah(1).name
    pen = r.store._chw_pen.get("DC1", 0.0)
    room0 = _room_supply(r)
    sa0 = _wire(r, name)
    # The discharge loop ripples a few tenths round setpoint (sigma ~0.1 C).
    assert sa0 == pytest.approx(SP + pen, abs=0.5)
    r.store.plant_alarm_overrides.setdefault(name, {})["Alarm_HighTemp"] = 1.0
    r.tick(60)                                         # within 60 s
    assert _wire(r, name) >= SP + pen + 5.5
    assert _wire(r, name, "CHW_Valve") == 100.0        # the coil is starving
    assert _wire(r, name, "Alarm_HighTemp") == 1.0
    assert _room_supply(r) - room0 == pytest.approx(6.0 / 7.0, abs=0.15)
    # Clearing restores the previous state.
    r.store.plant_alarm_overrides.pop(name, None)
    r.tick(5)
    assert _wire(r, name) == pytest.approx(sa0, abs=0.5)   # control ripple, both ends
    assert _room_supply(r) == pytest.approx(room0, abs=0.1)
    assert _wire(r, name, "CHW_Valve") < 100.0


def test_severity_ramps_like_the_engine(hall):
    r = hall
    name = r.crah(2).name
    pen = r.store._chw_pen.get("DC1", 0.0)
    r.store.plant_alarm_overrides.setdefault(name, {})["Alarm_HighTemp"] = 1.0
    r.tick(1)
    first = _wire(r, name) - (SP + pen)
    assert 6.0 * 0.25 - 0.2 <= first <= 6.0 * 0.30 + 0.2   # 0.25 + 1/60
    r.tick(30)
    mid = _wire(r, name) - (SP + pen)
    assert first < mid < 6.0


def test_a_limits_lock_forces_it_too(hall):
    r = hall
    name = r.crah(3).name
    pen = r.store._chw_pen.get("DC1", 0.0)
    r.store.metric_limits["crah:Alarm_HighTemp"] = {"enabled": True, "lock": "on"}
    try:
        r.tick(60)
        assert _wire(r, name) >= SP + pen + 5.5
    finally:
        r.store.metric_limits.pop("crah:Alarm_HighTemp", None)


def test_a_stopped_unit_is_not_charged(hall):
    """A unit that is off has no discharge to raise."""
    r = hall
    name = r.crah(4).name
    r.store.plant_alarm_overrides.setdefault(name, {}).update(
        {"Alarm_HighTemp": 1.0, "Unit_Running": 0.0})
    r.tick(10)
    assert "Supply_Air_Temp" not in r.store._plant_auto_points.get(name, {})


def test_lost_couplings_are_logged_once(hall, caplog):
    r = hall
    name = r.crah(5).name
    with caplog.at_level(logging.WARNING, logger="core.device_state_store"):
        r.store.plant_alarm_overrides.setdefault(name, {})["Alarm_HighReturnAir"] = 1.0
        r.tick(5)
        r.store.plant_alarm_overrides[name]["Alarm_HighTemp"] = 1.0     # fixed store-side
        r.tick(3)
    lost = [x.getMessage() for x in caplog.records if "does not reach the wire" in x.getMessage()]
    assert len(lost) == 1
    assert "Alarm_HighReturnAir" in lost[0] and name in lost[0]
    assert not any("Alarm_HighTemp" in m for m in lost)
    # Released then forced again: logged again (a new episode).
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="core.device_state_store"):
        r.store.plant_alarm_overrides[name].pop("Alarm_HighReturnAir")
        r.tick(2)
        r.store.plant_alarm_overrides[name]["Alarm_HighReturnAir"] = 1.0
        r.tick(2)
    assert sum("does not reach the wire" in x.getMessage() for x in caplog.records) == 1


# ── S1-10: CDU Alarm_HighSupplyTemp ─────────────────────────────────────────

def test_forced_cdu_high_supply_temp_reaches_the_wire_and_the_loop(tmp_path):
    from test_degradation import _rig as _iso_rig
    r = _iso_rig(tmp_path, cdus=1)
    r.tick(150)
    cdu = r.h.made["CDU1"].name
    loop = sorted(r.store._cdu_loop_servers().get(cdu, ()))
    assert loop, "the fixture CDU carries a cold-plate loop"
    dev = {d.name: d for d in r.servers()}
    die0 = sum(dev[n].cpu_temp for n in loop) / len(loop)
    sp = _wire(r, cdu, "TCS_Setpoint")
    tcs0, valve0 = _wire(r, cdu, "TCS_Supply_Temp"), _wire(r, cdu, "Facility_CHW_Valve")
    app0 = _wire(r, cdu, "Approach_Temp")
    r.store.plant_alarm_overrides.setdefault(cdu, {})["Alarm_HighSupplyTemp"] = 1.0
    r.tick(60)
    assert _wire(r, cdu, "Alarm_HighSupplyTemp") == 1.0
    assert _wire(r, cdu, "TCS_Supply_Temp") >= sp + 5.5
    assert _wire(r, cdu, "Facility_CHW_Valve") >= min(100.0, valve0 + 24.0)
    assert _wire(r, cdu, "Approach_Temp") == pytest.approx(app0 + 3.0, abs=0.3)
    # The servers on that loop take the warmer coolant (die tau ~150 s).
    r.tick(240)
    die1 = sum(dev[n].cpu_temp for n in loop) / len(loop)
    assert die1 > die0 + 2.0
    r.store.plant_alarm_overrides.pop(cdu, None)
    r.tick(3)
    assert _wire(r, cdu, "TCS_Supply_Temp") == pytest.approx(tcs0, abs=0.5)
    assert r.store._forced_t == {}


def test_a_healthy_crah_supply_ripples_round_setpoint_not_on_it(hall):
    """A real discharge loop hunts a little round setpoint: an hour at exactly
    22.0 is a flatline no sensor gives, and DQ-5 rightly flags it as stuck."""
    r = hall
    name = r.crah(2).name
    seen = []
    for _ in range(40):
        r.tick(1)
        seen.append(_wire(r, name))
    assert len(set(seen)) > 1, "supply air sat on one value for 40 ticks"
    assert all(abs(v - SP) <= 0.5 for v in seen), "ripple stays a few tenths"
