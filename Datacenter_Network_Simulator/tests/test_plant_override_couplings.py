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


def test_crah_forces_are_all_store_side_now():
    st = _store({"CRAH1": {"Return_Air_Temp": 30.0, "CHW_Valve": 60.0}})
    k = _coupling_kinds(st, "CRAH1", "crah")
    assert k["Alarm_HighTemp"] == "store"
    assert k["Alarm_HighReturnAir"] == "store"
    assert k["Filter_Dirty"] == "store"
    assert k["Unit_Running"] == "store"


def test_cdu_forces_are_all_store_side_now():
    st = _store({"CDU1": {"TCS_Flow": 4.0, "TCS_Supply_Temp": 32.0}})
    k = _coupling_kinds(st, "CDU1", "cdu")
    assert k["Alarm_HighSupplyTemp"] == "store"
    assert k["Alarm_PumpFault"] == "store"
    assert k["Alarm_LowFlow"] == "store"
    assert k["Unit_Running"] == "store"


def test_a_coupling_the_store_does_not_own_is_lost_once_its_target_is_published():
    """The classifier's lost/engine split, with a coupling taken back out of
    the store's set, so the mechanism stays tested now that nothing is lost."""
    side = DeviceStateStore._STORE_SIDE_COUPLINGS - {("crah", "Alarm_HighReturnAir")}
    st = _store({})
    st._STORE_SIDE_COUPLINGS = side
    assert _coupling_kinds(st, "CRAH1", "crah")["Alarm_HighReturnAir"] == "engine"
    st = _store({"CRAH1": {"Return_Air_Temp": 30.0, "CHW_Valve": 60.0}})
    st._STORE_SIDE_COUPLINGS = side
    assert _coupling_kinds(st, "CRAH1", "crah")["Alarm_HighReturnAir"] == "lost"


def test_pump_forces_are_the_store_taking_the_pump_out():
    st = _store({"CHWP1": {"Flow": 5.0, "Speed": 70.0}})
    k = _coupling_kinds(st, "CHWP1", "pump")
    assert k == {"Run_Status": "store", "Alarm_Fault": "store", "Alarm_LowFlow": "store"}


def test_valve_forces_both_reach_physics():
    """Actuator Fault parks the disc short of its command; Stuck freezes it
    where it stood and lets the command walk away. Both are scored."""
    k = _coupling_kinds(_store({}), "VCHW", "valve")
    assert k == {"Alarm_ActuatorFault": "store", "Status_Modulating": "store"}
