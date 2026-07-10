#!/usr/bin/env python3
"""Shift the CRAH row and the facility/plant block down the TOPOLOGY canvas so the
generator (and UPS) no longer sit on the CRAH row.

The generator (Generator Room) and the CRAH row were both at y=2680, overlapping.
This nudges the CRAH row down a little and drops the whole facility/plant block
(Central Plant, Generator, Mechanical, UPS Room, Roof) further down, keeping every
node's horizontal position. So the stack reads: … RPP row → CRAH → facility/plant.

Canvas layout only — floor_x/floor_y and all edges untouched. Idempotent-ish
(re-running shifts again, so run once after the other position fixes).

Usage:
    python tools/shift_plant_crah_down.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CRAH_DY = 80          # CRAH row nudge
FACILITY_DY = 300     # facility/plant block drop
FACILITY_ROOMS = {"Central Plant", "Generator Room", "Mechanical Room",
                  "UPS Room", "Roof"}


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    crah = fac = 0
    for n in topo["nodes"]:
        dv = n["device"]
        pos = n.get("position")
        if not pos or "y" not in pos:
            continue
        if dv["device_type"] == "crah":
            pos["y"] += CRAH_DY
            crah += 1
        elif dv.get("room") in FACILITY_ROOMS:
            pos["y"] += FACILITY_DY
            fac += 1
    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Shifted {crah} CRAH down {CRAH_DY}px, {fac} facility/plant devices down "
          f"{FACILITY_DY}px. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
