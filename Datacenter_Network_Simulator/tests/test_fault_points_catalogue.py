"""The injectable-point catalogue lists what a device's OWN renderer serves.

The store seeds every device's ext with the whole electrical template so any
renderer can read any key. Before this, /api/faults/points handed that seed
back as instrumentation: a door contact offered ninety ATS, genset and
switchgear points and an air head it does not have (2026-10-30). A catalogue
is a promise about the wire; it has to be true per device.
"""
from __future__ import annotations

import pytest

from conftest import _device, build_plant
from core import fault_points as fp
from core.fault_injection import is_binary

#: What the store seeds on every device, abridged.
_TEMPLATE = {
    "ats_normal_voltage": 400.0, "ats_frequency": 50.0, "ats_state": "utility",
    "gen_fuel_pct": 100.0, "gen_kw": 0.0, "mcc_voltage": 400.0, "mpp_kw": 0.0,
    "swgr_current": 0.0, "util_kw": 0.0, "ups_output_load": 0.0,
    "pdu_temperature": 24.0, "pdu_load": 10.0, "xfmr_oil_c": 55.0, "xfmr_tap": 5,
}


@pytest.fixture
def rig(tmp_path, plant_cache):
    fx = build_plant(tmp_path, crahs=1, servers=1, probes=True, rack_probes=1)
    dm = fx.dm
    made = {
        "door": _device(dm, "DOOR-DC1-HA", "sensor", "", 0, model="Plant Door Contact"),
        "facp": _device(dm, "FACP-DC1-OR", "sensor", "", 0, model="Plant Fire Alarm Panel"),
        "gsp": _device(dm, "GSP-DC1-HA", "sensor", "", 0, model="Plant Gas Release Panel"),
        "asd": _device(dm, "ASD-DC1-HA", "sensor", "", 0, model="Plant Aspirating Smoke Detector"),
        "wtr": _device(dm, "WTR-DC1-CP", "sensor", "", 0, model="Plant Tank Level Transmitter"),
        "fuel": _device(dm, "FUEL-DC1-GR", "sensor", "", 0, model="Plant Tank Level Transmitter"),
        "tx": _device(dm, "TX1-DC1-SB", "transformer", "", 0, model="Schneider Minera 2000kVA"),
        "sw": _device(dm, "SW1-DC1-NR-R1-01", "switch", "10.0.0.9", 300, model="Arista 7050X3"),
        "t1h1": _device(dm, "SEN1-DC1-HA-R1-01", "sensor", "", 0, model="Raritan DPX2-T1H1"),
        "t1": _device(dm, "SEN2-DC1-HA-R1-01", "sensor", "", 0, model="Raritan DPX2-T1"),
        "gthd": _device(dm, "SEN3-DC1-HA-R1-01", "sensor", "", 0, model="Vertiv Geist GTHD"),
    }
    return fx, made


def _points(dev, own=None):
    ext = dict(_TEMPLATE)
    ext.update(own or {})
    return {p["point"]: p for p in fp.device_points(dev, ext)}


def test_a_door_contact_is_one_bit_and_nothing_else(rig):
    _, m = rig
    pts = _points(m["door"], {"door_open": 0.0})
    assert set(pts) == {"door_open"}
    assert pts["door_open"]["binary"] is True
    assert pts["door_open"]["surface"] == "ext"
    assert pts["door_open"]["metric_hint"] == {"metric": "door_open", "instance": ""}
    assert "modbus" in pts["door_open"]["protocols"]


def test_a_fire_panel_offers_its_three_relays_not_its_zone_mask(rig):
    _, m = rig
    pts = _points(m["facp"], {"fire_alarm": 0.0, "fire_trouble": 0.0, "fire_supervisory": 0.0,
                              "fire_zones": 0.0, "fire_zone_count": 0.0})
    assert set(pts) == {"fire_alarm", "fire_trouble", "fire_supervisory"}
    assert all(p["binary"] for p in pts.values())


