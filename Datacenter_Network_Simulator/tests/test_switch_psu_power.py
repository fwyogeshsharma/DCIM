"""A switch says what it draws: PSU input power on ENTITY-SENSOR-MIB and
openconfig-platform-psu, summing to the same chassis figure on both planes.

Before this a network rack read as a dash on the Power page: its switches
were polled for interfaces and temperatures and published nothing in watts,
so the only instrument left was the strip. Real gear lists each supply in
entPhySensorTable as a watts(6) sensor; a poller sums the inputs.
"""

from __future__ import annotations

import json
import tempfile

from core.device_manager import Device, DeviceType, Vendor
from core.gnmi_data_generator import GNMIDataGenerator
from core.redfish_data_generator import _live_watts
from core.snmprec_generator import SNMPRecGenerator, _ENTITY_SENSOR
from core.topology_engine import TopologyEngine


def _switch(cpu: float = 40.0) -> Device:
    d = Device(name="SW01-DC1-HA-R1-01", device_type=DeviceType.SWITCH,
               vendor=Vendor.CISCO_SYSTEMS, model_name="Cisco Nexus 9364C",
               ip_address="10.0.2.1", power_draw_w=500)
    d.cpu_usage = cpu
    return d


def _rows(entries):
    return {oid: val for oid, _typ, val in entries}


def test_each_supply_is_a_watts_sensor_after_the_temperatures():
    sw = _switch()
    rows = _rows(SNMPRecGenerator._network_psu_entries(sw))
    assert len(sw.psus) == 2, "a switch is built with a 1+1 pair"
    for idx in (3, 4):
        assert rows[f"{_ENTITY_SENSOR}.1.{idx}"] == "6", "entPhySensorType watts(6)"
        assert rows[f"{_ENTITY_SENSOR}.2.{idx}"] == "9" and rows[f"{_ENTITY_SENSOR}.3.{idx}"] == "0"
        assert rows[f"{_ENTITY_SENSOR}.5.{idx}"] == "1"
    assert rows[f"1.3.6.1.2.1.47.1.1.1.1.7.3"] == "PSU1 input power"
    total = int(rows[f"{_ENTITY_SENSOR}.4.3"]) + int(rows[f"{_ENTITY_SENSOR}.4.4"])
    assert total == _live_watts(sw), "the inputs sum to the chassis draw the BMC-style figure gives"
    assert abs(int(rows[f"{_ENTITY_SENSOR}.4.3"]) - int(rows[f"{_ENTITY_SENSOR}.4.4"])) <= 1, "current sharing"


def test_the_draw_follows_the_cpu_like_a_server():
    idle, busy = _switch(0.0), _switch(100.0)
    w_idle = sum(SNMPRecGenerator._psu_input_watts(idle))
    w_busy = sum(SNMPRecGenerator._psu_input_watts(busy))
    assert w_idle < w_busy == 500, "nameplate at full load, an idle floor below it"


def test_gnmi_publishes_the_same_supplies_as_power_supply_components():
    topo = TopologyEngine()
    sw = _switch(); topo.add_device(sw)
    gen = GNMIDataGenerator(tempfile.mkdtemp())
    doc = json.load(open(gen.generate_device(sw, topo), encoding="utf-8"))
    comps = {c["name"]: c for c in doc["openconfig-platform:components"]["component"]}
    assert "CHASSIS" in comps and "power-supply" not in comps["CHASSIS"]
    psus = [c for c in comps.values() if c["state"]["type"].endswith("POWER_SUPPLY")]
    assert [c["name"] for c in psus] == ["PSU1", "PSU2"]
    inputs = [c["power-supply"]["state"]["input-power"] for c in psus]
    assert sum(inputs) == _live_watts(sw), "the two planes agree to the watt"
    assert psus[0]["power-supply"]["state"]["output-power"] < inputs[0], "a supply loses something"
    assert psus[0]["power-supply"]["state"]["capacity"] == sw.psus[0].capacity_w
