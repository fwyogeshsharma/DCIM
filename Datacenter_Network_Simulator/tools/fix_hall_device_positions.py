#!/usr/bin/env python3
"""Cluster in-hall device tiers (CDU, rack PDU) over each hall's leaf x-range on
the TOPOLOGY canvas.

Like the OOB/sensor fixes: these devices carried scattered canvas positions (big
gaps, some leaking into another hall/DC band). This lines each hall's devices of a
given type up evenly across the x-range that hall's LEAVES occupy. Types with many
nodes (rack PDUs) wrap onto multiple rows so a single row doesn't overlap.

Canvas layout only — floor_x/floor_y and all edges are untouched. Idempotent.

Usage:
    python tools/fix_hall_device_positions.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

# device_type -> (base row y, max nodes per row before wrapping)
TIERS = [
    ("cdu", 2100, 12),
    ("pdu", 2260, 8),
    ("rpp", 2450, 12),
    ("energy_monitor", 2560, 12),
    ("crah", 2680, 12),
]
ROW_GAP = 90


def lead(nm: str) -> str:
    return (nm or "").split("-", 1)[0].upper()


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    leaf_x = defaultdict(list)                         # (dc, room) -> [x]
    by_type = defaultdict(lambda: defaultdict(list))   # dtype -> (dc,room) -> [node]
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
        for dtype, _, _ in TIERS:
            if dv["device_type"] == dtype:
                by_type[dtype][key].append(n)

    moved = 0
    for dtype, base_y, per_row in TIERS:
        for key, items in sorted(by_type[dtype].items()):
            xs = leaf_x.get(key)
            if not xs:
                continue
            lo, hi = min(xs), max(xs)
            items.sort(key=lambda n: n["device"].get("name") or "")
            m = len(items)
            rows = max(1, -(-m // per_row))            # ceil
            per = -(-m // rows)                        # even split across rows
            for i, n in enumerate(items):
                r, c = divmod(i, per)
                cols = min(per, m - r * per)
                x = round((lo + hi) / 2) if cols == 1 else round(lo + (hi - lo) * c / (cols - 1))
                n["position"] = {"x": x, "y": base_y + r * ROW_GAP}
                moved += 1
            print(f"{key[0]}/{key[1]}: {m} {dtype} over x[{lo:.0f}..{hi:.0f}] in {rows} row(s)")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Repositioned {moved} devices. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
