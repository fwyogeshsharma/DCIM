#!/usr/bin/env python3
"""Resize each DC's Server Hall B to match that DC's Server Hall A footprint.

Hall B was a narrow compute annex (4.8 m wide) — too short a wall to fit a
realistic CRAH complement (6 units cramped at 0.8 m pitch). This copies Hall A's
floor extent (width / depth / rows / aisles / racks_per_row) onto Hall B in the
SAME datacenter, so the two halls are the same physical size. Hall B stays a
compute ANNEX (no local spine) — only its floor extent changes.

Existing Hall B gear all sits in the front compute row (row 1) within the old
4.8 m width, so widening needs no device reflow. CRAHs are re-placed afterwards
by tools/seed_hall_crahs.py against the new extent.

Idempotent. Run seed_hall_crahs.py + export_dcim_floorplan.py afterwards.

Usage:
    python tools/match_hall_b_to_a.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

# Extent fields that define a hall's physical size/grid (copied A -> B).
SIZE_FIELDS = ("width_m", "depth_m", "rows", "aisles", "racks_per_row")


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    rooms = (topo.get("floorplan") or {}).get("rooms") or {}

    changed = 0
    for dc in sorted({k.split("/")[0] for k in rooms}):
        a = rooms.get(f"{dc}/Server Hall A")
        b = rooms.get(f"{dc}/Server Hall B")
        if not a or not b:
            continue
        before = {f: b.get(f) for f in SIZE_FIELDS}
        for f in SIZE_FIELDS:
            b[f] = copy.deepcopy(a[f])
        after = {f: b.get(f) for f in SIZE_FIELDS}
        print(f"{dc}/Server Hall B: "
              f"width {before['width_m']}->{after['width_m']}m  "
              f"depth {before['depth_m']}->{after['depth_m']}m  "
              f"aisles {len(before['aisles'] or [])}->{len(after['aisles'] or [])}  "
              f"rpr {before['racks_per_row']}->{after['racks_per_row']}")
        changed += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nResized {changed} hall(s). Wrote {p}")
    print("Next: re-seed CRAHs, then refresh the DCIM export:")
    print(f"  python tools/seed_hall_crahs.py {path}")
    print(f"  python tools/export_dcim_floorplan.py {path} "
          f"{path.replace('.json', '_floorplan.json')}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
