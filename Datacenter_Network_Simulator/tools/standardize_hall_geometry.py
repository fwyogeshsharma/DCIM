#!/usr/bin/env python3
"""Standardize every server hall to one geometry across both datacenters, and put
the RPPs at identical columns.

The two DCs had drifted apart, with internally inconsistent extents:

    DC1 halls : width 8.4 m  but racks_per_row = 9
    DC2 halls : width 7.8 m  but racks_per_row = 13   (7.8 m holds only 12)

The 78-rack/DC build-out target only closes at 13 racks per row
(3 compute rows x 13 racks x 2 halls = 78), and 13 racks need an 8.4 m row. So the
design-correct geometry is 8.4 m wide, 13 racks/row for EVERY server hall.

This sets that extent on all server halls (fixing DC1's racks_per_row and DC2's
width) and moves each hall's RPPA/RPPB — together with the EV2 meter sharing its
rack — to identical flanking columns:

    RPPA -> col 4    (first slot past the network cluster; free in both DCs,
                      whose spines/OOB occupy cols 1-3 / 1-2)
    RPPB -> col 13   (far end of the 13-wide row)

RPP feeds are logical power edges, not distance-based, so no power/mgmt wiring
changes — only the panel's rack column, floor_x and the R<row>-<col> name suffix.

NOT addressed (separate fabric-level drift): the DCs still differ in spine count /
packing (DC1 4 spines in 2 racks, DC2 3 spines in 1 rack) and OOB column. DC2's
curated MPPs/CRAHs keep their 7.8 m floor_x, so they sit ~0.6 m off the new far
wall until re-seeded — cosmetic.

Idempotent. Run tools/layout_canvas.py and re-export the floorplan afterwards.

Usage:
    python tools/standardize_hall_geometry.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import hall_geometry as geo  # noqa: E402

WIDTH_M = 8.4
RACKS_PER_ROW = 13
RPP_COL = {"RPPA": 4, "RPPB": 13}


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]
    rooms = topo.get("floorplan", {}).get("rooms", {})

    def code(n) -> str:
        return (n["device"].get("name") or "").split("-", 1)[0]

    halls = sorted({(n["device"]["datacenter"], n["device"]["room"])
                    for n in nodes
                    if (n["device"].get("room") or "").startswith("Server Hall")
                    and n["device"].get("datacenter")})

    ext_fixed = moved = 0
    for dc, room in halls:
        key = f"{dc}/{room}"
        ext = rooms.get(key)
        if ext is not None and (ext.get("width_m") != WIDTH_M
                                or ext.get("racks_per_row") != RACKS_PER_ROW):
            old = (ext.get("width_m"), ext.get("racks_per_row"))
            ext["width_m"] = WIDTH_M
            ext["racks_per_row"] = RACKS_PER_ROW
            ext_fixed += 1
            print(f"{key}: extent {old[0]} m / {old[1]} rpr -> {WIDTH_M} m / {RACKS_PER_ROW} rpr")

        for rppcode, target in RPP_COL.items():
            rpp = next((n for n in nodes if n["device"].get("datacenter") == dc
                        and n["device"].get("room") == room and code(n) == rppcode), None)
            if rpp is None:
                continue
            row = rpp["device"].get("rack_row")
            cur = rpp["device"].get("rack_num")
            if cur == target:
                continue
            # Guard: refuse to move onto a column another device already holds in
            # this row (would double-book the network row).
            clash = [n for n in nodes if n["device"].get("datacenter") == dc
                     and n["device"].get("room") == room
                     and n["device"].get("rack_row") == row
                     and n["device"].get("rack_num") == target]
            if clash:
                print(f"{key}: {rppcode} target col {target} occupied by "
                      f"{clash[0]['device']['name']} — skipped")
                continue
            # Move the RPP and its rack-mate EV2 meter together.
            rackmates = [n for n in nodes if n["device"].get("datacenter") == dc
                         and n["device"].get("room") == room
                         and n["device"].get("rack_row") == row
                         and n["device"].get("rack_num") == cur]
            fx = round(geo.rack_x(target), 4)
            for n in rackmates:
                dv = n["device"]
                dv["rack_num"] = target
                dv["floor_x"] = fx
                parts = dv["name"].split("-")
                if len(parts) >= 2:
                    parts[-1] = f"{target:02d}"
                    dv["name"] = "-".join(parts)
                moved += 1
            print(f"{key}: {rppcode} col {cur} -> {target} (fx={fx}), "
                  f"moved {len(rackmates)} device(s)")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nExtents fixed: {ext_fixed}; devices repositioned: {moved}. Wrote {p}\n"
          f"Next: python tools/layout_canvas.py {p}  (then re-export the floorplan).")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
