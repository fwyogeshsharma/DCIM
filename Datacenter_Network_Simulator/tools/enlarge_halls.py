#!/usr/bin/env python3
"""Enlarge server halls so more compute rows fit — in place, curated racks kept.

Minimal/non-destructive: the existing network/compute/CDU/RPP racks are NOT
moved (their hand-tuned positions are preserved). Each hall just gets:
  * a bigger floor extent (depth grown for `target_compute_rows`, width kept),
  * its CRAH perimeter row relocated to the NEW back wall,
  * extra hot/cold aisles appended behind the existing ones.

The added rows are left EMPTY — headroom the fleet lifecycle engine fills over
sim-days (placing racks on the row grid behind the existing compute), then opens
a brand-new hall once the enlarged hall is full.

Run export_dcim_floorplan.py afterwards to refresh the asset/floor-plan file.

Usage:
    python tools/enlarge_halls.py topologies/dual_dc_enterprise.json [--dry-run]
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.hall_geometry import ROW_PITCH, AISLE_WIDTH  # noqa: E402

# target compute rows per hall (currently every hall has 1)
TARGETS = [
    dict(dc="DC1", floor="1", room="Server Hall A", target_compute_rows=3),
    dict(dc="DC1", floor="2", room="Server Hall B", target_compute_rows=3),
    dict(dc="DC2", floor="1", room="Server Hall A", target_compute_rows=3),
    dict(dc="DC2", floor="2", room="Server Hall B", target_compute_rows=3),
]


def enlarge(topo: dict, t: dict) -> dict:
    devs = [n["device"] for n in topo["nodes"]]
    dc, floor, room = t["dc"], t["floor"], t["room"]

    def in_hall(d):
        return (d.get("datacenter") == dc and str(d.get("floor")) == floor
                and d.get("room") == room)
    hall = [d for d in devs if in_hall(d)]
    if not hall:
        raise RuntimeError(f"hall not found: {t}")

    # Compute rows = floor_y bands that actually hold servers (ignore the 1-2
    # stray mislocated devices by requiring a real cluster).
    yc = Counter(round(d.get("floor_y"), 4) for d in hall
                 if d.get("device_type") == "server" and d.get("floor_y") is not None)
    compute_ys = sorted(y for y, c in yc.items() if c >= 3)
    if not compute_ys:
        raise RuntimeError(f"no compute row found in {t}")
    n_cur = len(compute_ys)
    back_y = max(compute_ys)

    add = max(0, t["target_compute_rows"] - n_cur)
    last_compute_y = back_y + ROW_PITCH * add        # back-most reserved IT row
    crah_y = round(last_compute_y + ROW_PITCH, 4)     # CRAH goes one row behind

    # Relocate CRAH perimeter to the new back wall (keep each unit's x).
    crah = [d for d in hall if d.get("device_type") == "crah"]
    for c in crah:
        c["floor_y"] = crah_y

    # Grow the room extent: deeper floor + appended aisles; width unchanged
    # (curated halls already declare ample racks_per_row width).
    ext = topo["floorplan"]["rooms"][f"{dc}/{room}"]
    ext["depth_m"] = round(crah_y + 0.9, 4)
    # Append one aisle per added row + one before CRAH, continuing the existing
    # hot/cold alternation past the current back-most aisle.
    aisles = ext.setdefault("aisles", [])
    if aisles:
        max_ay = max(a["y"] for a in aisles)
        last = max(aisles, key=lambda a: a["y"])
        next_hot = last["type"] != "hot"     # alternate from the last aisle
        hot_n = max((int(a["id"][2:]) for a in aisles if a["id"].startswith("HA")), default=-1)
        cold_n = max((int(a["id"][2:]) for a in aisles if a["id"].startswith("CA")), default=0)
        base_row = max(ext.get("rows", [1])) if ext.get("rows") else 1
        new_rows = list(ext.get("rows", []))
        # Append aisles behind the existing back-most one, continuing the
        # hot/cold alternation, but never past the (new) back wall.
        k = 0
        while True:
            ay = round(max_ay + ROW_PITCH * (k + 1), 4)
            if ay >= ext["depth_m"]:
                break
            if next_hot:
                hot_n += 1; aid, typ = f"HA{hot_n}", "hot"
            else:
                cold_n += 1; aid, typ = f"CA{cold_n}", "cold"
            next_hot = not next_hot
            r = base_row + k + 1
            aisles.append({"id": aid, "type": typ, "between_rows": [r - 1, r],
                           "y": ay, "width": AISLE_WIDTH})
            new_rows.append(r)
            k += 1
        ext["rows"] = new_rows

    return {"hall": f"{dc}/{room}", "compute_rows": f"{n_cur}->{t['target_compute_rows']}",
            "rows_added": add, "crah_moved_to_y": crah_y, "depth_m": ext["depth_m"]}


def main(argv) -> int:
    if not (2 <= len(argv) <= 3):
        print(__doc__); return 2
    path = argv[1]; dry = "--dry-run" in argv
    topo = json.loads(Path(path).read_text(encoding="utf-8"))
    for t in TARGETS:
        s = enlarge(topo, t)
        print(f"  {s['hall']}: compute rows {s['compute_rows']} (+{s['rows_added']} empty), "
              f"CRAH->y{s['crah_moved_to_y']}, depth={s['depth_m']}m")
    if dry:
        print("[dry-run] not written"); return 0
    Path(path).write_text(json.dumps(topo, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
