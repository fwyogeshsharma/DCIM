#!/usr/bin/env python3
"""Relocate each server hall's IT RPP pair (+ its EV2 monitors) to the front
network row (row 1), matching how the fleet engine builds NEW halls (RPP + spines
+ OOB on the front row). Curated halls had the RPP pair at the end of the compute
row (row 2); this frees those compute slots and makes curated halls consistent
with fleet-built ones — a front "infrastructure/distribution row" (network +
power) feeding the compute rows via overhead busway.

Only floor PLACEMENT changes (rack_row / rack_num / floor_x / floor_y / aisle);
the logical power edges (UPS→RPP→PDU) and every other field are untouched. The
EV2 energy monitor clamps its RPP's output breakers, so it moves with its RPP
(kept in the same rack cell). A-side and B-side RPPs are placed at OPPOSITE ends
of row 1 (independent-feed separation — a single incident can't take both).

Idempotent: an RPP already in row 1 is skipped.

Usage:  python -m tools.move_rpp_to_network_row [path/to/topology.json]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core.hall_geometry as geo  # noqa: E402

DEFAULT = Path("topologies/dual_dc_enterprise.json")
NETWORK_ROW = 1


def _hall_extent(fp: dict, dc: str, room: str) -> dict:
    return ((fp or {}).get("rooms") or {}).get(f"{dc}/{room}") or {}


def build(data: dict) -> list[str]:
    fp = data.get("floorplan", {})
    # group devices by hall
    halls: dict = defaultdict(list)
    for n in data.get("nodes", []):
        d = n["device"]
        halls[(d.get("datacenter"), d.get("room"))].append(d)

    log: list[str] = []
    for (dc, room), devs in halls.items():
        rpps = [d for d in devs if d.get("device_type") == "rpp"
                and (d.get("name") or "").startswith("RPP-IT")
                and d.get("rack_row") != NETWORK_ROW]
        if not rpps:
            continue
        row1 = [d for d in devs if d.get("rack_row") == NETWORK_ROW]
        if not row1:
            # A compute annex (e.g. Server Hall B) with no local spine/network
            # row — it shares the DC's spines in the primary hall. No network row
            # to move the RPP into, so its RPP stays end-of-row with its compute.
            log.append(f"  {dc}/{room}: no network row (compute annex) — RPP left in place")
            continue
        ref = row1[0]
        fy1 = ref.get("floor_y")
        facing = ref.get("rack_facing")
        cold = ref.get("cold_aisle")
        hot = ref.get("hot_aisle")
        ext = _hall_extent(fp, dc, room)
        rpr = int(ext.get("racks_per_row") or 9)
        used = {d.get("rack_num") for d in row1}
        free = [n for n in range(1, rpr + 1) if n not in used]
        if len(free) < 2:
            log.append(f"  {dc}/{room}: <2 free row-1 slots — skipped")
            continue

        # EV2 monitors indexed by their current rack cell, so each moves with the
        # RPP it clamps onto (same cell). Captured before any mutation.
        ev2_by_cell = defaultdict(list)
        for d in devs:
            if d.get("device_type") == "energy_monitor":
                ev2_by_cell[(d.get("rack_row"), d.get("rack_num"))].append(d)

        a_rpps = [r for r in rpps if "-A" in (r.get("name") or "")]
        b_rpps = [r for r in rpps if "-B" in (r.get("name") or "")]
        left = list(free)                          # A side fills from the near wall
        right = list(reversed(free))               # B side fills from the far wall
        taken: set = set()

        def place(rpp, target):
            cell = (rpp.get("rack_row"), rpp.get("rack_num"))
            movers = [rpp] + ev2_by_cell.get(cell, [])
            fx = geo.rack_x(target)
            for m in movers:
                m["rack_row"] = NETWORK_ROW
                m["rack_num"] = target
                m["floor_x"] = fx
                m["floor_y"] = fy1
                m["rack_facing"] = facing
                m["cold_aisle"] = cold
                m["hot_aisle"] = hot
                log.append(f"  {dc}/{room}: {m.get('name'):16} -> row1 rack{target} (fx={fx})")

        li = ri = 0
        for r in a_rpps:
            while left[li] in taken:
                li += 1
            t = left[li]; taken.add(t); li += 1
            place(r, t)
        for r in b_rpps:
            while right[ri] in taken:
                ri += 1
            t = right[ri]; taken.add(t); ri += 1
            place(r, t)
    return log


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    if not path.exists():
        print(f"topology not found: {path}", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    log = build(data)
    print("\n".join(log) if log else "(no changes — RPP/EV2 already in the network row)")
    if log:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"\nmoved {sum(1 for l in log if '-> row1' in l)} device(s) in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
