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


# ── S2-3: chiller Alarm_HighCHWSupply ────────────────────────────────────────

def _lead(r, dc="DC1"):
    trains = r.store._plant_trains_run.get(dc, [])
    assert trains, "the plant has staged a train on"
    return trains[0]["chiller"]


def test_forced_high_chw_supply_reaches_the_wire_and_the_rooms(hall):
    """The forced bit used to be the only tell: the engine's +3 K on the leaving
    water was overwritten by the loop pass every tick, the CRAHs kept their
    discharge and the room never warmed. Now it is a header shortfall the
    store carries: the supply leaves setpoint, the CRAH discharge follows, the
    room reads that back, and the plant's own alarm latches after its dwell."""
    r = hall
    name = _lead(r)
    sp = _wire(r, name, "CHW_Setpoint")
    room0 = _room_supply(r)
    crah = r.crah(1).name
    sa0 = _wire(r, crah)
    assert _wire(r, name, "CHW_Supply_Temp") == pytest.approx(sp, abs=0.5)
    r.store.plant_alarm_overrides.setdefault(name, {})["Alarm_HighCHWSupply"] = 1.0
    r.tick(60)                                          # the ramp is at 1.0
    assert _wire(r, name, "Alarm_HighCHWSupply") == 1.0
    assert _wire(r, name, "CHW_Supply_Temp") >= sp + 5.5
    assert _wire(r, crah) >= sa0 + 5.5                  # every coil fed warm water
    assert _room_supply(r) - room0 == pytest.approx(6.0, abs=0.3)
    # The plant's own annunciation: held high past the dwell, the header alarm
    # latches on the lead by the loop model itself, not by the operator's map.
    r.tick(int(r.store._CHW_HIGH_S) + 5)
    assert r.store._chw_high_by_dc.get("DC1") in r.store._plant_trains_run["DC1"][0].values()
    # Clearing restores the previous state, and the ramp forgets the episode.
    r.store.plant_alarm_overrides.pop(name, None)
    r.tick(5)
    assert _wire(r, name, "CHW_Supply_Temp") == pytest.approx(sp, abs=0.5)
    assert _wire(r, crah) == pytest.approx(sa0, abs=0.5)
    assert _room_supply(r) == pytest.approx(room0, abs=0.1)
    assert r.store._forced_t == {}


def test_forced_high_chw_supply_ramps_like_the_engine(hall):
    r = hall
    name = _lead(r)
    sp = _wire(r, name, "CHW_Setpoint")
    r.store.plant_alarm_overrides.setdefault(name, {})["Alarm_HighCHWSupply"] = 1.0
    r.tick(1)
    first = _wire(r, name, "CHW_Supply_Temp") - sp
    assert 6.0 * 0.25 - 0.3 <= first <= 6.0 * 0.30 + 0.3
    r.tick(30)
    mid = _wire(r, name, "CHW_Supply_Temp") - sp
    assert first < mid < 6.0
    r.store.plant_alarm_overrides.pop(name, None)


def test_a_standby_chiller_is_not_charged(hall):
    """A machine the BMS has staged off makes no water; forcing its alarm is a
    bad point, not a plant event, and the header does not move."""
    r = hall
    standby = sorted(r.store._plant_standby_names)
    if not standby:
        pytest.skip("this plant runs every train")
    name = standby[0]
    lead = _lead(r)
    sp = _wire(r, lead, "CHW_Setpoint")
    r.store.plant_alarm_overrides.setdefault(name, {})["Alarm_HighCHWSupply"] = 1.0
    r.tick(60)
    assert _wire(r, lead, "CHW_Supply_Temp") == pytest.approx(sp, abs=0.5)
    assert r.store._chw_forced_c.get("DC1", 0.0) == 0.0
    r.store.plant_alarm_overrides.pop(name, None)


def test_forced_high_chw_supply_is_no_longer_logged_as_lost(hall, caplog):
    r = hall
    name = _lead(r)
    with caplog.at_level(logging.WARNING, logger="core.device_state_store"):
        r.store.plant_alarm_overrides.setdefault(name, {})["Alarm_HighCHWSupply"] = 1.0
        r.tick(3)
    assert not any("does not reach the wire" in x.getMessage() for x in caplog.records)
    r.store.plant_alarm_overrides.pop(name, None)


# ── valve Stuck: fail-in-place, then the BMS deviation alarm ─────────────────

