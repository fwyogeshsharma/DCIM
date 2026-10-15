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
    OUTDOOR_LEVEL, RACK_MOUNTS, complete_building, complete_site, effective_mount,
    facing_deg, footprint, mount_height_m,
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
        # the yard is in the compound, not a level of the building
        yard = fp["rooms"][f"{dc}/Generator Yard"]
        assert yard["level"] == OUTDOOR_LEVEL and yard["class"] == "outdoor"


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
    assert doc["schema"] == "dcim-floorplan/1.2"
    in_rack = {i for r in doc["racks"] for i in r["device_ids"]}
    for d in doc["devices"]:
        if d["mount"] in RACK_MOUNTS:
            assert d["rack_id"] and d["id"] in in_rack, d["name"]
        else:
            assert d["rack_id"] is None and d["id"] not in in_rack, d["name"]
            assert d["room"] and d["datacenter"]
    assert set(doc["floorplan"]["buildings"]) == {"DC1", "DC2"}


# ── S3: the compound, the moved rooms, the fabric (docs/27 §8b, §8c) ────────

def test_the_gensets_stand_in_a_yard_and_the_chillers_on_the_ground(topo):
    devs = [n["device"] for n in topo["nodes"]]
    gens = [d for d in devs if d["device_type"] == "generator"]
    assert gens and all(d["room"] == "Generator Yard" and d["floor"] == OUTDOOR_LEVEL for d in gens)
    assert all(d["name"].endswith("-GR") for d in gens)          # the room code did not change
    chillers = [d for d in devs if d["device_type"] == "chiller"]
    assert chillers and all(d["room"] == "Central Plant" and d["floor"] == "G" for d in chillers)
    assert not any(d["room"] == "Generator Room" for d in devs)


def test_every_device_stands_on_its_rooms_level(topo):
    rooms = topo["floorplan"]["rooms"]
    for n in topo["nodes"]:
        d = n["device"]
        r = rooms.get(f"{d.get('datacenter')}/{d.get('room')}")
        if r is None:
            continue
        assert str(d.get("floor")) == str(r["level"]), (d["name"], d.get("floor"), r["level"])


def test_support_rooms_exist_with_their_class(topo):
    for dc in ("DC1", "DC2"):
        for room in ("Staging & Storage", "Operations Room", "Meet-Me Room"):
            assert topo["floorplan"]["rooms"][f"{dc}/{room}"]["class"] == "support", (dc, room)


def test_complete_site_adds_fabric_and_a_compound_without_touching_the_rooms(topo):
    fp = topo["floorplan"]
    before = copy.deepcopy(fp)
    out = complete_site(fp, (n["device"] for n in topo["nodes"]))
    assert fp == before
    b = out["buildings"]["DC1"]
    # the service strip (corridor + lobby/dock/core band) is part of the building
    assert b["extent_m"] == {"width": 25.0, "depth": 21.0}
    fab = out["fabric"]["DC1"]
    kinds = {(f["level"], f["kind"]) for f in fab}
    for lv in ("G", "1", "2"):
        assert (lv, "corridor") in kinds and (lv, "core") in kinds
    assert ("G", "lobby") in kinds and ("G", "dock") in kinds and ("G", "entrance") in kinds
    # one door per room; the halls' and the UPS room's are the ones the BMS watches
    doors = {f["attrs"]["room"]: f["attrs"] for f in fab if f["kind"] == "door" and "room" in f.get("attrs", {})}
    assert doors["DC1/Server Hall A"]["monitored"] and doors["DC1/UPS Room"]["monitored"]
    assert not doors["DC1/Central Plant"]["monitored"] and not doors["DC1/Staging & Storage"]["monitored"]
    comp = out["compound"]["DC1"]
    zk = {z["kind"] for z in comp["zones"]}
    assert {"fence", "gate", "driveway", "guardhouse", "road", "apron", "parking", "substation", "generator_yard",
            "fuel_tank", "water_tank", "fire_pump_house", "telecom_entrance"} <= zk
    yard = next(z for z in comp["zones"] if z["kind"] == "generator_yard")
    assert yard["attrs"]["room"] == "DC1/Generator Yard"
    # the yard lies east of the building, inside the fence
    (fx0, fy0), (fx1, fy1) = comp["outline_m"][0], comp["outline_m"][2]
    assert all(fx0 <= x <= fx1 and fy0 <= y <= fy1 for x, y in yard["polygon_m"])
    assert min(x for x, _ in yard["polygon_m"]) >= b["outline_m"][1][0]


