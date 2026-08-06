#!/usr/bin/env python3
"""Re-seat servers whose 2U body overlaps another device in the same rack.

A rack server is SERVER_U_HEIGHT (2U): one at U37 occupies U37 AND U38. The placer
that built this topology compared rack_unit as a POINT, so it never saw the 1U in-rack
CDU sitting at U38 and put a server straight through it. Three racks ended up with a
server and a CDU claiming the same U — a rack elevation that cannot be built:

    DC1/Server Hall A R2-2   SRV18 @U37  ->  CDU1 @U38
    DC1/Server Hall B R2-1   SRV18 @U37  ->  CDU1 @U38
    DC1/Server Hall B R2-3   SRV18 @U37  ->  CDU1 @U38

The runtime now measures occupancy as a SPAN on both paths that place a server
(FleetLifecycleEngine._next_free_unit and the Add-Device rack picker, which share
core.rack_capacity.device_u_height), so no NEW overlap can be created. This is the
one-shot that cleans up the ones already in the file.

WHICH END MOVES, and why it is the server:

The CDU sits at U38 in ALL 12 racks that have one — a uniform, deliberate placement.
Only 3 racks have a server at U37, and in every one of them the server broke the 2U
cadence to get there: the rack runs SRV01..SRV17 on odd units U1..U33 and then SRV18
jumps to U37, leaving U35 free. So the server is the anomaly, not the CDU. Moving it
to the cadence slot it should have taken (U35) both clears the CDU and restores the
run; moving the CDU instead would make it inconsistent with the other 9 racks and
leave the cadence broken anyway.

Only rack_unit changes. sys_location is derived from it at runtime
(Device.sys_location_override is empty on these), and floor_x/floor_y are the RACK's
coordinates — identical to the neighbouring server — so nothing moves on the floor
plan and no layout_canvas / floorplan re-export is needed.

Idempotent: a topology with no overlapping spans is left untouched and reports 0.

Usage:
    python tools/fix_server_u_overlap.py topologies/dual_dc_enterprise.json
    python tools/fix_server_u_overlap.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.rack_capacity import (            # noqa: E402  (after sys.path fix)
    FIRST_SERVER_UNIT, LAST_SERVER_UNIT, device_u_height,
)


def rack_key(d: dict) -> tuple:
    """Physically-unique rack id — mirrors FleetLifecycleEngine._rack_key. Floor and
    room are part of it: Hall A R2-01 and Hall B R2-01 are different racks."""
    return (d.get("datacenter") or "", str(d.get("floor") or ""), d.get("room") or "",
            d.get("rack_row") or 0, d.get("rack_num") or 0)


def spans(d: dict) -> range:
    u = d.get("rack_unit") or 0
    return range(u, u + device_u_height(d.get("device_type"), d.get("model_name") or ""))


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
        # Who holds each U. A rack this loop has already fixed re-reads clean.
        def occupancy(exclude=None):
            occ: dict = {}
            for d in devs:
                if d is exclude:
                    continue
                for cu in spans(d):
                    occ.setdefault(cu, []).append(d)
            return occ

        occ = occupancy()
        clashing = [d for d in devs
                    if d.get("device_type") == "server"
                    and any(len(occ.get(cu, [])) > 1 for cu in spans(d))]
        for srv in clashing:
            others = occupancy(exclude=srv)
            # Lowest gap this box's own body fits in — first-fit by 1U, since heights
            # are per-SKU and no single stride suits every server.
            h = device_u_height(srv.get("device_type"), srv.get("model_name") or "")
            slot = next((u for u in range(FIRST_SERVER_UNIT, LAST_SERVER_UNIT - h + 2)
                         if all(cu not in others for cu in range(u, u + h))), None)
            if slot is None:
                blocked.append((key, srv["name"], srv["rack_unit"], h))
                continue
            peers = sorted({o["name"] for cu in spans(srv) for o in occ.get(cu, [])
                            if o is not srv})
            moved.append((key, srv["name"], srv["rack_unit"], slot, peers))
            srv["rack_unit"] = slot

    if not dry_run and moved:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would re-seat" if dry_run else "Re-seated"
    print(f"\n{verb} {len(moved)} server(s) whose 2U body overlapped another device."
          f" {'(dry run)' if dry_run else (f'Wrote {p}' if moved else 'No change')}\n")
    for key, name, old, new, peers in moved:
        print(f"  {key[0]}/{key[2]} R{key[3]}-{key[4]}: {name}  U{old} -> U{new}"
              f"   (was overlapping {', '.join(peers)})")
    for key, name, u, h in blocked:
        print(f"  !! {key[0]}/{key[2]} R{key[3]}-{key[4]}: {name} @U{u} overlaps but the "
              f"rack has no free {h}U slot — left in place, needs a human")
    print()
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
