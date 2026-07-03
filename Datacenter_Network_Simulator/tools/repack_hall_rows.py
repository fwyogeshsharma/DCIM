"""Close gaps left in a row when racks are removed (e.g. the network core moving
out of Server Hall A leaves empty columns in the front row).

For every room/row, if the occupied rack columns have an internal gap, the
devices are shifted left so the columns are contiguous again (starting from the
row's first occupied column). All devices sharing a rack move together, and
rack_num is renumbered to match the new column.

Rows whose devices are NOT on the standard rack_x grid (e.g. the evenly-spread
back-wall CRAH row) are left untouched. Idempotent.

    python tools/repack_hall_rows.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import hall_geometry as geo  # noqa: E402

TOPO = "topologies/dual_dc_enterprise.json"


def _col(x: float):
    """Rack column (1-based) for a floor_x on the rack_x grid, else None."""
    k = (x - geo.rack_x(1)) / geo.RACK_PITCH
    n = round(k)
    return n + 1 if abs(k - n) < 1e-6 and n >= 0 else None


def main() -> None:
    with open(TOPO, "r", encoding="utf-8") as f:
        doc = json.load(f)
    devs = [n["device"] for n in doc["nodes"] if n.get("device")]

    # Only re-pack white-space halls (server halls / the network room). Facility
    # rooms — central plant, UPS/generator/mechanical — deliberately spread gear
    # across the room, so a "gap" there is intentional, not a removed rack.
    room_class = {(v.get("datacenter"), v.get("room")): v.get("class")
                  for v in doc.get("floorplan", {}).get("rooms", {}).values()}

    # Group by physical row: (dc, floor, room, floor_y).
    rows = defaultdict(list)
    for d in devs:
        if d.get("floor_x") is None or d.get("floor_y") is None:
            continue
        if room_class.get((d.get("datacenter"), d.get("room"))) != "white_space":
            continue
        rows[(d.get("datacenter"), d.get("floor"), d.get("room"), d.get("floor_y"))].append(d)

    moved = 0
    for key, members in rows.items():
        xs = sorted({d["floor_x"] for d in members})
        cols = [_col(x) for x in xs]
        if any(c is None for c in cols):
            continue                                   # off-grid row (e.g. CRAH) — skip
        if cols == list(range(1, len(cols) + 1)):
            continue                                   # already packed from column 1
        # Racks in a hall fill from the wall (column 1) — close leading AND
        # internal gaps by re-packing to a contiguous 1..N run.
        remap = {old_x: geo.rack_x(1 + i) for i, old_x in enumerate(xs)}
        newnum = {old_x: 1 + i for i, old_x in enumerate(xs)}
        for d in members:
            ox = d["floor_x"]
            if remap[ox] != ox:
                d["floor_x"] = remap[ox]
                d["rack_num"] = newnum[ox]
                moved += 1
        dc, _fl, room, y = key
        print(f"  {dc}/{room} y={y}: cols {cols} -> "
              f"{list(range(1, len(cols) + 1))}  ({len(members)} device(s))")

    if not moved:
        print("No gaps to close.")
        return
    with open(TOPO, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"Done: shifted {moved} device(s) to close row gaps.")


if __name__ == "__main__":
    main()
