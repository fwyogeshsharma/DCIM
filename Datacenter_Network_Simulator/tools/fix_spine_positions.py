#!/usr/bin/env python3
"""Center each hall's spine nodes over its own leaves on the TOPOLOGY canvas.

The topology (network-graph) view draws nodes at their stored position{x,y}. The
Hall-B pod (added by promote_hall_b_pod.py) got spine x-positions spread across
the whole DC width, so some spines sit entirely to one side of their leaf cluster
and every leaf edge leaves the node on the same side. This spreads each hall's
spines evenly across the x-range its leaves occupy (keeping the spine y), so the
leaf mesh fans out symmetrically below each spine.

Canvas layout only — floor_x/floor_y and all edges are untouched. Idempotent.

Usage:
    python tools/fix_spine_positions.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def lead(nm: str) -> str:
    return (nm or "").split("-", 1)[0].upper()


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    spines = defaultdict(list)      # (dc, room) -> [node]
    leaf_x = defaultdict(list)      # (dc, room) -> [x]
    for n in nodes:
        dv = n["device"]
        if dv["device_type"] != "switch":
            continue
        key = (dv["datacenter"], dv.get("room"))
        code = lead(dv.get("name"))
        if code.startswith("SP"):
            spines[key].append(n)
        elif code.startswith("LF"):
            pos = n.get("position") or {}
            if "x" in pos:
                leaf_x[key].append(pos["x"])

    moved = 0
    for key, sp in sorted(spines.items()):
        xs = leaf_x.get(key)
        if not xs:
            continue
        lo, hi = min(xs), max(xs)
        sp.sort(key=lambda n: (n.get("position") or {}).get("x", 0))
        n = len(sp)
        for i, node in enumerate(sp):
            # evenly across [lo, hi] (single spine → centred)
            x = round((lo + hi) / 2) if n == 1 else round(lo + (hi - lo) * i / (n - 1))
            pos = node.get("position") or {"x": 0, "y": 0}
            pos["x"] = x
            node["position"] = pos
            moved += 1
        print(f"{key[0]}/{key[1]}: {n} spines spread over x[{lo:.0f}..{hi:.0f}]")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Repositioned {moved} spine nodes. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
