#!/usr/bin/env python3
"""Relocate CRAH units from server racks to dedicated perimeter floor positions.

CRAHs (Vertiv Liebert PCW = Perimeter Chilled Water) are large floor-standing
downflow air handlers that line the room perimeter and pressurize the underfloor
plenum -- they are NOT rack-mounted and must not sit inside an IT rack. The
generator had bucketed all 4 CRAHs of each hall into that hall's server RACK1 at
U0. This gives each CRAH its own floor cell along the hall's back wall.

Synthetic perimeter row = R9 (well clear of IT rows 1-4). rack_unit stays 0
(floor-standing). Edits topology + floor-plan together.
"""
import json, sys
from collections import defaultdict

TOPO = "topologies/dual_dc_enterprise.json"
FLOOR = "topologies/dual_dc_enterprise_floorplan.json"
PERIM_ROW = 9

topo = json.load(open(TOPO, encoding="utf-8"))
floor = json.load(open(FLOOR, encoding="utf-8"))
rooms = floor["floorplan"]["rooms"]
rackById = {r["rack_id"]: r for r in floor["racks"]}
topoDev = {n["device"]["id"]: n["device"] for n in topo["nodes"]}

# group CRAH floor-plan devices by hall
crah_by_hall = defaultdict(list)
for d in floor["devices"]:
    if d["device_type"] == "crah":
        r = rackById[d["rack_id"]]
        crah_by_hall[(r["datacenter"], r["room"], r["floor"])].append(d)

new_racks = []
for (dc, room, fl), crahs in sorted(crah_by_hall.items()):
    g = rooms.get(f"{dc}/{room}", {})
    W = g.get("width_m", 8.4); D = g.get("depth_m", 9.6)
    slug = room.replace(" ", "")
    src_rack = rackById[crahs[0]["rack_id"]]
    n = len(crahs)
    for i, d in enumerate(sorted(crahs, key=lambda x: x["name"])):
        fx = round(W * (i + 0.5) / n, 2)     # spread evenly along the wall
        fy = round(D - 0.6, 2)               # back-wall perimeter row
        rid = f"{dc}:{slug}:F{fl}:R{PERIM_ROW}:RACK{i+1}"
        new_racks.append({
            "rack_id": rid, "datacenter": dc,
            "datacenter_city": src_rack.get("datacenter_city"),
            "room": room, "floor": fl, "row": PERIM_ROW, "rack_num": i + 1,
            "floor_x": fx, "floor_y": fy, "rack_facing": None,
            "cold_aisle": None, "hot_aisle": None,
            "device_ids": [d["id"]], "device_count": 1,
            "it_power_draw_w": d.get("power_draw_w") or 0,
        })
        d["rack_id"] = rid                                  # floor-plan device -> perimeter
        nd = topoDev.get(d["id"])                           # topology node
        if nd:
            nd["rack_row"], nd["rack_num"], nd["rack_unit"] = PERIM_ROW, i + 1, 0

# rebuild old racks (drop CRAHs already re-pointed), then append perimeter racks
members = defaultdict(list)
for d in floor["devices"]:
    members[d["rack_id"]].append(d)
kept = []
for r in floor["racks"]:
    ds = members.get(r["rack_id"], [])
    if not ds:
        continue
    r["device_ids"] = [x["id"] for x in ds]
    r["device_count"] = len(ds)
    r["it_power_draw_w"] = sum(x.get("power_draw_w") or 0 for x in ds)
    kept.append(r)
floor["racks"] = kept + new_racks
floor["summary"]["racks"] = len(floor["racks"])

json.dump(topo, open(TOPO, "w", encoding="utf-8"), indent=2)
json.dump(floor, open(FLOOR, "w", encoding="utf-8"), indent=2)
print(f"Relocated {sum(len(v) for v in crah_by_hall.values())} CRAHs to perimeter (row {PERIM_ROW})")
print(f"Created {len(new_racks)} perimeter CRAH cells; floor-plan racks now {len(floor['racks'])}")
for r in new_racks:
    print(f"  {r['rack_id']:34} ({r['floor_x']},{r['floor_y']}) m")
