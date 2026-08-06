#!/usr/bin/env python3
"""Re-seat servers bottom-up so a rack's free U is CONTIGUOUS, not scattered singles.

THE DEFECT

The topology was seeded on a flat 2U cadence — servers at U1, U3, U5… — and later
re-SKU'd to their real heights. A 1U box on a 2U pitch leaves a 1U hole above it, so
racks ended up looking like this:

    U08  .            <- three free U in this rack …
    U07  SRV04
    U06  .
    U05  SRV03
    U04  .
    U03  SRV02
    U02  .
    U01  SRV01

Seven free U in R2-01, and nowhere to put a 2U server: every gap is an isolated
single. The rack picker reported the truth ("0 free slots" for a 2U SKU) and it read
like a bug, which is how this was found.

Real installs do not look like that. Kit is racked contiguously — bottom-up, or to a
planned elevation — so free U accumulates as one block, normally at the top. Gaps do
appear over years of churn, but a DCIM tool flags them and the operator consolidates,
precisely because scattered singles strand capacity.

WHAT THIS DOES

Per rack, servers are re-seated bottom-up in their existing order, packed against
each other, skipping any U that a NON-server occupant holds. Free U is left as one
run at the top of the server area.

  * order is preserved, so SRV01 stays below SRV02 and the elevation reads the same
  * non-servers (the 4U CDU at U37-40, and anything else racked) never move
  * U41/U42 are outside the server area (the ToR pair) and are not touched
  * nothing leaves the rack; this only closes gaps

WHAT THIS DOES NOT TOUCH

  everything except rack_unit. No SKU, no power, no link, no cooling loop — the U a
  device sits at is not referenced by any edge, so re-seating cannot invalidate one.

Default scope is racks that contain a CDU: those are the ones where the fragmentation
actually blocks a build (a 2U DLC server can only go in a CDU rack). --all-racks
widens it to every rack in the topology.

Idempotent: a rack already packed is left byte-identical.

Usage:
    python tools/defrag_rack_units.py topologies/dual_dc_enterprise.json --dry-run
    python tools/defrag_rack_units.py topologies/dual_dc_enterprise.json
    python tools/defrag_rack_units.py topologies/dual_dc_enterprise.json --all-racks
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.rack_capacity import (FIRST_SERVER_UNIT, LAST_SERVER_UNIT,          # noqa: E402
                                device_u_height)


def _rack_of(dev: dict) -> tuple:
    return (dev.get("datacenter") or "", dev.get("room") or "",
            str(dev.get("floor") or ""), dev.get("rack_row") or 0,
            dev.get("rack_num") or 0)


def main(path: str, dry_run: bool = False, all_racks: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    devs = [n["device"] for n in topo["nodes"]]

    racks: dict = defaultdict(list)
    for d in devs:
        rk = _rack_of(d)
        if rk[3] <= 0 or rk[4] <= 0:
            continue
        if (d.get("rack_unit") or 0) <= 0:
            continue                          # 0U side-rail gear occupies no slot
        racks[rk].append(d)

    targets = sorted(racks) if all_racks else sorted(
        rk for rk, occ in racks.items()
        if any(o.get("device_type") == "cdu" for o in occ))

    moved_total, racks_changed = 0, 0
    report = []
    for rk in targets:
        occ = racks[rk]
        servers, fixed = [], []
        for d in occ:
            (servers if d.get("device_type") == "server" else fixed).append(d)
        if not servers:
            continue

        # U's held by anything that is not a server — packed around, never over.
        blocked = set()
        for d in fixed:
            u = d["rack_unit"]
            for cu in range(u, u + device_u_height(d.get("device_type"),
                                                   d.get("model_name") or "")):
                blocked.add(cu)

        servers.sort(key=lambda d: d["rack_unit"])
        cursor = FIRST_SERVER_UNIT
        plan = []
        for d in servers:
            h = device_u_height("server", d.get("model_name") or "")
            # Slide up until the whole body clears blocked U's and the rack top.
            while cursor + h - 1 <= LAST_SERVER_UNIT and any(
                    u in blocked for u in range(cursor, cursor + h)):
                cursor += 1
            if cursor + h - 1 > LAST_SERVER_UNIT:
                print(f"  !! {d['name']}: no room left in "
                      f"R{rk[3]}-{rk[4]:02d} — rack left untouched")
                plan = None
                break
            plan.append((d, cursor))
            cursor += h
        if plan is None:
            continue

        changed = [(d, u) for d, u in plan if d["rack_unit"] != u]
        if not changed:
            continue
        racks_changed += 1
        moved_total += len(changed)
        before = sorted({u for d in servers
                         for u in range(d["rack_unit"],
                                        d["rack_unit"] + device_u_height(
                                            "server", d.get("model_name") or ""))})
        for d, u in plan:
            d["rack_unit"] = u
        # The usable run is from the cursor up to the first BLOCKED U — not to the
        # top of the rack. A CDU sitting at U37-40 is not free space, and counting it
        # would overstate what the rack can still take.
        free_run = 0
        u = cursor
        while u <= LAST_SERVER_UNIT and u not in blocked:
            free_run += 1
            u += 1
        report.append((rk, len(changed), len(servers), free_run, before))

    if not dry_run and moved_total:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")

    verb = "Would re-seat" if dry_run else "Re-seated"
    print(f"\n{verb} {moved_total} server(s) across {racks_changed} rack(s)"
          f"{' (all racks)' if all_racks else ' (CDU racks)'}.\n")
    for rk, n, total, free_run, _b in report:
        print(f"   {rk[0]:4} {rk[1]:14} R{rk[3]}-{rk[4]:02d}  moved {n:2d}/{total:2d}"
              f" servers  -> contiguous free run now {free_run}U")
    if moved_total:
        print(f"\n   Free U is now one block per rack, so a multi-U server fits where "
              f"the scattered singles previously stranded the space.")
    print(f"\n{'(dry run)' if dry_run else (f'Wrote {p}' if moved_total else 'No change')}\n")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv,
                  all_racks="--all-racks" in sys.argv))
