#!/usr/bin/env python3
"""Lay out the electrical upstream (utility feed, switchgear, ATS, UPS, MCC) both
physically and on the topology canvas, and grow the facility rooms to hold it.

add_electrical_upstream.py wires the one-line correctly but drops the new gear at
coordinates borrowed from whatever device it anchored on. That leaves three
problems this fixes:

  1. On the CANVAS the tiers collide — SWGR1 landed on the same row as the two
     ATS it feeds, and MCC2 sat on top of its own EV2 meter. Power flows downward
     on this canvas (larger y is further from the source), so each electrical rank
     gets its own row: sources, buses, transfer switches, then UPS/MCC.

  2. On the FLOOR every new device inherited rack_row=1, rack_num=1, which means
     export_dcim_floorplan.py collapses them all into one rack id. Real gear stands
     in a lineup, one section per unit. Each device gets its own rack_num across
     the room, ordered the way the one-line reads:

         electrical room:   UTIL1  SWGR1  ATS1  UPSA  ATS2  UPSB
         generator room:    GEN1   GEN2   SWGR2
         mechanical room:   MCC1   MCC2

  3. The rooms themselves are still sized for the old device count (the electrical
     room was 1.2 m wide, enough for two UPS), so the new lineups would hang out
     past the wall. width_m and racks_per_row are recomputed from what each room
     now holds.

floor_x/floor_y are derived from (rack_num, rack_row) with the floorplan's own
rack_pitch/row_pitch, matching how every other facility device in the file is
placed. Idempotent: every coordinate is assigned absolutely, never nudged.

Run AFTER add_electrical_upstream.py, then regenerate the asset DB:
    python tools/fix_electrical_positions.py topologies/dual_dc_enterprise.json
    python tools/export_dcim_floorplan.py topologies/dual_dc_enterprise.json \
        topologies/dual_dc_enterprise_floorplan.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

# Canvas rows, one per electrical rank. Power flows downward (+y).
SRC_Y  = 2480   # utility service entrance, gensets
BUS_Y  = 2610   # utility main board, generator paralleling board
ATS_Y  = 2740   # transfer switches
DIST_Y = 2870   # UPS (critical) and MCC (mechanical) — both hang off an ATS
METER_Y = 2990  # EV2 meters clamped onto the gear above them

# Canvas x offsets from each DC's facility band origin.
CANVAS_DX = {
    "UTIL1": 0,    "SWGR1": 0,     "EV2-UR": 0,
    "ATS1": 200,   "UPSA": 200,
    "ATS2": 460,   "UPSB": 460,
    "GEN1": 700,   "GEN2": 820,   "SWGR2": 760,   "EV2-GR": 760,
    "MCC1": 1000,  "MCC2": 1120,  "EV2-MR": 1000, "EV2-MR2": 1120,
}

# Physical lineup: room -> ordered device keys, one rack section each.
LINEUP = {
    "UPS Room":        ["UTIL1", "SWGR1", "ATS1", "UPSA", "ATS2", "UPSB"],
    "Generator Room":  ["GEN1", "GEN2", "SWGR2"],
    "Mechanical Room": ["MCC1", "MCC2"],
}
# Meters stand in the row behind the gear they clamp (row 2), as they already did.
# Order here fixes their rack_num, so keep each meter aligned with its panel.
METER_ROW = {
    "UPS Room":        ["EV2-UR"],            # clamps SWGR1 (facility main, utility)
    "Generator Room":  ["EV2-GR"],            # clamps SWGR2 (facility main, generator)
    "Mechanical Room": ["EV2-MR", "EV2-MR2"], # clamp MCC1 / MCC2
}


def key_for(name: str) -> str | None:
    """Map a device name like 'ATS1-DC1-UR' or 'EV22-DC1-MR' onto its layout key."""
    head = name.split("-", 1)[0]
    if head in ("UTIL1", "SWGR1", "SWGR2", "ATS1", "ATS2", "MCC1", "MCC2",
                "GEN1", "GEN2", "UPSA", "UPSB"):
        return head
    # Meters are keyed by room, and by index within the room where a room holds
    # more than one (the two MCC meters).
    if head.startswith("EV2"):
        if name.endswith("-UR"):
            return "EV2-UR"
        if name.endswith("-GR"):
            return "EV2-GR"
        if name.endswith("-MR"):
            return "EV2-MR2" if head == "EV22" else "EV2-MR"
    return None


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]
    fp = topo.setdefault("floorplan", {})
    rooms = fp.setdefault("rooms", {})
    pitch = float(fp.get("rack_pitch") or 0.6)
    row_pitch = float(fp.get("row_pitch") or 2.4)

    by_dc_room: dict = defaultdict(dict)
    for n in nodes:
        d = n["device"]
        k = key_for(d.get("name") or "")
        if k and d.get("room") in LINEUP:
            by_dc_room[(d["datacenter"], d["room"])][k] = n

    # Facility band origin per DC: the leftmost x any of this gear already holds.
    base_x: dict = {}
    for (dc, _room), found in by_dc_room.items():
        xs = [n["position"]["x"] for n in found.values()]
        base_x[dc] = min(base_x.get(dc, min(xs)), min(xs))

    moved = 0
    for (dc, room), found in sorted(by_dc_room.items()):
        order = [k for k in LINEUP[room] if k in found]
        meters = [k for k in METER_ROW.get(room, []) if k in found]

        for num, k in enumerate(order, start=1):
            d = found[k]["device"]
            d["rack_row"], d["rack_num"] = 1, num
            d["floor_x"] = round(pitch / 2 + (num - 1) * pitch, 3)
            d["floor_y"] = round(row_pitch / 4, 3)
            moved += 1

        for num, k in enumerate(meters, start=1):
            d = found[k]["device"]
            d["rack_row"], d["rack_num"] = 2, num
            d["floor_x"] = round(pitch / 2 + (num - 1) * pitch, 3)
            d["floor_y"] = round(row_pitch / 4 + row_pitch, 3)
            moved += 1

        # Canvas rows, one per electrical rank.
        y_of = {"UTIL1": SRC_Y, "GEN1": SRC_Y, "GEN2": SRC_Y,
                "SWGR1": BUS_Y, "SWGR2": BUS_Y,
                "ATS1": ATS_Y, "ATS2": ATS_Y,
                "UPSA": DIST_Y, "UPSB": DIST_Y, "MCC1": DIST_Y, "MCC2": DIST_Y,
                "EV2-UR": METER_Y, "EV2-GR": METER_Y,
                "EV2-MR": METER_Y, "EV2-MR2": METER_Y}
        for k, n in found.items():
            n["position"] = {"x": base_x[dc] + CANVAS_DX[k], "y": y_of[k]}

        # Grow the room to the lineup it now holds.
        rk = f"{dc}/{room}"
        r = rooms.setdefault(rk, {"datacenter": dc, "room": room,
                                  "class": "facility", "containment": "none",
                                  "aisles": []})
        n_rows = 2 if meters else 1
        r["racks_per_row"] = max(len(order), len(meters))
        r["width_m"] = round(r["racks_per_row"] * pitch, 3)
        r["depth_m"] = round(n_rows * row_pitch, 3)
        r["rows"] = list(range(1, n_rows + 1))
        print(f"{rk:26s} lineup={order} meters={meters} "
              f"-> {r['racks_per_row']} racks/row, {r['width_m']} x {r['depth_m']} m")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nPlaced {moved} devices. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
