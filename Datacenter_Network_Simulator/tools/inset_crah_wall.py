#!/usr/bin/env python3
"""Inset each hall's CRAH lineup and seat the MPPs in the wall's end bays.

The CRAHs were spread across the FULL back wall (fx 0.3 .. width-0.3), leaving no
room at the ends for the two mechanical power panels (MPP). So the MPPs either
overlapped the end CRAHs or were parked away from the loads they feed.

FIX: inset the CRAH lineup by END_RESERVE (0.7 m) at each end — pulling the units
inward so ~0.7 m opens at each wall end — and stand the two MPPs in those end bays,
on the CRAH row (same floor_y), flanking the CRAHs. Matches the inset formula now
in tools/seed_hall_crahs.perimeter_positions and
core.fleet_lifecycle._crah_perimeter_positions.

Idempotent: a hall whose CRAHs already match the inset layout is left alone. Only
floor-plan coords change (floor_x for CRAHs, floor_x/floor_y for MPPs).

Usage:
    python tools/inset_crah_wall.py topologies/dual_dc_enterprise.json
    python tools/inset_crah_wall.py topologies/dual_dc_enterprise.json --check
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

END_RESERVE = 0.7   # metres per end for an MPP bay — matches CRAH_END_RESERVE


def _inset_fx(width: float, target: int) -> list:
    usable = max(1.0, width - 2 * END_RESERVE)
    return [round(END_RESERVE + usable * (j + 0.5) / target, 4) for j in range(target)]


def main(argv) -> int:
    path = argv[1]
    check_only = "--check" in argv
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]
    rooms = (topo.get("floorplan", {}) or {}).get("rooms", {}) or {}

    by_hall = collections.defaultdict(list)
    for n in nodes:
        d = n["device"]
        if (d.get("room") or "").startswith("Server Hall"):
            by_hall[(d.get("datacenter"), d.get("room"))].append(d)

    changed = 0
    for (dc, room), devs in sorted(by_hall.items(), key=lambda kv: str(kv[0])):
        crahs = [d for d in devs if d.get("device_type") == "crah"]
        mpps = [d for d in devs if d.get("device_type") == "mpp"]
        if not crahs:
            continue
        width = float((rooms.get(f"{dc}/{room}", {}) or {}).get("width_m") or 8.4)
        back_y = crahs[0].get("floor_y")
        target = len(crahs)
        want = _inset_fx(width, target)
        crahs_sorted = sorted(crahs, key=lambda d: (d.get("floor_x") or 0))
        # CRAHs: assign inset fx left-to-right (floor_y stays on the back wall).
        for d, fx in zip(crahs_sorted, want):
            if abs((d.get("floor_x") or -99) - fx) > 1e-3:
                d["floor_x"] = fx
                changed += 1
        # MPPs: end bays of the CRAH wall.
        for m in mpps:
            side = "B" if "MPPB" in (m.get("name") or "").upper() else "A"
            fx = 0.3 if side == "A" else round(width - 0.3, 3)
            if (abs((m.get("floor_x") or -99) - fx) > 1e-3
                    or abs((m.get("floor_y") or -99) - (back_y or 0)) > 1e-3):
                m["floor_x"], m["floor_y"] = fx, back_y
                changed += 1
        if changed:
            print(f"  {dc}/{room}: CRAHs inset to [{want[0]}..{want[-1]}], "
                  f"MPPs to wall ends (fx 0.3 / {round(width-0.3,3)}, fy {back_y})")

    if not changed:
        print("Already inset. Nothing to do.")
        return 0
    print(f"\nAdjusted {changed} device position(s).")
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