def test_a_stuck_header_valve_holds_its_disc_and_alarms_when_the_ask_moves():
    """Told to stop modulating, the disc stands where it was while the command
    keeps following load. The engine raises the actuator fault only after the
    disc has stood short of the command past the BMS dwell."""
    from core.bacnet_plant_generator import (PlantTelemetryEngine, VALVE_DEV_DWELL_S,
                                             VALVE_DEV_DEADBAND)
    e = PlantTelemetryEngine("valve", rated_kw=0.5, seed=7)
    for _ in range(40):
        out = e.tick(1.0, plant_load_frac=0.5, running=True)
    pos0 = out["Position"]
    assert abs(pos0 - out["Commanded_Position"]) < 6.0        # tracking, actuator lag
    # Stuck, same load: nothing moves, nothing alarms.
    for _ in range(int(VALVE_DEV_DWELL_S) + 10):
        out = e.tick(1.0, plant_load_frac=0.5, running=False)
    assert out["Position"] == pos0
    assert out.get("Alarm_ActuatorFault", 0.0) == 0.0
    # The load rises: the command walks away from the frozen disc.
    for _ in range(20):
        out = e.tick(1.0, plant_load_frac=0.95, running=False)
    assert out["Position"] == pos0
    assert (out["Commanded_Position"] - pos0) / out["Commanded_Position"] > VALVE_DEV_DEADBAND
    assert out.get("Alarm_ActuatorFault", 0.0) == 0.0          # dwell not run yet
    for _ in range(int(VALVE_DEV_DWELL_S)):
        out = e.tick(1.0, plant_load_frac=0.95, running=False)
    assert out["Alarm_ActuatorFault"] == 1.0
    # Freed: the disc tracks again and the alarm goes with the deviation.
    for _ in range(40):
        out = e.tick(1.0, plant_load_frac=0.95, running=True)
    assert abs(out["Position"] - out["Commanded_Position"]) < 6.0
    assert out.get("Alarm_ActuatorFault", 0.0) == 0.0


def test_a_stuck_header_valve_is_frozen_on_the_wire(tmp_path):
    from test_fault_publish_truth import _attach_bacnet, _clear_caches, _lay_out, Rig, SEED
    import random
    from conftest import build_plant
    _clear_caches(); random.seed(SEED)
    h = build_plant(tmp_path, crahs=4, servers=48, probes=False, valves=True)
    _lay_out(h, 4); h.store._air_mode = "uniform"
    ctrl, bac = _attach_bacnet(h, tmp_path)
    r = Rig(h=h, store=h.store, ctrl=ctrl, bac=bac)
    r.tick(120)
    v = next(d.name for d in h.dm.get_all_devices() if d.name.startswith("VCHW"))
    r.store.plant_alarm_overrides.setdefault(v, {})["Status_Modulating"] = 0.0
    r.tick(2)
    pos = _wire(r, v, "Position")
    r.tick(40)
    assert _wire(r, v, "Position") == pos                       # the disc stood still
    assert _wire(r, v, "Status_Modulating") == 0.0
    assert r.store._is_faulted(v)                               # the store knows
    r.store.plant_alarm_overrides.pop(v, None)
    r.tick(40)
    assert _wire(r, v, "Position") != pos                        # modulating again


# ── pump Flow Proof Lost: the dwell, then the stop ───────────────────────────

def test_flow_proof_lost_runs_the_flow_down_before_the_pump_stops(hall):
    """The flow switch opens: the alarm is on the wire at once, the flow runs
    down over the proof dwell with the drive still at speed, and only when the
    dwell runs out does the BMS call the pump failed, stop it and re-stage."""
    r = hall
    dc = "DC1"
    pump = next(p for p in sorted(n for n in r.store._cooling_context()["plant_by_dc"][dc]["pump"])
                if p.startswith("CHWP") and p not in r.store._plant_standby_names)
    r.tick(5)
    flow0 = _wire(r, pump, "Flow")
    assert flow0 > 0.5 and _wire(r, pump, "Run_Status") == 1.0
    r.store.plant_alarm_overrides.setdefault(pump, {})["Alarm_LowFlow"] = 1.0
    r.tick(3)
    assert _wire(r, pump, "Alarm_LowFlow") == 1.0
    assert _wire(r, pump, "Run_Status") == 1.0                  # not failed yet
    assert pump not in r.store._plant_standby_names
    f3 = _wire(r, pump, "Flow")
    assert 0.0 < f3 < flow0                                     # running down
    assert _wire(r, pump, "Speed") > 0.0                        # drive still at speed
    r.tick(4)
    f7 = _wire(r, pump, "Flow")
    assert f7 < f3
    r.tick(int(r.store._FLOW_PROOF_S) + 5)
    assert _wire(r, pump, "Run_Status") == 0.0                  # called failed, stopped
    assert _wire(r, pump, "Flow") == 0.0
    assert pump in r.store._plant_standby_names                 # re-staged
    r.store.plant_alarm_overrides.pop(pump, None)
    r.tick(3)
    assert pump not in r.store._pump_proof_s


