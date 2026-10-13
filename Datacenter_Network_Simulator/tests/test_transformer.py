"""S3 increment 2b: the service transformer (docs/27 §8c.2).

A transformer is thermal mass. Its monitor reports oil that takes hours to
move and copper that takes minutes, both over the weather in the yard; the
pure step below is what the state store runs each tick, and these tests are
the loading guide's shape: steady states at the nameplate numbers, the right
order of time constants, and relays that mean something.
"""
from __future__ import annotations

import math

import core.device_state_store as dss
from core.device_manager import FACILITY_TYPES, DeviceType, rated_capacity_w
from core.equipment_geometry import OUTDOOR_LEVEL, complete_site, footprint
from core.modbus_register_map import MODBUS_MAPS, SPACE_DISCRETE, SPACE_INPUT, get_map
from core.snmprec_generator import _NO_SNMP_TYPES


def _settle(k: float, hours: float = 24.0) -> tuple[float, float]:
    oil, hs = 0.0, 0.0
    for _ in range(int(hours * 60)):
        oil, hs = dss.transformer_thermal_step(oil, hs, k, 60.0)
    return oil, hs


def test_steady_state_at_nameplate_is_the_loading_guide_numbers():
    oil, hs = _settle(1.0)
    assert math.isclose(oil, dss._XFMR_OIL_RISE_K, rel_tol=1e-3)
    assert math.isclose(hs, dss._XFMR_HOTSPOT_GRADIENT_K, rel_tol=1e-3)


def test_no_load_still_warms_the_oil_on_iron_loss_and_the_copper_not_at_all():
    oil, hs = _settle(0.0)
    # (1 / (1 + R))^x of the rated rise: the core is magnetised whatever the load.
    assert 0 < oil < dss._XFMR_OIL_RISE_K * 0.3
    assert hs == 0.0


def test_the_winding_answers_a_load_step_long_before_the_tank_does():
    oil0, hs0 = _settle(0.5)
    oil, hs = oil0, hs0
    for _ in range(10):                      # ten minutes at nameplate
        oil, hs = dss.transformer_thermal_step(oil, hs, 1.0, 60.0)
    oil_ss, hs_ss = _settle(1.0)
    hs_progress = (hs - hs0) / (hs_ss - hs0)
    oil_progress = (oil - oil0) / (oil_ss - oil0)
    assert hs_progress > 0.85
    assert oil_progress < 0.10


def test_a_dead_tick_changes_nothing():
    assert dss.transformer_thermal_step(30.0, 12.0, 1.0, 0.0) == (30.0, 12.0)


def test_the_monitor_map_is_a_modbus_device_with_a_validity_bit():
    m = get_map("transformer")
    assert m is MODBUS_MAPS["transformer"]
    assert m.map_id == "SIM-XFMR-ITM-v1"
    names = {p.name for p in m.points[SPACE_INPUT]}
    assert {"Top_Oil_Temperature", "Winding_Temperature", "Load_Percent", "Tap_Position",
            "Current_A", "Current_B", "Current_C"} <= names
    bits = {p.name: p for p in m.points[SPACE_DISCRETE]}
    assert bits["Data_Valid"].presence_of == "xfmr_oil_c"
    assert bits["Fans_Running"].truthy == "running"
    assert bits["Gas_Relay_Alarm"].truthy == "alarm"


def test_the_type_is_facility_plant_with_no_snmp_and_a_nameplate_rating():
    assert DeviceType.TRANSFORMER in FACILITY_TYPES
    assert DeviceType.TRANSFORMER in _NO_SNMP_TYPES
    assert rated_capacity_w(DeviceType.TRANSFORMER, "Schneider Minera 2000kVA ONAN 11/0.415kV") == 2_000_000
    fp = footprint({"device_type": "transformer", "model_name": "Schneider Minera 2000kVA ONAN 11/0.415kV",
                    "mounting": "floor"})
    assert (fp["width"], fp["depth"], fp["height"]) == (2.40, 1.60, 2.20)


def test_a_substation_room_replaces_the_drawn_substation_zone():
    fp = {
        "rooms": {
            "DC9/UPS Room": {"datacenter": "DC9", "room": "UPS Room", "class": "electrical", "level": "G",
                             "origin": {"x": 0.0, "y": 0.0}, "width_m": 9.0, "depth_m": 6.0, "rows": []},
            "DC9/Substation": {"datacenter": "DC9", "room": "Substation", "class": "outdoor", "level": OUTDOOR_LEVEL,
                               "origin": {"x": -16.0, "y": 2.0}, "width_m": 10.0, "depth_m": 11.0, "rows": []},
        },
    }
    placed = complete_site(fp, [])
    zones = placed["compound"]["DC9"]["zones"]
    sub = [z for z in zones if z["kind"] == "substation"]
    assert len(sub) == 1
    assert sub[0]["attrs"]["room"] == "DC9/Substation"
    without = complete_site({"rooms": {"DC9/UPS Room": fp["rooms"]["DC9/UPS Room"]}}, [])
    drawn = [z for z in without["compound"]["DC9"]["zones"] if z["kind"] == "substation"]
    assert len(drawn) == 1 and "room" not in (drawn[0].get("attrs") or {})
