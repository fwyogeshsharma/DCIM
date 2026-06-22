#!/usr/bin/env python3
"""Add a labeled perimeter hot-aisle band behind a wall-flush, back-to-wall row.

After open_perimeter_aisle.py opened a ~1.2 m strip behind Row 1 (back/exhaust
side, facing 'S'), this marks that strip as a hot aisle so the viewer renders it
red (like HA1) instead of bare floor. Only triggers on a genuine perimeter aisle
(0.8-1.6 m gap) behind an S-facing row -- not large empty expansion space.
"""
import json, sys
from collections import defaultdict

FLOOR = "topologies/dual_dc_enterprise_floorplan.json"
HALF = 0.6
f = json.load(open(FLOOR, encoding="utf-8"))
devById = {d["id"]: d for d in f["devices"]}
rooms = f["floorplan"]["rooms"]

def is_hall(room): return "Server Hall" in room
def crah_only(r): 
    ds=[devById[i]["device_type"] for i in r["device_ids"]]; return ds and all(t=="crah" for t in ds)

by_hall = defaultdict(list)
for r in f["racks"]:
    if is_hall(r["room"]): by_hall[(r["datacenter"], r["room"])].append(r)

added = 0
for (dc, room), racks in sorted(by_hall.items()):
    it = [r for r in racks if not crah_only(r)]
    if not it: continue
    lowest_edge = min(r["floor_y"] - HALF for r in it)
    low_y = min(r["floor_y"] for r in it)
    low = [r for r in it if r["floor_y"] == low_y]
    facing = (low[0].get("rack_facing") or "")
    g = rooms[f"{dc}/{room}"]
    aisles = g.setdefault("aisles", [])
    if any(a.get("id") == "HA0" for a in aisles):
        print(f"  {dc}/{room}: HA0 already present"); continue
    if not (0.8 <= lowest_edge <= 1.6 and facing == "S"):
        print(f"  {dc}/{room}: no back-to-wall perimeter aisle (edge {lowest_edge:.2f}, facing '{facing}') -> skip")
        continue
    band = {"id": "HA0", "type": "hot", "between_rows": [0, low[0]["row"]],
            "y": round(lowest_edge / 2, 2), "width": round(lowest_edge, 2)}
    aisles.insert(0, band)
    added += 1
    print(f"  {dc}/{room}: added hot aisle HA0 at y={band['y']} width={band['width']} (behind row {low[0]['row']})")

json.dump(f, open(FLOOR, "w", encoding="utf-8"), indent=2)
print(f"Added {added} perimeter hot aisle(s).")
