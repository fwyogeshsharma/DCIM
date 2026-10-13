"""S3 increment 2: the instruments a site wires to its BMS (docs/27 §8b, §8c).

A door contact, a hydrogen detector, a battery string probe and two tank
level transmitters, on the same RS-485 trunk as the thermowells. What must
hold: each has a role and a map, none of them answers for air or water, the
tanks behave like tanks, and every site in the shipped topology has the set.
"""
import json
from pathlib import Path

import pytest

from core.device_state_store import (
    _BULK_FUEL_L, _FUEL_DELIVERY_H, _FUEL_REFILL_BELOW_PCT, _FUEL_REFILL_TO_PCT,
    _POINT_ROLES, _PROBE_ROLES, _WATER_ROLES, _probe_role_by_name, fuel_step, water_step,
)
from core.modbus_register_map import PROBE_MAPS, SPACE_DISCRETE, SPACE_INPUT, get_probe_map

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"


@pytest.fixture(scope="module")
def topo():
    return json.loads(TOPO.read_text(encoding="utf-8"))


def test_each_instrument_has_a_role_and_a_map():
    for code, role in (("DOOR", "door_contact"), ("H2", "hydrogen"), ("BTT", "battery_temp"),
                       ("FUEL", "fuel_level"), ("WTR", "water_level")):
        assert _PROBE_ROLES[code] == role
        assert _probe_role_by_name(f"{code}-DC1-UR") == role
        assert get_probe_map(role) is not None, role
    assert set(PROBE_MAPS) == set(_PROBE_ROLES.values())


def test_a_point_is_neither_air_nor_water():
    """The cold-aisle air rules and the water keys must both stay off them."""
    assert _POINT_ROLES.isdisjoint(_WATER_ROLES)
    assert "room_air" not in _POINT_ROLES
    for role in _POINT_ROLES:
        keys = {p.key for pts in get_probe_map(role).points.values() for p in pts if p.key}
        assert "water_temp" not in keys and "room_air_temp_c" not in keys, role


def test_a_door_is_one_bit_with_a_validity_bit():
    m = get_probe_map("door_contact")
    assert SPACE_INPUT not in m.points
    names = [p.name for p in m.points[SPACE_DISCRETE]]
    assert names == ["Door_Open", "Reading_Valid"]
    assert m.points[SPACE_DISCRETE][0].truthy(1.0) and not m.points[SPACE_DISCRETE][0].truthy(0.0)


def test_hydrogen_reads_in_lel_and_carries_its_own_alarm():
    m = get_probe_map("hydrogen")
    (pt,) = m.points[SPACE_INPUT]
    assert pt.units == "%LEL" and pt.scale == 100
    assert [p.name for p in m.points[SPACE_DISCRETE]] == ["Alarm_High", "Reading_Valid"]


def test_a_tank_reports_percent_and_litres_and_a_low_relay():
    fuel, water = get_probe_map("fuel_level"), get_probe_map("water_level")
    # The same transmitter on both tanks, but its own template on each: the
    # binding to the generator's fuel or the towers' water lives there.
    assert fuel.map_id != water.map_id
    for m, lvl, vol in ((fuel, "Bulk_Fuel_Level", "Bulk_Fuel_Volume"), (water, "Water_Level", "Water_Volume")):
        names = {p.name: p for p in m.points[SPACE_INPUT]}
        assert names[lvl].units == "%" and names[vol].dtype == "u32"
        assert [p.name for p in m.points[SPACE_DISCRETE]] == ["Alarm_Low_Level", "Reading_Valid"]


def test_the_bulk_tank_burns_while_a_set_runs_and_a_delivery_fills_it():
    # Two sets at full load: 1000 L/h on a 40 000 L tank is 2.5 % an hour.
    pct, due = fuel_step(92.0, 1000.0, 1.0, None)
    assert pct == pytest.approx(89.5, abs=0.01) and due is None
    # Nothing running, nothing burns.
    assert fuel_step(89.5, 0.0, 10.0, None) == (89.5, None)
    # Below the reorder level a delivery is ordered, and it lands later.
    pct, due = fuel_step(_FUEL_REFILL_BELOW_PCT + 0.5, 1000.0, 1.0, None)
    assert pct < _FUEL_REFILL_BELOW_PCT and due == _FUEL_DELIVERY_H
    pct, due = fuel_step(pct, 0.0, _FUEL_DELIVERY_H, due)
    assert pct == _FUEL_REFILL_TO_PCT and due is None
    assert _BULK_FUEL_L == 40000.0


def test_the_makeup_tank_is_drawn_down_by_the_towers_and_refilled_by_the_mains():
    assert water_step(84.0, 0.0, 60.0) == 84.0
    low = water_step(84.0, 50.0, 60.0)                  # 3000 L drawn in an hour, valve shut
    assert low == pytest.approx(69.0, abs=0.01)
    # Under the float valve the mains refill it (80 L/min against 50 L/min drawn).
    assert water_step(50.0, 50.0, 60.0) > 50.0
    assert water_step(50.0, 0.0, 600.0) == 90.0         # and the valve shuts at its level


# ── the shipped topology ────────────────────────────────────────────────────

def test_every_site_carries_the_instrument_set_on_its_gateway(topo):
    devs = {n["device"]["name"]: n["device"] for n in topo["nodes"]}
    rooms = topo["floorplan"]["rooms"]
    for dc in ("DC1", "DC2"):
        gw = next(d for d in devs.values() if d["device_type"] == "modbus_gateway" and d["datacenter"] == dc)
        gw_id = next(n["id"] for n in topo["nodes"] if n["device"]["name"] == gw["name"])
        for code, suffix, room in (("DOOR", "HA", "Server Hall A"), ("DOOR", "HB", "Server Hall B"),
                                   ("DOOR", "NR", "Network Room"), ("DOOR", "UR", "UPS Room"),
                                   ("DOOR", "MM", "Meet-Me Room"), ("H2", "UR", "UPS Room"),
                                   ("BTT", "UR", "UPS Room"), ("FUEL", "GR", "Generator Yard"),
                                   ("WTR", "CP", "Central Plant"), ("THR", "OR", "Operations Room")):
            name = f"{code}-{dc}-{suffix}"
            d = devs[name]
            assert d["room"] == room and d["model_name"].startswith("Plant ")
            assert d["modbus_role"] == "rtu_slave" and d["modbus_gateway_ip"] == gw["mgmt_ip"]
            assert name in gw["modbus_children"]
            assert str(d["floor"]) == rooms[f"{dc}/{room}"]["level"]
            nid = next(n["id"] for n in topo["nodes"] if n["device"]["name"] == name)
            assert any(e["src"] == gw_id and e["dst"] == nid and e.get("layer") == "fieldbus" for e in topo["edges"])
            # inside its room
            r = rooms[f"{dc}/{room}"]
            assert 0 <= d["floor_x"] <= r["width_m"] and 0 <= d["floor_y"] <= r["depth_m"], name
        unit_ids = [d["modbus_unit_id"] for d in devs.values() if d.get("modbus_gateway_ip") == gw["mgmt_ip"]]
        assert len(unit_ids) == len(set(unit_ids)), "unit ids clash on the trunk"
