"""The right-click Simulate Fault menu offers a sensor what it can really do.

A rack probe gets the air ramps its channels support and the traps its model
raises. A BMS-trunk instrument gets its physical scenario - a door held open,
smoke in its hall, a hand on the abort switch - and no air head, no traps:
it has no agent, and the platform's polled rules raise the alarm.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from conftest import _device, build_plant
from api.routers import devices as D


@pytest.fixture
def rig(tmp_path, plant_cache, monkeypatch):
    from api.state import AppState
    fx = build_plant(tmp_path, crahs=1, servers=1, probes=True, rack_probes=1)
    for _ in range(2):
        fx.store._tick()
    s = SimpleNamespace(state_store=fx.store, device_manager=fx.dm, topology=fx.topo)
    monkeypatch.setattr(AppState, "get", classmethod(lambda cls: s))
    dm = fx.dm
    made = {
        "door": _device(dm, "DOOR-DC1-HA", "sensor", "", 0, model="Plant Door Contact"),
        "h2": _device(dm, "H2-DC1-UR", "sensor", "", 0, model="Plant Hydrogen Detector"),
        "asd": _device(dm, "ASD-DC1-HA", "sensor", "", 0, model="Plant Aspirating Smoke Detector"),
        "gsp": _device(dm, "GSP-DC1-HA", "sensor", "", 0, model="Plant Gas Release Panel"),
        "facp": _device(dm, "FACP-DC1-OR", "sensor", "", 0, model="Plant Fire Alarm Panel"),
        "t1h1": _device(dm, "SEN1-DC1-HA-R1-01", "sensor", "", 0, model="Raritan DPX2-T1H1"),
        "t1": _device(dm, "SEN2-DC1-HA-R1-01", "sensor", "", 0, model="Raritan DPX2-T1"),
        "gthd": _device(dm, "SEN3-DC1-HA-R1-01", "sensor", "", 0, model="Vertiv Geist GTHD"),
    }
    return SimpleNamespace(fx=fx, st=fx.store, made=made)


def _avail(dev):
    r = D.get_device_faults(dev.id)
    return {a["fault"] for a in r["available"]}, set(r["active"]), [e["type"] for e in r.get("events", [])]


def _set(dev, fault, action="start"):
    return D.set_device_fault(dev.id, D.FaultRequest(fault=fault, action=action))


def test_a_rack_probe_offers_the_ramps_and_traps_its_model_has(rig):
    avail, _, events = _avail(rig.made["t1h1"])
    assert avail == {"ambient_high", "humidity_high"}
    assert "HUMIDITY_ALERT" in events and "TEMPERATURE_ALERT" in events
    assert "SENSOR_MID_TEMP_HIGH" not in events

    avail, _, events = _avail(rig.made["t1"])
    assert avail == {"ambient_high"}, "one thermistor, no hygrometer"
    assert "HUMIDITY_ALERT" not in events

    _, _, events = _avail(rig.made["gthd"])
    assert "DEWPOINT_ALERT" in events


def test_a_thermowell_gets_no_air_ramp_and_no_trap(rig):
    avail, _, events = _avail(rig.fx.made["CHWS"])
    assert avail == set() and events == []


def test_a_door_contact_offers_held_open_and_holds_it(rig):
    door = rig.made["door"]
    avail, active, events = _avail(door)
    assert avail == {"door_held_open"} and active == set() and events == []
    _set(door, "door_held_open")
    assert rig.st.door_open(door.name)
    assert "door_held_open" in _avail(door)[1]
    _set(door, "door_held_open", "clear")
    assert not rig.st.door_open(door.name)


def test_an_air_ramp_is_refused_on_an_instrument_that_has_no_air(rig):
    with pytest.raises(HTTPException) as ei:
        _set(rig.made["door"], "ambient_high")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        _set(rig.made["t1"], "humidity_high")
    assert ei.value.status_code == 400


def test_the_detector_puts_smoke_in_its_own_hall(rig):
    asd = rig.made["asd"]
    avail, _, _ = _avail(asd)
    assert avail == {"smoke_alert", "smoke_fire1", "smoke_fire2", "asd_flow_fault"}
    _set(asd, "smoke_fire2")
    assert rig.st.smoke_level("DC1", "HA") == 2.5
    assert rig.st.smoke_level("DC1", "HB") is None
    assert _avail(asd)[1] == {"smoke_fire2"}
    _set(asd, "asd_flow_fault")
    assert rig.st.asd_flow_faulted(asd.name)
    assert _avail(asd)[1] == {"smoke_fire2", "asd_flow_fault"}
    _set(asd, "smoke_fire2", "clear")
    _set(asd, "asd_flow_fault", "clear")
    assert rig.st.smoke_level("DC1", "HA") is None and _avail(asd)[1] == set()


def test_the_release_panel_offers_the_abort_switch_and_the_fire_panel_nothing(rig):
    gsp = rig.made["gsp"]
    assert _avail(gsp)[0] == {"gas_abort_held"}
    _set(gsp, "gas_abort_held")
    assert rig.st.gas_abort_held("DC1", "HA") and _avail(gsp)[1] == {"gas_abort_held"}
    _set(gsp, "gas_abort_held", "clear")
    assert not rig.st.gas_abort_held("DC1", "HA")
    # The panel reflects its detectors and release panels; it has no knob of
    # its own, and it sends no trap.
    assert _avail(rig.made["facp"]) == (set(), set(), [])


def test_the_hydrogen_detector_gasses_its_own_site(rig):
    h2 = rig.made["h2"]
    assert _avail(h2)[0] == {"h2_buildup"}
    _set(h2, "h2_buildup")
    assert rig.st.hydrogen_extra("DC1") == 30.0 and rig.st.hydrogen_extra("DC2") is None
    _set(h2, "h2_buildup", "clear")
    assert rig.st.hydrogen_extra("DC1") is None
