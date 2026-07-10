#!/usr/bin/env python3
"""Lay the RPPs that feed PDUs into one clean row per DC, each EV2 meter beside
the RPP it clamps onto.

The hall IT RPPs and the Network-Room RPPs both feed PDUs and should read as ONE
RPP row per DC. This evenly spreads all Server-Hall + Network-Room RPPs across the
DC's hall x-span on one row, then drops each EV2 immediately to the right of the
RPP it meters (same row). Central-Plant RPPs (a separate facility area below the
halls) get their own row with their EV2 alongside.

Run AFTER fix_hall_device_positions.py (this has the final say on RPP/EV2 rows).
Canvas layout only — floor_x/floor_y and all edges untouched. Idempotent.

Usage:
    python tools/fix_rpp_positions.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

RPP_Y = 2450                         # the shared hall+network RPP row
PLANT_RPP_Y = 2820
EV2_DX = 130                         # EV2 sits this far to the right of its RPP


def lead(nm: str) -> str:
    return (nm or "").split("-", 1)[0].upper()


def x_of(n) -> float:
    return (n.get("position") or {}).get("x", 0)


def spread(items, lo, hi, y):
    items.sort(key=x_of)
    m = len(items)
    for i, n in enumerate(items):
        x = round((lo + hi) / 2) if m <= 1 else round(lo + (hi - lo) * i / (m - 1))
        n["position"] = {"x": x, "y": y}
    return m


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]
    byid = {n["id"]: n for n in nodes}

    # EV2 -> the RPP it meters (its power neighbour that is an RPP).
    ev2_rpp = {}
    for e in topo["edges"]:
        if e.get("layer") != "power":
            continue
        a, b = byid.get(e["src"]), byid.get(e["dst"])
        if not a or not b:
            continue
        if a["device"]["device_type"] == "energy_monitor" and b["device"]["device_type"] == "rpp":
            ev2_rpp[a["id"]] = b["id"]
        elif b["device"]["device_type"] == "energy_monitor" and a["device"]["device_type"] == "rpp":
            ev2_rpp[b["id"]] = a["id"]

    dc_hall_x = defaultdict(list)       # dc -> hall leaf xs (the RPP row span)
    hallnet_rpp = defaultdict(list)     # dc -> [rpp]  (Server Hall + Network Room)
    plant_rpp = defaultdict(list)
    plant_x = defaultdict(list)
    ev2s = []
    for n in nodes:
        dv = n["device"]
        dc, room = dv["datacenter"], dv.get("room") or ""
        t = dv["device_type"]
        if t == "switch" and lead(dv.get("name")).startswith("LF") \
                and room.startswith("Server Hall") and "x" in (n.get("position") or {}):
            dc_hall_x[dc].append(x_of(n))
        hall_or_net = room.startswith("Server Hall") or room == "Network Room"
        if t == "rpp":
            if hall_or_net:
                hallnet_rpp[dc].append(n)
            elif room == "Central Plant":
                plant_rpp[dc].append(n)
        elif t == "energy_monitor":
            ev2s.append(n)
        elif room == "Central Plant" and "x" in (n.get("position") or {}):
            plant_x[dc].append(x_of(n))

    moved = 0
    for dc in sorted(set(hallnet_rpp) | set(plant_rpp)):
        xs = dc_hall_x.get(dc)
        if xs:
            moved += spread(hallnet_rpp.get(dc, []), min(xs), max(xs), RPP_Y)
        px = plant_x.get(dc)
        if px:
            moved += spread(plant_rpp.get(dc, []), min(px), max(px), PLANT_RPP_Y)
        print(f"{dc}: {len(hallnet_rpp.get(dc, []))} hall+net RPP row; "
              f"{len(plant_rpp.get(dc, []))} plant RPP")

    # Each EV2 sits just right of the RPP it meters, on the same row.
    for n in ev2s:
        rpp = byid.get(ev2_rpp.get(n["id"]))
        if rpp and rpp.get("position"):
            n["position"] = {"x": rpp["position"]["x"] + EV2_DX, "y": rpp["position"]["y"]}
            moved += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Repositioned {moved} RPP/EV2 nodes (EV2 beside its RPP). Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
