#!/usr/bin/env python3
"""Stand each server hall's CRAHs at the aisle ends (S2 decision D-1, option A).

The curated halls lined seven 1.75 m Liebert PCW units on 1.0 m centres along one
8.4 m back wall - units that physically overlap - and the S2 spatial thermal model
(docs/S2_SPATIAL_THERMAL_MODEL.md) needs to know where each unit really stands.
Option A puts them on the two walls the rows point at, lined up with the aisles,
which is the common layout. Each of those walls gives up an end zone for the
unit's depth plus service clearance (core/hall_geometry.CRAH_END_ZONE_M), so:

  * the room is widened by two end zones (8.4 m -> 12.2 m);
  * every rack and everything in it moves in by one end zone (+1.9 m in x),
    and the room records the new grid origin as ``grid_x0_m`` so the fleet
    engine lays new racks on the same grid;
  * the CRAHs take hall_geometry.crah_positions (4 on the x = 0 wall facing E,
    3 on the far wall facing W), in name order;
  * the two mechanical power panels stay in the back wall's end bays.

Asset layer only: no names, addresses, rack_row/rack_num or telemetry change.
The SNMP dataset fingerprint covers the topology, so after this:
Stop -> Regenerate Datasets -> Start, and re-import the platform with
``--geometry-only``.

Idempotent: a hall that already carries ``grid_x0_m`` is skipped.

    python tools/regrid_halls_aisle_end_crahs.py topologies/dual_dc_enterprise.json
    python tools/layout_buildings.py topologies/dual_dc_enterprise.json --check
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import hall_geometry as geo  # noqa: E402

PANEL_INSET = 0.3   # an MPP's centre off the side wall, in the back wall's end bay


def is_server_hall(room: dict, devs: list[dict]) -> bool:
    return room.get("class") == "white_space" and any(d.get("device_type") == "crah" for d in devs)


def apply(topo: dict) -> list[str]:
    log: list[str] = []
    rooms = topo.get("floorplan", {}).get("rooms", {})
    by_room: dict[str, list[dict]] = {}
    for n in topo["nodes"]:
        d = n.get("device") or {}
        by_room.setdefault(f"{d.get('datacenter')}/{d.get('room')}", []).append(d)
    for key, room in sorted(rooms.items()):
        devs = by_room.get(key, [])
        if not is_server_hall(room, devs):
            continue
        if room.get("grid_x0_m") is not None:
            log.append(f"{key}: already at the aisle-end layout (grid_x0_m={room['grid_x0_m']})")
            continue
        old_x0 = geo.room_x0(room)
        shift = round(geo.HALL_X0 - old_x0, 4)
        old_w = float(room.get("width_m") or 0)
        width = round(old_w + 2 * shift, 4)
        depth = float(room.get("depth_m") or 0)
        room["width_m"], room["grid_x0_m"] = width, geo.HALL_X0
        log.append(f"{key}: width {old_w} -> {width} m, racks +{shift} m in x")

        crahs = sorted((d for d in devs if d.get("device_type") == "crah"), key=lambda d: d.get("name", ""))
        for d, (x, y, rot) in zip(crahs, geo.crah_positions(width, depth, len(crahs)), strict=True):
            d["floor_x"], d["floor_y"], d["rotation_deg"] = x, y, rot
            log.append(f"  {d['name']}: ({x}, {y}) facing {int(rot)}")
        panels = sorted((d for d in devs if d.get("device_type") == "mpp"), key=lambda d: d.get("name", ""))
        for i, d in enumerate(panels):
            # A side in the left corner, B side in the right, as the fleet lays them.
            d["floor_x"] = PANEL_INSET if i % 2 == 0 else round(width - PANEL_INSET, 4)
        moved = 0
        for d in devs:
            if d.get("device_type") in ("crah", "mpp") or d.get("floor_x") is None:
                continue
            d["floor_x"] = round(float(d["floor_x"]) + shift, 4)
            moved += 1
        log.append(f"  moved {moved} devices in racks, {len(crahs)} CRAHs, {len(panels)} panels")
    return log


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    topo = json.loads(path.read_text(encoding="utf-8"))
    log = apply(topo)
    print("\n".join(log) or "no server halls found")
    path.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
