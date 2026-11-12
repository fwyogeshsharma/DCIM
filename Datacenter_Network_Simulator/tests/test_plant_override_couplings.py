"""What GET /bacnet/plant/overrides?device= says forcing each alarm does
(doc 32 §9.2): the menu's "annunciation only" label hangs on this."""
from __future__ import annotations

from types import SimpleNamespace

from api.routers.bacnet import _coupling_kinds
from core.device_state_store import DeviceStateStore


def _store(published: dict[str, dict]):
    return SimpleNamespace(_FORCED_COUPLINGS=DeviceStateStore._FORCED_COUPLINGS,
                           _STORE_SIDE_COUPLINGS=DeviceStateStore._STORE_SIDE_COUPLINGS,
                           _plant_auto_points=published)


def test_chiller_high_chw_supply_is_store_side_now():
    st = _store({"CHL1": {"CHW_Supply_Temp": 7.0, "CHW_Return_Temp": 12.0}})
    k = _coupling_kinds(st, "CHL1", "chiller")
    assert k["Alarm_HighCHWSupply"] == "store"
    assert k["Chiller_Running"] == "store"            # a trip
    assert k["Alarm_HighPressure"] == "store"         # scored as a fault


def test_crah_return_air_force_is_lost_when_the_store_publishes_the_target():
    st = _store({"CRAH1": {"Return_Air_Temp": 30.0, "CHW_Valve": 60.0}})
    k = _coupling_kinds(st, "CRAH1", "crah")
    assert k["Alarm_HighTemp"] == "store"
    assert k["Alarm_HighReturnAir"] == "lost"
    assert k["Filter_Dirty"] == "lost"
    assert k["Unit_Running"] == "store"


def test_crah_return_air_force_reaches_the_wire_before_anything_is_published():
    k = _coupling_kinds(_store({}), "CRAH1", "crah")
    assert k["Alarm_HighReturnAir"] == "engine"


def test_pump_forces_are_the_store_taking_the_pump_out():
    st = _store({"CHWP1": {"Flow": 5.0, "Speed": 70.0}})
    k = _coupling_kinds(st, "CHWP1", "pump")
    assert k == {"Run_Status": "store", "Alarm_Fault": "store", "Alarm_LowFlow": "store"}


def test_valve_actuator_fault_moves_the_disc_but_stuck_is_annunciation_only():
    k = _coupling_kinds(_store({}), "VCHW", "valve")
    assert k["Alarm_ActuatorFault"] == "store"
    assert k["Status_Modulating"] == "lost"
