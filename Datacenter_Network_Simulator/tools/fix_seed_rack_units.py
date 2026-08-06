#!/usr/bin/env python3
"""Resolve rack-U (rack_unit) collisions in a topology JSON.

Two devices cannot occupy the same U of the same rack. The seed had 8:

  * OOB1 + OOBM1 both at U40 in each hall's network rack (x4). The access OOB and
    the BMS OOB share the rack (correct — one network/MDA rack), but not the same U.
    FIX: move the BMS OOB (OOBM, one per rack) to the lowest free even U in its
    rack, clear of the access-OOB stack near the top. Moving the single OOBM is
    less disruptive than shifting the OOB block, and the fleet's stacker already
    scans used U, so it keeps avoiding whatever U the OOBM ends up on.

  * SRV01 + SEN both at U1 in a compute rack (x4). Environmental probes mount in
    the cold aisle (the floor-plan draws them as aisle dots, off floor_x/floor_y),
    not in a server's rack U.
    FIX: set the colliding sensor's rack_unit to 0 (zero-U / aisle-mounted), so it
    no longer claims a rack elevation slot.

Idempotent: a topology with no collisions is left unchanged. Canvas/floor
coordinates are untouched — this only edits rack_unit (RU elevation).

Usage:
    python tools/fix_seed_rack_units.py topologies/dual_dc_enterprise.json
    python tools/fix_seed_rack_units.py topologies/dual_dc_enterprise.json --check
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def _role(name: str) -> str:
    return "".join(c for c in (name or "").split("-", 1)[0] if c.isalpha()).upper()


def _rack(d: dict):
    return (d.get("datacenter"), d.get("room"), d.get("rack_row"), d.get("rack_num"))


def _collisions(nodes) -> dict:
    seen = collections.defaultdict(list)
    for n in nodes:
        d = n["device"]
        if d.get("rack_unit") and d.get("rack_num"):
            seen[(_rack(d), d["rack_unit"])].append(d)
    return {k: v for k, v in seen.items() if len(v) > 1}


def _used_units(nodes, rack) -> set:
    return {d["rack_unit"] for n in nodes if _rack(d := n["device"]) == rack
            and d.get("rack_unit")}


def main(argv) -> int:
    path = argv[1]
    check_only = "--check" in argv
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    col = _collisions(nodes)
    if not col:
        print("No rack-U collisions. Nothing to do.")
        return 0

    moved = 0
    for (rack, unit), devs in sorted(col.items(), key=lambda kv: str(kv[0])):
        roles = {_role(d["name"]) for d in devs}

        # OOB + OOBM in one network rack, same U → move the BMS OOB (OOBM) down.
        if roles == {"OOB", "OOBM"}:
            oobm = next(d for d in devs if _role(d["name"]) == "OOBM")
            used = _used_units(nodes, rack)
            new_u = next((u for u in range(2, 43, 2) if u not in used), None)
            if new_u is not None:
                print(f"  {oobm['name']}: U{oobm['rack_unit']} -> U{new_u} "
                      f"(off the OOB stack)")
                oobm["rack_unit"] = new_u
                moved += 1
            continue

        # Server + environmental sensor at the same U → sensor is aisle-mounted (U0).
        if "SEN" in roles or any(d.get("device_type") == "sensor" for d in devs):
            for d in devs:
                if d.get("device_type") == "sensor" or _role(d["name"]) == "SEN":
                    print(f"  {d['name']}: U{d['rack_unit']} -> U0 (aisle-mounted)")
                    d["rack_unit"] = 0
                    moved += 1
            continue

        print(f"  !! unhandled collision at {rack} U{unit}: "
              f"{[d['name'] for d in devs]}")

    left = _collisions(nodes)
    print(f"\nResolved {moved} device placement(s); {len(left)} collision(s) remain.")
    if left:
        for (rack, unit), devs in left.items():
            print(f"  still colliding {rack} U{unit}: {[d['name'] for d in devs]}")
        return 1

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
