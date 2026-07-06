#!/usr/bin/env python3
"""Re-lay compute-annex halls (e.g. Server Hall B) so their racks fill from the
FRONT row (row 1), not offset into the room.

A compute annex has no local spine/OOB — it shares the DC's fabric in the primary
hall — so it has NO network row to reserve. But the curated topology carved these
halls on a continuous grid with the primary hall, labeling their rows 3-5 and
placing the single compute row ~4 m into the room. That left the front (would-be
rows 1-2) as dead, uncooled floor: no rows, no aisles, and in 3D no perforated
cold-aisle vent tiles.

This moves every non-CRAH rack to consecutive rows starting at row 1 (front),
rebuilds the room extent (rows + hot/cold aisles from the front, via
core.hall_geometry) so cooling tiles cover the compute area, and leaves the
perimeter CRAHs at the back wall. Only floor placement + the extent change; power/
network edges are untouched. Idempotent (a hall already front-aligned is skipped).

Pairs with FleetLifecycleEngine._hall_grid, which now treats a hall with no local
spine as compute-from-row-1.

Usage:  python -m tools.fix_hall_annex_layout [path/to/topology.json]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core.hall_geometry as geo  # noqa: E402

DEFAULT = Path("topologies/dual_dc_enterprise.json")
CRAH = "crah"


def _has_local_spine(devs: list) -> bool:
    return any(d.get("device_type") == "switch" and "-SP" in (d.get("name") or "")
               for d in devs)


def _n_rows_for_depth(depth_m: float) -> int:
    """Compute rows that fit before the back-wall CRAH margin."""
    n = 1
    while geo.row_y(n + 1) <= float(depth_m) - geo.ROW_PITCH * 0.5:
        n += 1
    return max(2, n)


def build(data: dict) -> list[str]:
    fp = data.get("floorplan", {})
    halls: dict = defaultdict(list)
    for n in data.get("nodes", []):
        d = n["device"]
        halls[(d.get("datacenter"), d.get("room"))].append(d)

    log: list[str] = []
    for (dc, room), devs in halls.items():
        if not (room or "").startswith("Server Hall"):
            continue
        if _has_local_spine(devs):
            continue                              # network hall — front row is its MDA
        non_crah = [d for d in devs if d.get("device_type") != CRAH
                    and d.get("floor_y") is not None]
        if not non_crah:
            continue
        rows_fy = sorted({round(d.get("floor_y"), 2) for d in non_crah})
        if abs(rows_fy[0] - geo.row_y(1)) < 0.01:
            continue                              # already front-aligned — idempotent

        # Remap physical rows front-to-back onto rows 1,2,3…
        new_row = {fy: i + 1 for i, fy in enumerate(rows_fy)}
        for d in non_crah:
            nr = new_row[round(d.get("floor_y"), 2)]
            hot, cold, facing = geo.row_aisles(nr)
            d["rack_row"] = nr
            d["floor_y"] = geo.row_y(nr)
            d["hot_aisle"] = hot
            d["cold_aisle"] = cold
            d["rack_facing"] = facing
            log.append(f"  {dc}/{room}: {d.get('name'):16} -> row{nr} (fy={geo.row_y(nr)})")

        # Rebuild the extent: rows + front-covering aisles for the room's real depth,
        # keeping the hall's actual width/depth/class. Cold aisles now start at the
        # front, so the 3D draws vent tiles over the compute area.
        ext = ((fp.get("rooms") or {}).get(f"{dc}/{room}")) or {}
        rpr = int(ext.get("racks_per_row") or 8)
        depth = float(ext.get("depth_m") or 12.6)
        n_rows = _n_rows_for_depth(depth)
        base = geo.hall_extent(n_rows, rpr)
        base.update({
            "width_m": ext.get("width_m", base.get("width_m")),
            "depth_m": ext.get("depth_m", base.get("depth_m")),
            "racks_per_row": rpr,
            "datacenter": dc, "room": room,
            "class": ext.get("class", "white_space"),
            "containment": ext.get("containment", "cold_aisle"),
        })
        fp.setdefault("rooms", {})[f"{dc}/{room}"] = base
        log.append(f"  {dc}/{room}: extent rebuilt — rows {base['rows']} (annex, compute from row 1)")
    return log


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    if not path.exists():
        print(f"topology not found: {path}", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    log = build(data)
    print("\n".join(log) if log else "(no changes — annex halls already front-aligned)")
    if log:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"\nre-laid {sum(1 for l in log if '-> row' in l)} rack(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
