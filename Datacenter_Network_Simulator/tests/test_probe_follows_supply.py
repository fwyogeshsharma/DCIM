"""A rack's PDU probe reads the same air as the servers beside it.

Through a total loss of CRAH airflow the PDU probes read 23 °C while the
co-located servers read 36: the strip's probe was a random walk mean-reverting
to the setpoint and capped at 30, the per-tick SNMP patch wrote that number
over the DPX2's physically modelled reading, and the quiet-mode scrub capped
whatever survived at 31.9. A DCIM that takes intake from the rack probe first
- as real ones do - then reported the hall 100 % in band under 300 inlet
alarms. These pin the three places that lie was built.
"""
from __future__ import annotations

import pytest

import core.device_state_store as dss
from core.device_manager import Device, DeviceType, Vendor
from core.snmprec_generator import SNMPRecGenerator, _RARITAN_SENSOR
from core import vendor_oids

from conftest import build_plant


@pytest.fixture
def rack(tmp_path, plant_cache):
    return build_plant(tmp_path, crahs=2, rack_probes=1)


def _step(store, device, n=6):
    for _ in range(n):
        store._step_ext_state(device)
    return dss._get_ext_state(device.name)


def test_pdu_probe_tracks_the_room_supply(rack, monkeypatch):
    """Warm the cold aisle and the strip's probe warms with it, a little above
    the floor-level supply because a vertical strip samples mid-rack air."""
    store, pdu = rack.store, rack.made["PDUA"]
    monkeypatch.setattr(store, "_room_supply_temp", lambda d: 22.0)
    cool = _step(store, pdu)["pdu_temperature"]
    monkeypatch.setattr(store, "_room_supply_temp", lambda d: 36.0)
    hot = _step(store, pdu)["pdu_temperature"]
    assert 22.5 <= cool <= 24.5
    assert 36.5 <= hot <= 38.5
    assert hot - cool > 10


def test_quiet_mode_does_not_cap_a_real_cooling_failure(rack):
    """The scrub tames random walks. A probe reading is the room air, so the
    old 34.9 / 31.9 caps would have falsified a measurement."""
    store, pdu, probe = rack.store, rack.made["PDUA"], rack.made["SNS1"]
    st = dss._get_ext_state(pdu.name)
    st["pdu_temperature"] = 38.4
    probe.inlet_temp, probe.mid_temp, probe.outlet_temp = 36.2, 40.0, 47.0
    probe.humidity = 50.0
    store.autonomous_faults = False
    store._scrub_numeric_faults(pdu)
    store._scrub_numeric_faults(probe)
    assert dss._get_ext_state(pdu.name)["pdu_temperature"] == 38.4
    assert (probe.inlet_temp, probe.mid_temp, probe.outlet_temp) == (36.2, 40.0, 47.0)
    # The walks keep their caps.
    probe.humidity = 80.0
    store._scrub_numeric_faults(probe)
    assert probe.humidity == 69.9


# --------------------------------------------------------------- SNMP patch


def _publish(name, model, slot, inlet=35.6):
    dss._ext_state_cache[name] = {
        "probe_inlet_c": inlet, "probe_mid_c": 39.1, "probe_outlet_c": 46.8,
        "probe_humidity_pct": 41.5, "probe_dewpoint_c": 9.0,
        "probe_model": model, "probe_slot": slot, "water_detection": "dry"}


@pytest.fixture
def strip_with_probe():
    pdu = Device(name="PDUA-DC1-HA-R2-01", device_type=DeviceType.PDU,
                 vendor=Vendor.RARITAN, ip_address="", mgmt_ip="10.52.11.30",
                 model_name="Raritan PX2-5170CR")
    pdu.sensor_children = ["SEN1-DC1-HA-R2-01"]
    _publish("SEN1-DC1-HA-R2-01", "Raritan DPX2-T3H1", 1)
    yield pdu
    dss._ext_state_cache.pop("SEN1-DC1-HA-R2-01", None)


def test_a_strip_with_probes_republishes_their_readings(strip_with_probe):
    """The tick rewrites the DPX2's slots from the DPX2, not from the strip:
    slot 1 is its 35.6 °C inlet, and slot 2 is its mid-rack temperature,
    which a humidity used to overwrite."""
    up = SNMPRecGenerator._pdu_probe_updates(strip_with_probe, 230, 450)
    assert up[f"{_RARITAN_SENSOR}.4.1.1"][1] == "356"
    assert up[f"{_RARITAN_SENSOR}.4.1.2"][1] == "391"
    assert up[f"{_RARITAN_SENSOR}.4.1.3"][1] == "468"
    assert up[f"{_RARITAN_SENSOR}.4.1.4"][1] == "415"
    assert "230" not in {v for _, v in up.values()}


def test_a_strip_without_probes_keeps_its_own_probe_at_slot_one():
    pdu = Device(name="PDUB-DC1-HA-R2-01", device_type=DeviceType.PDU,
                 vendor=Vendor.RARITAN, ip_address="", mgmt_ip="10.52.11.31",
                 model_name="Raritan PX2-5170CR")
    up = SNMPRecGenerator._pdu_probe_updates(pdu, 356, 450)
    R = vendor_oids.RARITAN
    assert up[f"{R['externalValue']}.1.1"] == ("66", "356")
    assert up[f"{R['externalValue']}.1.2"] == ("66", "450")
