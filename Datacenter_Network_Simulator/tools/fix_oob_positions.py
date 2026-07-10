#!/usr/bin/env python3
"""Cluster each server hall's OOB switches over its own leaf x-range on the
TOPOLOGY canvas.

The hall access-OOB + BMS switches were spread across the whole DC width, so the
outermost ones (e.g. OOB5-DC1-HB) overshot into the neighbouring DC's band or ran
off the right edge (OOB4-DC2-HB). This re-spreads each hall's OOB/OOBM switches
evenly across the x-range that hall's LEAVES occupy, at one tidy row, so they sit
under their own hall and stay inside the DC band. OOB-CORE (OOBC, one per DC) and
the facility/network-room OOBs are left where they are.

Canvas layout only — floor_x/floor_y and all edges are untouched. Idempotent.

Usage:
    python tools/fix_oob_positions.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

OOB_Y = 1780        # the access/BMS OOB tier row (just below the leaves)
CORE_Y = 1650       # OOB-CORE sits one row above the access OOBs it aggregates


def lead(nm: str) -> str:
    return (nm or "").split("-", 1)[0].upper()


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    leaf_x = defaultdict(list)          # (dc, room) -> [x]
    dc_leaf_x = defaultdict(list)       # dc -> [x]  (all halls, for centring the core)
    hall_oobs = defaultdict(list)       # (dc, room) -> [node]  (access + BMS)
    cores = []                          # OOB-CORE nodes
    for n in nodes:
        dv = n["device"]
        room = dv.get("room") or ""
        code = lead(dv.get("name"))
        if dv["device_type"] == "oob_switch" and code.startswith("OOBC"):
            cores.append(n)
            continue
        if not room.startswith("Server Hall"):
            continue
        key = (dv["datacenter"], room)
        if dv["device_type"] == "switch" and code.startswith("LF"):
            x = (n.get("position") or {}).get("x")
            if x is not None:
                leaf_x[key].append(x)
                dc_leaf_x[dv["datacenter"]].append(x)
        elif dv["device_type"] == "oob_switch" and code.startswith("OOB"):   # access + BMS
            hall_oobs[key].append(n)

    moved = 0
    for key, oobs in sorted(hall_oobs.items()):
        xs = leaf_x.get(key)
        if not xs:
            continue
        lo, hi = min(xs), max(xs)
        oobs.sort(key=lambda n: n["device"].get("name") or "")
        m = len(oobs)
        for i, n in enumerate(oobs):
            x = round((lo + hi) / 2) if m == 1 else round(lo + (hi - lo) * i / (m - 1))
            n["position"] = {"x": x, "y": OOB_Y}
            moved += 1
        print(f"{key[0]}/{key[1]}: {m} OOB switches clustered over x[{lo:.0f}..{hi:.0f}]")

    # OOB-CORE: centred over its DC's halls, one row above the access OOBs.
    for n in cores:
        dc = n["device"]["datacenter"]
        xs = dc_leaf_x.get(dc)
        cx = round((min(xs) + max(xs)) / 2) if xs else (n.get("position") or {}).get("x", 0)
        n["position"] = {"x": cx, "y": CORE_Y}
        moved += 1
        print(f"{dc}: OOB-CORE {n['device'].get('name')} centred at ({cx}, {CORE_Y})")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Repositioned {moved} OOB switches. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
