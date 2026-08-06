#!/usr/bin/env python3
"""Recompute every topology-canvas coordinate in a topology JSON.

A thin CLI over core/canvas_layout.py, which holds the rules and is shared with
core/fleet_lifecycle (so a device the fleet adds to a LIVE topology lands on the
same grid a batch run would give it). Read that module's docstring for the rules.

This is the single owner of position.x/y. Every tool that adds a node writes
{"x": 0, "y": 0} and leaves placement to this. Physical placement (rack_row /
rack_num / floor_x / floor_y and the room extents) is a different concern and is
NOT touched here — see fix_electrical_positions.py and the floorplan export.

Replaced seven tools that each owned a slice of the layout and disagreed:
fix_hall_device_positions, fix_oob_positions, fix_rpp_positions,
fix_sensor_positions, fix_network_room_positions, fix_spine_positions, and
shift_plant_crah_down (which was explicitly non-idempotent).

Usage:
    python tools/layout_canvas.py topologies/dual_dc_enterprise.json
    python tools/layout_canvas.py topologies/dual_dc_enterprise.json --check
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.canvas_layout import layout_all, check  # noqa: E402


def main(argv) -> int:
    path = argv[1]
    check_only = "--check" in argv
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    records = [(n["id"], n["device"]["name"],
                n["device"].get("datacenter"), n["device"].get("room"))
               for n in nodes]

    positions, rects, notes = layout_all(records)
    for note in notes:
        print(f"  note {note}")

    for n in nodes:
        x, y = positions[n["id"]]
        n["position"] = {"x": x, "y": y}

    print(f"\n{len(rects)} room rectangles across "
          f"{len({dc for dc, _ in rects})} datacenters, {len(nodes)} nodes placed")
    for (dc, room), (x0, y0, w, h) in sorted(rects.items()):
        print(f"  {dc}/{room:16s} x[{x0:6.0f}..{x0 + w:6.0f}] y[{y0:6.0f}..{y0 + h:6.0f}]")

    errs = check(positions, records, rects)
    if errs:
        print(f"\n!! {len(errs)} invariant violations:")
        for e in errs[:15]:
            print("   ", e)
        return 1
    print("\ninvariants OK: no coincident nodes, no overlapping boxes, "
          "every node inside its room, room rects disjoint")

    if check_only:
        print("--check: nothing written")
        return 0
    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv))
