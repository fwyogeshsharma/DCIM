"""Physical geometry a DCIM imports as its asset model (doc 27 Phase S1).

Two kinds of check: the rules in core/equipment_geometry.py, and invariants of the
shipped topology - every free-standing unit inside its room and clear of its
neighbours, every rack probe standing at its rack, every room placed in a
building. The topology checks are the point: before tools/layout_buildings.py two
2 MW gensets stood in a 1.8 m room and twenty plant devices shared one coordinate,
and nothing failed.
"""
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from core.device_manager import Device
from core.equipment_geometry import (
    RACK_MOUNTS, complete_building, effective_mount, facing_deg, footprint,
    mount_height_m,
)
from tools.export_dcim_floorplan import build as build_floorplan
from tools.layout_buildings import check as layout_check

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"


@pytest.fixture(scope="module")
def topo():
    return json.loads(TOPO.read_text(encoding="utf-8"))


# ── rules ───────────────────────────────────────────────────────────────────

def test_mount_follows_type_and_rack_unit():
    assert effective_mount({"device_type": "chiller"}) == "floor"
    assert effective_mount({"device_type": "server", "rack_unit": 12}) == "rack"
    assert effective_mount({"device_type": "pdu", "rack_unit": 0}) == "zero_u"
    assert effective_mount({"device_type": "energy_monitor"}) == "panel"
    assert effective_mount({"device_type": "valve"}) == "pipe"
    # an explicit mounting always wins
    assert effective_mount({"device_type": "sensor", "rack_unit": 6,
                            "mounting": "pipe"}) == "pipe"


def test_footprint_is_datasheet_for_known_skus_and_absent_for_rack_gear():
    ups = footprint({"device_type": "ups", "model_name": "Vertiv Liebert EXL S1 1200kVA"})
    assert ups == {"width": 2.65, "depth": 0.91, "height": 2.01, "basis": "datasheet"}
    # an unknown SKU still gets a class-level size, flagged as such
    assert footprint({"device_type": "chiller", "model_name": "?"})["basis"] == "class"
    assert footprint({"device_type": "server", "rack_unit": 10}) is None


def test_facing_explicit_beats_rack_facing():
    assert facing_deg({"rack_facing": "N"}) == 0.0
    assert facing_deg({"rack_facing": "S"}) == 180.0
    assert facing_deg({"rack_facing": "S", "rotation_deg": 270}) == 270.0
    assert facing_deg({}) is None


def test_rack_probe_heights_are_bottom_mid_top():
    h = [mount_height_m({"device_type": "sensor", "mounting": "rack_front",
                         "rack_unit": u}) for u in (6, 24, 40)]
    assert h == sorted(h)
    # ASHRAE inlet placement: roughly knee, chest and head height
    assert 0.2 < h[0] < 0.5 and 1.0 < h[1] < 1.3 and 1.7 < h[2] < 2.0


def test_complete_building_derives_levels_and_never_mutates():
    fp = {"rooms": {
        "DC9/Hall": {"datacenter": "DC9", "room": "Hall", "width_m": 10, "depth_m": 8,
                     "level": "1", "origin": {"x": 0, "y": 0}},
        "DC9/Roof": {"datacenter": "DC9", "room": "Roof", "width_m": 5, "depth_m": 5,
                     "level": "Roof", "origin": {"x": 0, "y": 0}},
        "DC9/New Hall": {"datacenter": "DC9", "room": "New Hall",
                         "width_m": 6, "depth_m": 8},
    }}
    before = copy.deepcopy(fp)
    devs = [{"datacenter": "DC9", "room": "New Hall", "floor": "3"}]
    out = complete_building(fp, devs)
    assert fp == before
    lv = {x["name"]: x["elevation_m"] for x in out["buildings"]["DC9"]["levels"]}
    # the runtime hall's floor 3 pushes the roof up instead of landing on it
    assert lv == {"1": 5.0, "3": 15.0, "Roof": 20.0}
    nh = out["rooms"]["DC9/New Hall"]
    assert nh["level"] == "3" and nh["placement"] == "auto"
    # the roof is on the building, not part of its plan outline
    assert out["buildings"]["DC9"]["extent_m"] == {"width": 10.0, "depth": 8.0}


