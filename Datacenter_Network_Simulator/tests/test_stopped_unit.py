"""A stopped cooling unit has to LOOK stopped on the wire.

A CRAH tripped from the simulator published 22.0 C discharge against a 27.6 C
return, 80 % airflow and a healthy 5.6 K air-side delta for the whole six
minutes it was down. Every number a DCIM grades a cooling unit on said the
machine was fine; one binary said it was off. A monitoring platform reading
that plane truthfully has no way to tell the operator anything useful, and the
room model was meanwhile - correctly - warming the hall.

Two causes, one per side:

  * the controller only treated a unit as stopped when the BMS had staged it off
    or its MCC was dead, so an operator's or a rule's stop command never reached
    the physics, and
  * the store published a room-wide discharge temperature onto every CRAH in the
    hall, including the ones not running, and that publish is applied last.

These pin both, and the shape of what a stopped air handler reports.
"""
from __future__ import annotations

import pytest

from core.bacnet_plant_generator import STOPPED_AIR_SOAK_S, apply_stopped
from conftest import DC, ROOM, build_plant


def crah_points(supply=22.0, ret=27.6):
    """What the engine's own walk hands the controller for a running CRAH."""
    return {"Supply_Air_Temp": supply, "Return_Air_Temp": ret, "Setpoint": 22.0,
            "Fan_Speed": 71.0, "CHW_Valve": 62.0, "Cooling_Capacity": 65.0,
            "Airflow": 80.2, "Fan_Power": 9.4, "Run_Hours": 18000.0,
            "Unit_Running": 1.0, "Alarm_HighTemp": 0.0}


# ── what stops ───────────────────────────────────────────────────────────────

def test_a_stopped_unit_moves_no_air_and_draws_nothing():
    """The fan is not turning. Airflow, speed, draw, valve and delivered
    capacity are all consequences of it turning, so all of them are zero."""
    v = crah_points()
    apply_stopped(v)
    assert v["Airflow"] == 0.0
    assert v["Fan_Speed"] == 0.0
    assert v["Fan_Power"] == 0.0
    assert v["CHW_Valve"] == 0.0
    assert v["Cooling_Capacity"] == 0.0
    assert v["Unit_Running"] == 0.0


def test_a_stopped_unit_keeps_its_setpoint_and_its_run_hours():
    """Configuration and cumulative counters are not readings. A unit that is
    off still knows what it was asked to hold, and its runtime meter does not
    reset - it simply stops advancing, which the engine handles."""
    v = crah_points()
    apply_stopped(v)
    assert v["Setpoint"] == 22.0
    assert v["Run_Hours"] == 18000.0


# ── the discharge sensor ─────────────────────────────────────────────────────

def test_the_discharge_climbs_toward_the_return_rather_than_holding_setpoint():
    """Dead air in the supply plenum, over a coil whose valve has just shut,
    soaks toward the air around it. Holding 22.0 is the one thing a unit with
    no fan cannot be doing."""
    v = crah_points()
    supply = None
    seen = []
    for _ in range(6):
        supply = apply_stopped(crah_points(), dt=60.0, return_air_c=27.6,
                               supply_air_c=supply)
        seen.append(supply)
    assert seen == sorted(seen)                 # monotonic, no oscillation
    assert seen[0] > 22.0                       # it moved on the first minute
    assert seen[-1] < 27.6                      # and has not teleported
    assert seen[-1] > 24.0                      # six minutes is most of the way


def test_the_air_side_delta_collapses():
    """Return minus supply is the heat the unit carried away. It carried none."""
    supply = None
    for _ in range(40):
        supply = apply_stopped(crah_points(), dt=60.0, return_air_c=27.6,
                               supply_air_c=supply)
    assert 27.6 - supply < 0.5


def test_the_return_is_left_alone():
    """It reads the ROOM, by convection, and the room is getting hotter because
    this unit stopped. Averaging it with the discharge - which is right for a
    dead water loop - would hide exactly the thing the stop caused."""
    v = crah_points(ret=31.0)
    apply_stopped(v, dt=60.0, return_air_c=31.0)
    assert v["Return_Air_Temp"] == 31.0


def test_the_room_is_the_target_not_the_units_own_stale_reading():
    """The store publishes the room-derived return; the engine's walk knows
    nothing about the hall. The soak has to aim at the room."""
    hot = apply_stopped(crah_points(), dt=600.0, return_air_c=38.0)
    mild = apply_stopped(crah_points(), dt=600.0, return_air_c=27.6)
    assert hot > mild


# ── water is not air ─────────────────────────────────────────────────────────

def test_a_dead_water_loop_equalizes():
    """No flow, no heat exchange: the two thermowells drift onto one number.
    This is the case the air pair is deliberately excluded from."""
    v = {"CHW_Supply_Temp": 7.0, "CHW_Return_Temp": 12.0, "Chiller_Running": 1.0,
         "COP": 5.5, "CHW_Flow": 22.0}
    apply_stopped(v)
    assert v["CHW_Supply_Temp"] == v["CHW_Return_Temp"] == 9.5
    assert v["COP"] == 0.0
    assert v["CHW_Flow"] == 0.0
    assert v["Chiller_Running"] == 0.0


def test_a_machine_with_no_air_points_carries_nothing_forward():
    assert apply_stopped({"CHW_Supply_Temp": 7.0, "CHW_Return_Temp": 12.0}) is None


# ── the store side ───────────────────────────────────────────────────────────

@pytest.fixture
def hall(tmp_path, plant_cache):
    return build_plant(tmp_path, crahs=3)


def _running(plant_cache, *names):
    for n in names:
        plant_cache.setdefault(n, {})["Unit_Running"] = 1.0


