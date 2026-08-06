#!/usr/bin/env python3
"""De-collide the Network Room OOB-management switch (OOBM) from the device it
shares a U with.

In each DC's Network Room the OOB-mgmt switch (OOBM1) was authored at the same
rack_unit as a load balancer (LB2 at U40) — two devices stacked on one U, which
is physically impossible. This drops OOBM1 to the next free U below the rack's
occupied stack (2U network cadence), clearing the double-book. Nothing else moves;
rack/room/power/uplinks and canvas position are unchanged (same rack).

Idempotent: an OOBM that no longer collides is left where it is.

Usage:
    python tools/fix_oobm_rack_unit.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOM = "Network Room"


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    def code(n) -> str:
        return (n["device"].get("name") or "").split("-", 1)[0]

    fixed = 0
    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        oobm = next((n for n in nodes
                     if n["device"].get("datacenter") == dc
                     and n["device"].get("room") == ROOM
                     and code(n) == "OOBM1"), None)
        if oobm is None:
            print(f"{dc}: no OOBM1 in {ROOM} — skipped")
            continue
        d = oobm["device"]
        row, num, u = d.get("rack_row"), d.get("rack_num"), d.get("rack_unit")

        # Everything else physically in this rack (exclude 0U power gear and self).
        rackmates = [n["device"] for n in nodes
                     if n["device"].get("datacenter") == dc
                     and n["device"].get("room") == ROOM
                     and n["device"].get("rack_row") == row
                     and n["device"].get("rack_num") == num
                     and n is not oobm
                     and n["device"].get("device_type") not in ("pdu", "rpp", "energy_monitor")]
        occupied = {int(m.get("rack_unit")) for m in rackmates
                    if m.get("rack_unit") is not None}
        if u not in occupied:
            print(f"{dc}: OOBM1 at U{u} does not collide — skipped")
            continue

        # Next free U stepping DOWN from the lowest occupied unit, 2U cadence to
        # match the network row's spacing.
        floor_u = min(occupied)
        target = floor_u - 2
        while target in occupied or target == u:
            target -= 2
        if target < 1:
            print(f"{dc}: OOBM1 — no free U below the stack (floor U{floor_u}) — skipped")
            continue

        d["rack_unit"] = target
        fixed += 1
        print(f"{dc}: OOBM1 {d['name']} U{u} -> U{target} "
              f"(cleared collision at U{u} in R{row}-{num:02d})")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nDone: de-collided {fixed} OOBM switch(es). Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
