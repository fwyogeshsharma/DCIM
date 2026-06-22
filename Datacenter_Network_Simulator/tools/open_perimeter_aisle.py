#!/usr/bin/env python3
"""Open a perimeter service/hot aisle behind wall-flush IT rows.

Some halls place the first IT row at floor_y=0.6 -> its back edge is flush at the
y=0 wall (no hot-aisle / service clearance behind the exhaust side). This shifts
the hall's IT rows AND their aisles inward (+Y) so the nearest IT-rack edge is at
least TARGET m from the wall, opening a perimeter aisle. The CRAH perimeter row
(against the back wall) and room geometry are left in place.

Floor-plan only: floor_x/floor_y live in the DCIM asset file, not the topology.
"""
import json, sys
from collections import defaultdict

FLOOR = "topologies/dual_dc_enterprise_floorplan.json"
TARGET = 1.2          # desired clearance from the y=0 wall (m) = one hot-aisle width
HALF = None           # half rack depth, from footprint

f = json.load(open(FLOOR, encoding="utf-8"))
HALF = (f["floorplan"]["rack_footprint"]["depth"]) / 2     # 0.6
devById = {d["id"]: d for d in f["devices"]}
rooms = f["floorplan"]["rooms"]


def is_hall(room): return "Server Hall" in room


def crah_only(rack):
    ds = [devById[i]["device_type"] for i in rack["device_ids"]]
    return len(ds) > 0 and all(t == "crah" for t in ds)


by_hall = defaultdict(list)
for r in f["racks"]:
    if is_hall(r["room"]):
        by_hall[(r["datacenter"], r["room"])].append(r)

shifted = []
for (dc, room), racks in sorted(by_hall.items()):
    it = [r for r in racks if not crah_only(r)]          # IT rows + end-of-row power
    crah = [r for r in racks if crah_only(r)]
    if not it:
        continue
    min_edge = min(r["floor_y"] - HALF for r in it)
    offset = round(TARGET - min_edge, 2)
    if offset <= 0.001:
        print(f"  {dc}/{room}: nearest IT edge {min_edge:.2f} m from wall -> ok, no shift")
        continue
    # collision guard: shifted IT must not reach the CRAH band
    new_max = max(r["floor_y"] + HALF for r in it) + offset
    crah_edge = min((r["floor_y"] - HALF for r in crah), default=rooms[f"{dc}/{room}"]["depth_m"])
    if new_max > crah_edge - 0.3:
        print(f"  !! {dc}/{room}: shift {offset} would reach CRAHs (newmax {new_max:.1f} vs {crah_edge:.1f}); skipped")
        continue
    for r in it:
        r["floor_y"] = round(r["floor_y"] + offset, 2)
    for a in rooms[f"{dc}/{room}"].get("aisles", []):
        a["y"] = round(a["y"] + offset, 2)
    shifted.append((dc, room, offset, min_edge))
    print(f"  {dc}/{room}: edge was {min_edge:.2f} m -> shifted IT rows +{offset} m "
          f"(now {min_edge+offset:.2f} m perimeter aisle)")

json.dump(f, open(FLOOR, "w", encoding="utf-8"), indent=2)
print(f"Shifted {len(shifted)} hall(s).")