def test_an_outdoor_room_never_sets_the_buildings_levels_or_outline():
    fp = {"rooms": {
        "D/Hall": {"datacenter": "D", "room": "Hall", "width_m": 10, "depth_m": 8, "level": "1", "origin": {"x": 0, "y": 0}},
        "D/Generator Yard": {"datacenter": "D", "room": "Generator Yard", "class": "outdoor",
                             "width_m": 10, "depth_m": 12, "level": OUTDOOR_LEVEL},
    }}
    out = complete_site(fp, [])
    b = out["buildings"]["D"]
    assert [x["name"] for x in b["levels"]] == ["1"]
    assert b["outline_m"][1][0] == 10.0                      # the yard did not widen the building
    yard = out["rooms"]["D/Generator Yard"]
    assert yard["placement"] == "auto" and yard["origin"]["x"] >= 14.0   # set down east of it
    assert out["compound"]["D"]["zones"][-2]["kind"] == "generator_yard"
    assert out["compound"]["D"]["zones"][-1]["kind"] == "fuel_tank"


def test_the_export_carries_fabric_and_compound(topo):
    doc = build_floorplan(topo)
    assert set(doc["floorplan"]["fabric"]) == {"DC1", "DC2"} == set(doc["floorplan"]["compound"])
    assert doc["floorplan"]["rooms"]["DC1/Generator Yard"]["class"] == "outdoor"


def test_every_room_door_opens_onto_floor_inside_the_building(topo):
    """A door goes on a wall with floor outside it - the corridor, or the
    passages the free floor between rooms makes - never in the building's
    face just because the room does not reach the corridor. A room boxed in
    on every side opens into the hall beside it."""
    fab = complete_site(topo["floorplan"], (n["device"] for n in topo["nodes"]))["fabric"]["DC1"]
    door = {f["attrs"]["room"]: f["polygon_m"] for f in fab if f["kind"] == "door" and "room" in f.get("attrs", {})}
    mid = lambda poly: ((poly[0][0] + poly[2][0]) / 2, (poly[0][1] + poly[2][1]) / 2)  # noqa: E731
    # the halls stop 2.7 m short of the corridor: the door is in the south wall, onto that strip
    assert mid(door["DC1/Server Hall A"])[1] == 12.3 and mid(door["DC1/Server Hall B"])[1] == 12.3
    # the UPS room's only free wall is the metre beside the plant: a single leaf there
    x, y = mid(door["DC1/UPS Room"])
    assert y == 6.0 and 8.0 < x < 9.0 and abs(door["DC1/UPS Room"][2][0] - door["DC1/UPS Room"][0][0] - 0.9) < 1e-6
    # the mechanical room opens east onto the same gap
    assert mid(door["DC1/Mechanical Room"])[0] == 8.0
    # the network room is boxed in: it opens west, into the hall it serves
    assert mid(door["DC1/Network Room"])[0] == 12.2
    # no room's door is in the building's outer face
    assert not any(f["attrs"].get("external") for f in fab if f["kind"] == "door" and "room" in f.get("attrs", {}))
    # the free floor between rooms is drawn as passages, the gap beside the plant among them
    passages = [f["polygon_m"] for f in fab if f["kind"] == "corridor" and f["name"] == "Passage"]
    assert [p[0], p[2]] == [[8.0, 6.0], [9.0, 15.0]] if (p := next(q for q in passages if q[0] == [8.0, 6.0])) else False