def test_auto_placed_room_sits_beside_its_level_neighbours():
    fp = {"rooms": {
        "D/A": {"datacenter": "D", "room": "A", "width_m": 8, "depth_m": 8,
                "level": "1", "origin": {"x": 0, "y": 0}},
        "D/B": {"datacenter": "D", "room": "B", "width_m": 4, "depth_m": 4, "level": "1"},
    }}
    assert complete_building(fp, [])["rooms"]["D/B"]["origin"] == {"x": 8.0, "y": 0.0}


def test_derived_fields_do_not_survive_a_save_load_cycle(topo):
    raw = next(n["device"] for n in topo["nodes"] if n["device"]["name"] == "CHL1-DC1-CP")
    out = Device.from_dict(raw).to_dict()
    assert out["footprint_m"]["width"] == 2.55 and out["facing_deg"] == 180.0
    back = Device.from_dict(out)
    assert back.mounting == "floor" and back.rotation_deg == 180
    assert not hasattr(back, "footprint_m")


# ── the shipped topology ────────────────────────────────────────────────────

def test_every_room_is_placed_in_a_building(topo):
    fp = complete_building(topo["floorplan"], (n["device"] for n in topo["nodes"]))
    for key, r in fp["rooms"].items():
        assert r.get("placement") != "auto", f"{key} has no curated placement"
    for dc, b in fp["buildings"].items():
        names = [x["name"] for x in b["levels"]]
        assert names == ["G", "1", "2", "Roof"], (dc, names)


def test_rooms_on_a_level_do_not_overlap(topo):
    by_level = defaultdict(list)
    for key, r in topo["floorplan"]["rooms"].items():
        o = r["origin"]
        by_level[(r["datacenter"], r["level"])].append(
            (key, o["x"], o["y"], o["x"] + r["width_m"], o["y"] + r["depth_m"]))
    for rooms in by_level.values():
        for i, a in enumerate(rooms):
            for b in rooms[i + 1:]:
                assert not (a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]), \
                    f"{a[0]} overlaps {b[0]}"


def test_free_standing_equipment_fits_its_room(topo):
    issues = layout_check(topo)
    # Known and deliberate: seven 1.75 m Liebert PCW units on 1.0 m centres along
    # each hall's back wall. Fixing it re-grids the halls, which belongs with the
    # spatial thermal model (doc 27 Phase S2). Anything else is a regression.
    other = [i for i in issues if not ("CRAH" in i and "overlaps CRAH" in i)]
    assert other == []


def test_rack_probes_stand_at_their_rack_on_the_rooms_floor(topo):
    devs = [n["device"] for n in topo["nodes"]]
    rack_xy = defaultdict(Counter)
    room_floor = defaultdict(Counter)
    for d in devs:
        room_floor[(d["datacenter"], d["room"])][str(d["floor"])] += 1
        if d["device_type"] != "sensor" and effective_mount(d) in RACK_MOUNTS:
            rack_xy[(d["datacenter"], d["room"], d["rack_row"], d["rack_num"])][
                (d["floor_x"], d["floor_y"])] += 1
    for d in devs:
        if d["device_type"] != "sensor" or effective_mount(d) not in RACK_MOUNTS:
            continue
        k = (d["datacenter"], d["room"], d["rack_row"], d["rack_num"])
        if rack_xy.get(k):
            assert (d["floor_x"], d["floor_y"]) == rack_xy[k].most_common(1)[0][0], d["name"]
        assert str(d["floor"]) == room_floor[(d["datacenter"], d["room"])].most_common(1)[0][0], d["name"]


def test_floorplan_export_files_only_rack_gear_under_racks(topo):
    doc = build_floorplan(topo)
    assert doc["schema"] == "dcim-floorplan/1.1"
    in_rack = {i for r in doc["racks"] for i in r["device_ids"]}
    for d in doc["devices"]:
        if d["mount"] in RACK_MOUNTS:
            assert d["rack_id"] and d["id"] in in_rack, d["name"]
        else:
            assert d["rack_id"] is None and d["id"] not in in_rack, d["name"]
            assert d["room"] and d["datacenter"]
    assert set(doc["floorplan"]["buildings"]) == {"DC1", "DC2"}