# ── spare before swap, and the chiller's own flow switch ─────────────────────

def _lead_train(r, dc="DC1"):
    trains = r.store._plant_trains_run.get(dc, [])
    assert trains, "a train is staged on"
    return trains[0]


def _spare(r, dc="DC1"):
    sp = r.store._cooling_context()["spare_chwp"].get(dc) or []
    assert sp, "the plant has a header spare"
    return sp[0]


def test_a_failed_pump_starts_the_spare_and_keeps_the_chiller(hall):
    """A running train whose evaporator pump fails is not a failed train when
    a header spare exists: the BMS parks the pump, starts the spare, and the
    chiller never stops. It used to swap the whole train."""
    r = hall
    tr = _lead_train(r)
    ch, pump, spare = tr["chiller"], tr["chwp"], _spare(r)
    assert spare in r.store._plant_standby_names                  # idling as N+1
    r.store.plant_alarm_overrides.setdefault(pump, {})["Alarm_Fault"] = 1.0
    r.tick(8)
    assert _lead_train(r)["chiller"] == ch                        # same train leads
    assert ch not in r.store._plant_standby_names
    assert _wire(r, ch, "Chiller_Running") == 1.0
    assert pump in r.store._plant_standby_names                   # parked
    assert _wire(r, pump, "Run_Status") == 0.0 and _wire(r, pump, "Flow") == 0.0
    assert spare not in r.store._plant_standby_names              # running for it
    assert _wire(r, spare, "Flow") > 0.5
    assert "Alarm_FlowLoss" not in r.store._plant_auto_points.get(ch, {})
    assert _wire(r, ch, "CHW_Flow") > 0.5                         # the barrel sees water
    # Repaired: the train's own pump comes back and the spare idles again.
    r.store.plant_alarm_overrides.pop(pump, None)
    r.tick(8)
    assert _lead_train(r)["chiller"] == ch
    assert pump not in r.store._plant_standby_names and _wire(r, pump, "Flow") > 0.5
    assert spare in r.store._plant_standby_names


def test_flow_proof_lost_with_a_spare_never_trips_the_chiller(hall):
    """The run-down takes the barrel below half its flow for a few seconds;
    the spare proves before the chiller's own switch runs out."""
    r = hall
    tr = _lead_train(r)
    ch, pump, spare = tr["chiller"], tr["chwp"], _spare(r)
    r.store.plant_alarm_overrides.setdefault(pump, {})["Alarm_LowFlow"] = 1.0
    r.tick(7)
    assert 0.0 < _wire(r, ch, "CHW_Flow") < 0.6 * 7.0 or _wire(r, ch, "CHW_Flow") < 5.0
    r.tick(int(r.store._FLOW_PROOF_S) + 6)
    assert _lead_train(r)["chiller"] == ch
    assert spare not in r.store._plant_standby_names
    assert "Alarm_FlowLoss" not in r.store._plant_auto_points.get(ch, {})
    assert ch not in r.store._chiller_flow_trip
    r.store.plant_alarm_overrides.pop(pump, None)


def test_no_spare_left_trips_the_chiller_on_its_own_switch(hall):
    """Pump and spare both stopped: the barrel sees no flow, the chiller's own
    switch trips it on Alarm_FlowLoss after its dwell, and the plant fails
    over to another train. Repaired, the switch resets by itself."""
    r = hall
    tr = _lead_train(r)
    ch, pump, spare = tr["chiller"], tr["chwp"], _spare(r)
    r.store.plant_alarm_overrides.setdefault(pump, {})["Run_Status"] = 0.0
    r.store.plant_alarm_overrides.setdefault(spare, {})["Run_Status"] = 0.0
    r.tick(6)
    assert _wire(r, ch, "CHW_Flow") == 0.0                        # nothing moving
    assert "Alarm_FlowLoss" not in r.store._plant_auto_points.get(ch, {})   # dwell
    r.tick(int(r.store._CHILLER_FLOW_TRIP_S) + 3)
    assert ch in r.store._chiller_flow_trip
    assert _wire(r, ch, "Alarm_FlowLoss") == 1.0
    assert r.store._plant_status(r.h.name(ch)) in ("flow_loss", "stopped", "unit_fault")
    r.tick(3)
    assert _lead_train(r)["chiller"] != ch                        # failed over
    assert ch in r.store._plant_standby_names
    # Repaired: both pumps back; the switch resets once the machine has sat off.
    r.store.plant_alarm_overrides.pop(pump, None)
    r.store.plant_alarm_overrides.pop(spare, None)
    r.tick(int(r.store._CHILLER_FLOW_RESET_S) + 5)
    assert ch not in r.store._chiller_flow_trip
    assert "Alarm_FlowLoss" not in r.store._plant_auto_points.get(ch, {})
