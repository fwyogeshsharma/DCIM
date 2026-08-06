#!/usr/bin/env python3
"""Drop the in-rack CDUs one U so their real 4U body clears the U41 ToR-B reserve.

A CoolIT CHx80 is a 4U coolant distribution unit — the 3D floor-plan viewer has drawn it
that height since before the backend modelled height at all, and core.rack_capacity now
agrees (_TYPE_U_HEIGHT). But the topology placed every CDU at **U38**, which was only
ever valid under the flat "everything non-server is 1U" assumption:

    CDU at U38, 4U  ->  occupies U38, U39, U40, U41
                                             ^^^ TOR_B_UNIT

U41 is RESERVED and must stay empty — it is where the second leaf lands when the racks
are dual-homed for MLAG/vPC, and the whole point of reserving it is that adopting
dual-homing must not move a single installed device (see core/rack_capacity.py). A 4U
CDU at U38 quietly consumes it, so the reserve would only be discovered gone on the day
someone tried to use it.

U37 is the correct seat: 4U at U37 spans U37..U40, filling the server face exactly to
LAST_SERVER_UNIT and stopping one U below the reserve. U37..U40 is free in all 12 CDU
racks (verified before writing this).

CONSEQUENCE, deliberately accepted: those racks lose their last free 2U server slot
(U39-U40 was it). That capacity was never real — the CDU's body was always there; the
model just could not see it. server_capacity in the floor-plan export drops accordingly,
which is the honest number.

Only rack_unit changes. The CDU keeps its cords, its cooling loop to the servers, its
position on the floor, and its rack.

Idempotent: a topology whose CDUs already clear the reserve reports 0 and writes nothing.

Usage:
    python tools/reseat_cdu_below_tor_reserve.py topologies/dual_dc_enterprise.json
    python tools/reseat_cdu_below_tor_reserve.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.rack_capacity import (          # noqa: E402
    device_u_height, FIRST_SERVER_UNIT, LAST_SERVER_UNIT, TOR_B_UNIT,
)


def rack_key(d: dict) -> tuple:
    return (d.get("datacenter") or "", str(d.get("floor") or ""), d.get("room") or "",
            d.get("rack_row") or 0, d.get("rack_num") or 0)


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    racks: dict = {}
    for n in nodes:
        d = n["device"]
        if (d.get("rack_unit") or 0) > 0:
            racks.setdefault(rack_key(d), []).append(d)

    moved, blocked = [], []
    for key, devs in sorted(racks.items()):
        for cdu in [d for d in devs if d.get("device_type") == "cdu"]:
            h = device_u_height("cdu", cdu.get("model_name") or "")
            u = cdu["rack_unit"]
            top = u + h - 1
            if top <= LAST_SERVER_UNIT:
                continue                       # already clears the reserve
            # Highest seat whose whole body still lands at or below LAST_SERVER_UNIT.
            want = LAST_SERVER_UNIT - h + 1
            occ = {}
            for d in devs:
                if d is cdu:
                    continue
                for cu in range(d["rack_unit"],
                                d["rack_unit"] + device_u_height(d.get("device_type"),
                                                                 d.get("model_name") or "")):
                    occ[cu] = d["name"]
            clash = [(cu, occ[cu]) for cu in range(want, want + h) if cu in occ]
            if want < FIRST_SERVER_UNIT or clash:
                blocked.append((key, cdu["name"], u, want, clash))
                continue
            moved.append((key, cdu["name"], u, want, h, top))
            cdu["rack_unit"] = want

    if not dry_run and moved:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would re-seat" if dry_run else "Re-seated"
    print(f"\n{verb} {len(moved)} CDU(s) so their body clears the U{TOR_B_UNIT} ToR-B "
          f"reserve. {'(dry run)' if dry_run else (f'Wrote {p}' if moved else 'No change')}\n")
    for key, nm, old, new, h, top in moved:
        print(f"  {key[0]}/{key[2]} R{key[3]}-{key[4]}: {nm}  U{old} -> U{new}"
              f"   ({h}U body was U{old}-U{top}, now U{new}-U{new+h-1})")
    for key, nm, u, want, clash in blocked:
        print(f"  !! {key[0]}/{key[2]} R{key[3]}-{key[4]}: {nm} @U{u} cannot drop to "
              f"U{want} — {clash or 'below the first server U'}. Left in place.")
    print()
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