def test_life_safety_points_are_binary_to_the_transform_too(rig):
    """The router refuses bias on a binary point by the catalogue flag; the
    transform must agree, or a stuck-free fault on a relay would drift it."""
    for k in ("door_open", "fire_alarm", "agent_released", "tank_low", "h2_alarm",
              "asd_flow_fault", "gas_abort"):
        assert is_binary(k), k
    assert not is_binary("smoke_obsc_pct_m") and not is_binary("tank_level_pct")


def test_a_gas_panel_keeps_pressure_and_relays_drops_the_enum(rig):
    _, m = rig
    pts = _points(m["gsp"], {"gas_state": 0.0, "gas_countdown_s": 0.0, "agent_pressure_bar": 42.0,
                             "agent_released": 0.0, "gas_abort": 0.0, "agent_low_pressure": 0.0})
    assert set(pts) == {"agent_pressure_bar", "agent_released", "gas_abort", "agent_low_pressure"}
    assert pts["agent_pressure_bar"]["binary"] is False
    assert pts["agent_pressure_bar"]["metric_hint"]["metric"] == "agent_pressure"


def test_an_aspirating_detector_offers_obscuration_not_the_stage_it_derives(rig):
    _, m = rig
    pts = _points(m["asd"], {"smoke_obsc_pct_m": 0.01, "smoke_stage": 0.0, "asd_flow_fault": 0.0})
    assert set(pts) == {"smoke_obsc_pct_m", "asd_flow_fault"}
    assert pts["smoke_obsc_pct_m"]["metric_hint"]["metric"] == "smoke_obscuration"


def test_the_same_transmitter_binds_to_fuel_on_one_tank_and_water_on_the_other(rig):
    _, m = rig
    own = {"tank_level_pct": 70.0, "tank_volume_l": 7000.0, "tank_low": 0.0}
    fuel = _points(m["fuel"], own)
    water = _points(m["wtr"], own)
    assert set(fuel) == set(water) == {"tank_level_pct", "tank_low"}
    assert fuel["tank_level_pct"]["metric_hint"]["metric"] == "fuel_level_pct"
    assert water["tank_level_pct"]["metric_hint"]["metric"] == "water_level_pct"


def test_a_transformer_offers_its_monitor_and_only_its_monitor(rig):
    _, m = rig
    pts = _points(m["tx"], {"xfmr_hotspot_c": 70.0, "xfmr_load_pct": 40.0, "xfmr_ia": 100.0,
                            "xfmr_oil_alarm": "normal"})
    assert set(pts) == {"xfmr_oil_c", "xfmr_hotspot_c", "xfmr_load_pct", "xfmr_ia"}
    assert pts["xfmr_oil_c"]["protocols"] == ["modbus"]
    assert pts["xfmr_hotspot_c"]["metric_hint"]["metric"] == "winding_temperature"
    assert pts["xfmr_ia"]["metric_hint"] == {"metric": "current", "instance": "A"}


def test_a_switch_has_no_ext_instrumentation_to_bend(rig):
    _, m = rig
    assert _points(m["sw"]) == {}


def test_a_rack_probe_offers_the_channels_its_model_has(rig):
    _, m = rig
    assert set(_points(m["t1h1"])) == {"inlet_temp", "humidity"}
    assert set(_points(m["t1"])) == {"inlet_temp"}
    # A model the channel table does not know keeps the full head.
    assert set(_points(m["gthd"])) == {"inlet_temp", "mid_temp", "outlet_temp",
                                       "humidity", "dewpoint"}


def test_a_thermowell_still_reads_its_water_on_both_wires(rig):
    fx, _ = rig
    pts = _points(fx.made["CHWS"], {"water_temp": 7.1})
    assert set(pts) == {"water_temp"}
    assert set(pts["water_temp"]["protocols"]) == {"snmp", "modbus"}


def test_the_electrical_types_keep_their_own_prefix(rig):
    fx, _ = rig
    dm = fx.dm
    ups = _device(dm, "UPS9-DC1-UR", "ups", "10.0.0.50", 0, model="Liebert EXL S1")
    pts = _points(ups, {"ups_battery_temp_c": 26.0, "ups_input_voltage": 400.0})
    assert set(pts) == {"ups_output_load", "ups_battery_temp_c", "ups_input_voltage"}
    assert "modbus" in pts["ups_input_voltage"]["protocols"]
