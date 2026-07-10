#!/usr/bin/env python3
"""Cluster each server hall's environmental sensors over its own leaf x-range on
the TOPOLOGY canvas.

The hall sensors (SEN…, LEAK…) carried scattered canvas positions (large random
gaps, some landing in the wrong hall/DC band). This lines each hall's sensors up
evenly across the x-range that hall's LEAVES occupy, on one tidy sensor row, so
they read cleanly in the management/cooling layers instead of being flung apart.

Canvas layout only — floor_x/floor_y and all edges are untouched. Idempotent.

Usage:
    python tools/fix_sensor_positions.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

SENSOR_Y = 1980        # the sensor tier row (below the OOB row)


def lead(nm: str) -> str:
    return (nm or "").split("-", 1)[0].upper()


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    leaf_x = defaultdict(list)          # (dc, room) -> [x]
    hall_sensors = defaultdict(list)    # (dc, room) -> [node]
    for n in nodes:
        dv = n["device"]
        room = dv.get("room") or ""
        if not room.startswith("Server Hall"):
            continue
        key = (dv["datacenter"], room)
        if dv["device_type"] == "switch" and lead(dv.get("name")).startswith("LF"):
            x = (n.get("position") or {}).get("x")
            if x is not None:
                leaf_x[key].append(x)
        elif dv["device_type"] == "sensor":
            hall_sensors[key].append(n)

    moved = 0
    for key, sensors in sorted(hall_sensors.items()):
        xs = leaf_x.get(key)
        if not xs:
            continue
        lo, hi = min(xs), max(xs)
        sensors.sort(key=lambda n: n["device"].get("name") or "")
        m = len(sensors)
        for i, n in enumerate(sensors):
            x = round((lo + hi) / 2) if m == 1 else round(lo + (hi - lo) * i / (m - 1))
            n["position"] = {"x": x, "y": SENSOR_Y}
            moved += 1
        print(f"{key[0]}/{key[1]}: {m} sensors clustered over x[{lo:.0f}..{hi:.0f}]")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Repositioned {moved} sensors. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