def test_a_running_crah_is_published_a_discharge(hall, plant_cache):
    _running(plant_cache, *[hall.made[f"CRAH{i}"].name for i in (1, 2, 3)])
    hall.tick()
    pts = hall.auto_points(hall.made["CRAH1"].name)
    assert "Supply_Air_Temp" in pts
    assert "CHW_Valve" in pts


def test_a_stopped_crah_is_not(hall, plant_cache):
    """The publish is applied AFTER the stopped physics, so leaving it in place
    is what put 22.0 back on a tripped unit every tick."""
    dead = hall.made["CRAH1"].name
    _running(plant_cache, hall.made["CRAH2"].name, hall.made["CRAH3"].name)
    plant_cache.setdefault(dead, {})["Unit_Running"] = 0.0
    hall.tick()
    assert "Supply_Air_Temp" not in hall.auto_points(dead)
    assert "CHW_Valve" not in hall.auto_points(dead)


def test_a_stopped_crah_still_reports_the_return_air(hall, plant_cache):
    """Its return sensor is in the room's air whether or not the fan is on, and
    that reading is how the hall's heating shows up on the unit that caused it.

    The room's intake and exhaust are pinned here because the return figure is
    derived from them, and a fixture with no measured exhaust publishes no
    return at all - which would pass this test for the wrong reason."""
    dead = hall.made["CRAH1"].name
    _running(plant_cache, hall.made["CRAH2"].name, hall.made["CRAH3"].name)
    plant_cache.setdefault(dead, {})["Unit_Running"] = 0.0
    hall.store._room_inlet_c[(DC, ROOM)] = 24.0
    hall.store._room_outlet_c[(DC, ROOM)] = 38.0
    hall.store._compute_chw_loop()
    assert "Return_Air_Temp" in hall.auto_points(dead)
    assert "Supply_Air_Temp" not in hall.auto_points(dead)


def test_stopping_one_unit_does_not_silence_the_others(hall, plant_cache):
    dead = hall.made["CRAH1"].name
    _running(plant_cache, hall.made["CRAH2"].name, hall.made["CRAH3"].name)
    plant_cache.setdefault(dead, {})["Unit_Running"] = 0.0
    hall.tick()
    assert "Supply_Air_Temp" in hall.auto_points(hall.made["CRAH2"].name)


def test_the_soak_constant_is_minutes_not_seconds():
    """A guard on the one number that decides whether this reads as physics or
    as a step change: sheet metal and a wet coil have mass."""
    assert 60.0 <= STOPPED_AIR_SOAK_S <= 900.0


# ── the controller: a commanded stop is a stop ───────────────────────────────

class _FakeDevice:
    """Enough of a BACnet device for the tick loop: a kind, a name, and
    somewhere for the published values to land."""

    def __init__(self, name="CRAH1-DC1-HA-R1-01"):
        self.kind = "plant:crah"
        self.device_name = name
        self.device_ip = ""
        self.published: dict = {}

    def update_present_values(self, values):
        self.published.update(values)

    def get_snapshot(self):
        return dict(self.published)


def _controller(tmp_path):
    from core.bacnet_plant_generator import PlantTelemetryEngine
    from simulator.bacnet_controller import BACnetController

    ctrl = BACnetController(datasets_dir=str(tmp_path / "bacnet"))
    dev = _FakeDevice()
    ctrl._devices[1] = dev
    ctrl._telemetry[1] = PlantTelemetryEngine("crah", rated_kw=11.0, seed=1)
    ctrl._running = True
    return ctrl, dev


def test_an_operator_stop_reaches_the_physics(tmp_path):
    """The whole outage in one test. Commanding Unit_Running off used to move
    that one binary and nothing else, so the unit kept publishing a fan speed,
    an airflow and a discharge on setpoint."""
    ctrl, dev = _controller(tmp_path)
    ovr = {dev.device_name: {"Unit_Running": 0.0, "Return_Air_Temp": 27.6}}
    ctrl.tick(1.0, plant_overrides=ovr)

    assert dev.published["Unit_Running"] == 0.0
    assert dev.published["Airflow"] == 0.0
    assert dev.published["Fan_Speed"] == 0.0
    assert dev.published["CHW_Valve"] == 0.0


def test_the_discharge_leaves_setpoint_while_the_stop_is_held(tmp_path):
    ctrl, dev = _controller(tmp_path)
    ovr = {dev.device_name: {"Unit_Running": 0.0, "Return_Air_Temp": 27.6}}
    ctrl.tick(1.0, plant_overrides=ovr)
    first = dev.published["Supply_Air_Temp"]
    for _ in range(5):
        ctrl.tick(60.0, plant_overrides=ovr)
    assert dev.published["Supply_Air_Temp"] > first
    assert dev.published["Supply_Air_Temp"] > 23.5


def test_a_running_unit_is_left_alone(tmp_path):
    """The stop physics must not fire on a healthy unit carrying an alarm
    override - a dirty filter is not a stopped machine."""
    ctrl, dev = _controller(tmp_path)
    ctrl.tick(1.0, plant_overrides={dev.device_name: {"Filter_Dirty": 1.0}})
    assert dev.published["Airflow"] > 0.0
    assert dev.published["Unit_Running"] == 1.0


def test_restarting_forgets_the_soak(tmp_path):
    """Otherwise the next stop would resume from wherever the last one ended,
    which for a unit that had been down an hour is the room temperature."""
    ctrl, dev = _controller(tmp_path)
    ovr = {dev.device_name: {"Unit_Running": 0.0, "Return_Air_Temp": 27.6}}
    for _ in range(20):
        ctrl.tick(60.0, plant_overrides=ovr)
    assert dev.device_name in ctrl._stopped_air_c
    ctrl.tick(1.0, plant_overrides={})
    assert dev.device_name not in ctrl._stopped_air_c
