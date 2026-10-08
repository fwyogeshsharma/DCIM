#!/usr/bin/env python3
"""Place every room in its building and lay the plant rooms out at real size.

Until now each DC was a set of rooms with no relation to one another (the floor was
only a label), and the plant rooms were sized from a 0.6 m rack grid: two 2 MW
gensets stood in a 1.8 m room, three cooling towers on a 0.6 x 2.4 m roof, and all
twenty Central Plant devices shared one coordinate. A digital twin built on that
would draw a building that cannot exist. This tool fixes the asset layer only - no
device identity, name, address, rack_row/rack_num or telemetry changes:

  1. BUILDING. Each room gets ``level`` (its devices' floor), ``origin`` (its corner
     in building coordinates on that level) and ``rotation_deg``; each DC gets
     ``floorplan.buildings[dc]``. Levels, elevations and the outline are DERIVED by
     core/equipment_geometry.complete_building, so they are not written here.

  2. PLANT ROOMS. UPS, Generator, Mechanical, Central Plant and Roof are resized to
     hold their equipment at datasheet footprints (core/equipment_geometry.FOOTPRINTS)
     with service clearances, and every free-standing unit gets its own floor_x /
     floor_y / rotation_deg. Clearances follow common practice: ~1 m rear access on
     switchgear, >= 1.2 m working space in front of electrical gear (NEC 110.26), a
     tube-pull bay the length of the shell on one end of each chiller, ~1.5 m
     between gensets.

  3. MOUNTING. Meters go INSIDE the panel they clamp (``panel``), panelboards on
     the wall, plant immersion probes and valves on the header (``pipe``, they were
     filed as ``rack_front``), Modbus gateway / BACnet router in a control panel.

  4. DEFECTS. A probe on a rack's front door must stand at that rack: snap every
     rack-mounted sensor to its rack's x/y and to the floor its room is on. (Five
     DC1/DC2 Server Hall B probes were on floor '1' while the hall is on '2', and
     the rack-3 / rack-5 probes sat at rack 1's x.)

Deliberately NOT changed here: the white-space halls. Their CRAHs were re-placed
at the aisle ends by tools/regrid_halls_aisle_end_crahs.py (S2 decision D-1,
option A); run that first. ``python tools/layout_buildings.py --check`` reports
any overlap left.

The Central Plant CT basin probe (CTB) physically sits in the tower basin on the
roof; it stays in Central Plant because its room is part of its name.

Idempotent: every coordinate is assigned absolutely.

    python tools/layout_buildings.py topologies/dual_dc_enterprise.json
    python tools/layout_buildings.py topologies/dual_dc_enterprise.json --check
    python tools/export_dcim_floorplan.py topologies/dual_dc_enterprise.json \\
        topologies/dual_dc_enterprise_floorplan.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.equipment_geometry import (  # noqa: E402
    RACK_MOUNTS, complete_building, effective_mount, footprint,
)

FLOOR_TO_FLOOR_M = 5.0

# room -> (level, origin x, origin y, width_m, depth_m or None = keep)
BUILDING = {
    "UPS Room":        ("G",    0.0,  0.0, 9.0,  6.0),
    "Mechanical Room": ("G",    0.0,  6.0, 8.0,  4.0),
    "Generator Room":  ("G",    9.0,  0.0, 10.0, 12.6),
    "Server Hall A":   ("1",    0.0,  0.0, None, None),
    # Server Hall A is 12.2 m wide since its CRAHs moved to the aisle ends
    # (tools/regrid_halls_aisle_end_crahs.py), so the rooms beside it on level 1
    # start where it ends.
    "Network Room":    ("1",   12.2,  0.0, None, None),
    "Central Plant":   ("1",   15.2,  0.0, 16.0, 15.0),
    "Server Hall B":   ("2",    0.0,  0.0, None, None),
    "Roof":            ("Roof", 0.0,  0.0, 12.0, 6.0),
}

# room -> {name head: (x, y, facing_deg, mounting, rack_facing or None)}
# x/y = centre of a free-standing unit, or the point a probe/meter/valve sits at.
# facing_deg: N 0, E 90, S 180, W 270 (core/equipment_geometry).
PLACE = {
    "UPS Room": {
        # rear-access switchgear line on the north wall, fronts facing S into the aisle
        "SWGR1": (2.80, 1.76, 180, "floor", None),
        "ATS1":  (5.16, 1.76, 180, "floor", None),
        "ATS2":  (7.16, 1.76, 180, "floor", None),
        # front-access UPS line against the south wall, facing N - 2.4 m aisle between
        "UPSA":  (2.33, 5.35, 0, "floor", None),
        "UPSB":  (5.98, 5.35, 0, "floor", None),
        "UTIL1": (2.00, 2.52, 180, "panel", None),   # revenue meter in SWGR1's incoming section
        "EV21":  (3.40, 2.52, 180, "panel", None),   # clamps SWGR1
        "THR":   (0.05, 3.70, 90, "wall", None),
    },
    "Generator Room": {
        # radiators to the north louvre wall, 1.5 m between sets
        "GEN1":  (2.52, 4.50, 0, "floor", None),
        "GEN2":  (6.66, 4.50, 0, "floor", None),
        "SWGR2": (5.00, 10.84, 0, "floor", None),    # paralleling gear facing the sets
        "EV21":  (5.60, 10.08, 0, "panel", None),    # clamps SWGR2
        "THR":   (0.05, 9.00, 90, "wall", None),
    },
    "Mechanical Room": {
        "MCC1":  (2.27, 0.36, 180, "floor", None),
        "MCC2":  (5.81, 0.36, 180, "floor", None),
        "EV21":  (2.27, 0.61, 180, "panel", None),   # clamps MCC1
        "EV22":  (5.81, 0.61, 180, "panel", None),   # clamps MCC2
        "BMSC2": (7.40, 3.20, None, "", "N"),        # BMS access switch in a wall rack
        "THR":   (0.05, 2.00, 90, "wall", None),
    },
    "Central Plant": {
        # three chillers side by side, tube-pull bay to the north, pumps to the south
        "CHL1":  (2.48, 8.28, 180, "floor", None),
        "CHL2":  (6.53, 8.28, 180, "floor", None),
        "CHL3":  (10.58, 8.28, 180, "floor", None),
        "CHWP1": (1.50, 12.30, 180, "floor", None),
        "CHWP2": (2.70, 12.30, 180, "floor", None),
        "CHWP3": (3.90, 12.30, 180, "floor", None),
        "CHWP4": (5.10, 12.30, 180, "floor", None),
        "CWP1":  (8.60, 12.30, 180, "floor", None),
        "CWP2":  (9.80, 12.30, 180, "floor", None),
        "CWP3":  (11.00, 12.30, 180, "floor", None),
        # headers along the south wall
        "CHWS":  (1.50, 13.90, 180, "pipe", None),
        "CHWR":  (2.70, 13.90, 180, "pipe", None),
        "FLOW":  (3.90, 13.90, 180, "pipe", None),
        "VCHW":  (5.10, 13.90, 180, "pipe", None),
        "CWS":   (8.60, 13.90, 180, "pipe", None),
        "CWR":   (9.80, 13.90, 180, "pipe", None),
        "VCW":   (11.00, 13.90, 180, "pipe", None),
        "CTB":   (12.20, 13.90, 180, "pipe", None),
        # electrical + controls on the east side
        "RPPA":  (15.35, 6.00, 270, "floor", None),
        "RPPB":  (15.35, 7.00, 270, "floor", None),
        "EV21":  (14.80, 6.00, 270, "panel", None),
        "EV22":  (14.80, 7.00, 270, "panel", None),
        "MBGW1": (13.00, 0.05, 180, "panel", None),  # BMS control panel, north wall
        "BRTR1": (13.40, 0.05, 180, "panel", None),
        # the plant's one IT rack: OOB/BMS switches + its two rack PDUs
        "OOBM1": (15.20, 1.80, None, "", "S"),
        "BMSC1": (15.20, 1.80, None, "", "S"),
        "PDUA":  (15.20, 1.80, None, "", "S"),
        "PDUB":  (15.20, 1.80, None, "", "S"),
        "THR":   (0.05, 7.50, 90, "wall", None),
    },
    "Roof": {
        "CT1": (2.88, 2.62, 180, "floor", None),
        "CT2": (5.64, 2.62, 180, "floor", None),
        "CT3": (8.40, 2.62, 180, "floor", None),
    },
}


def _head(name: str) -> str:
    return name.split("-", 1)[0]


def apply(topo: dict) -> list[str]:
    log: list[str] = []
    fp = topo.setdefault("floorplan", {})
    rooms = fp.setdefault("rooms", {})
    devs = [n["device"] for n in topo["nodes"] if n.get("device")]

    # 1. building placement + room extents
    for key, r in rooms.items():
        dc, room = key.split("/", 1)
        spec = BUILDING.get(room)
        if spec is None:
            continue
        level, ox, oy, w, d = spec
        r["level"] = level
        r["origin"] = {"x": ox, "y": oy}
        r["rotation_deg"] = 0
        r.pop("placement", None)
        if w is not None and (r.get("width_m"), r.get("depth_m")) != (w, d):
            log.append(f"room {key}: {r.get('width_m')}x{r.get('depth_m')} -> {w}x{d} m")
            r["width_m"], r["depth_m"] = w, d
    for dc in sorted({k.split("/", 1)[0] for k in rooms}):
        b = fp.setdefault("buildings", {}).setdefault(dc, {})
        b["name"] = f"{dc} Building"
        b["floor_to_floor_m"] = FLOOR_TO_FLOOR_M

    # 2-3. plant equipment + mounting
    for dv in devs:
        spec = PLACE.get(dv.get("room", ""), {}).get(_head(dv.get("name", "")))
        if spec is None:
            continue
        x, y, rot, mnt, rf = spec
        before = (dv.get("floor_x"), dv.get("floor_y"), dv.get("rotation_deg"),
                  dv.get("mounting", ""))
        dv["floor_x"], dv["floor_y"] = x, y
        dv["rotation_deg"] = rot
        dv["mounting"] = mnt
        if rf is not None:
            dv["rack_facing"] = rf
        if before != (x, y, rot, mnt):
            log.append(f"place {dv['name']}: {before} -> {(x, y, rot, mnt)}")

    # Hall perimeter gear: a CRAH on the back wall faces into the room (N); the
    # A/B mechanical power panels hang ON that wall, not out in the CRAH line.
    for dv in devs:
        g = rooms.get(f"{dv.get('datacenter')}/{dv.get('room')}", {})
        if g.get("class") != "white_space" or dv.get("floor_y") is None:
            continue
        depth = float(g.get("depth_m") or 0)
        if dv.get("device_type") == "crah" and dv.get("rotation_deg") is None:
            dv["rotation_deg"] = 0 if dv["floor_y"] > depth / 2 else 180
            log.append(f"face {dv['name']}: {dv['rotation_deg']}")
        elif dv.get("device_type") == "mpp" and depth:
            y = round(depth - 0.075, 3) if dv["floor_y"] > depth / 2 else 0.075
            if (dv["floor_y"], dv.get("mounting"), dv.get("rotation_deg")) != (y, "wall", 0 if y > 1 else 180):
                log.append(f"wall {dv['name']}: y {dv['floor_y']} -> {y}")
            dv["floor_y"], dv["mounting"] = y, "wall"
            dv["rotation_deg"] = 0 if y > 1 else 180

    # 4. rack-mounted probes stand at their rack, on their room's floor
    room_floor: dict = defaultdict(Counter)
    rack_xy: dict = defaultdict(Counter)
    for dv in devs:
        room_floor[(dv.get("datacenter"), dv.get("room"))][str(dv.get("floor"))] += 1
        if dv.get("device_type") != "sensor" and effective_mount(dv) in RACK_MOUNTS \
                and dv.get("floor_x") is not None:
            rack_xy[(dv.get("datacenter"), dv.get("room"), dv.get("rack_row"),
                     dv.get("rack_num"))][(dv["floor_x"], dv["floor_y"])] += 1
    for dv in devs:
        if dv.get("device_type") != "sensor" or effective_mount(dv) not in RACK_MOUNTS:
            continue
        rk = (dv.get("datacenter"), dv.get("room"), dv.get("rack_row"), dv.get("rack_num"))
        if rack_xy.get(rk):
            xy = rack_xy[rk].most_common(1)[0][0]
            if (dv.get("floor_x"), dv.get("floor_y")) != xy:
                log.append(f"snap {dv['name']}: ({dv.get('floor_x')}, {dv.get('floor_y')}) -> {xy}")
                dv["floor_x"], dv["floor_y"] = xy
        want = room_floor[(dv.get("datacenter"), dv.get("room"))].most_common(1)[0][0]
        if str(dv.get("floor")) != want:
            log.append(f"floor {dv['name']}: {dv.get('floor')} -> {want}")
            dv["floor"] = want
    return log


def _rect(dv: dict):
    f = footprint(dv)
    if not f or dv.get("floor_x") is None:
        return None
    w, d = f["width"], f["depth"]
    if int(round((dv.get("rotation_deg") or 0) / 90.0)) % 2:
        w, d = d, w
    return (dv["floor_x"] - w / 2, dv["floor_y"] - d / 2,
            dv["floor_x"] + w / 2, dv["floor_y"] + d / 2)


def check(topo: dict) -> list[str]:
    """Free-standing equipment outside its room, or overlapping other equipment."""
    issues: list[str] = []
    rooms = topo.get("floorplan", {}).get("rooms", {})
    by_room: dict = defaultdict(list)
    for n in topo["nodes"]:
        dv = n["device"]
        if effective_mount(dv) == "floor" and _rect(dv):
            by_room[f"{dv['datacenter']}/{dv['room']}"].append(dv)
    for key, ds in sorted(by_room.items()):
        g = rooms.get(key, {})
        W, D = g.get("width_m") or 0, g.get("depth_m") or 0
        for dv in ds:
            x0, y0, x1, y1 = _rect(dv)
            if x0 < -1e-6 or y0 < -1e-6 or x1 > W + 1e-6 or y1 > D + 1e-6:
                issues.append(f"{key}: {dv['name']} outside the room "
                              f"({x0:.2f},{y0:.2f})-({x1:.2f},{y1:.2f}) in {W}x{D}")
        for i, a in enumerate(ds):
            for b in ds[i + 1:]:
                ax0, ay0, ax1, ay1 = _rect(a)
                bx0, by0, bx1, by1 = _rect(b)
                if ax0 < bx1 - 1e-6 and bx0 < ax1 - 1e-6 and ay0 < by1 - 1e-6 and by0 < ay1 - 1e-6:
                    issues.append(f"{key}: {a['name']} overlaps {b['name']}")
    return issues


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    topo = json.loads(path.read_text(encoding="utf-8"))
    if "--check" in argv:
        issues = check(topo)
        print("\n".join(issues) or "no placement issues")
        return 1 if issues else 0
    log = apply(topo)
    print("\n".join(log) or "already laid out")
    path.write_text(json.dumps(topo, indent=2), encoding="utf-8")   # file has no final newline
    placed = complete_building(topo["floorplan"], (n["device"] for n in topo["nodes"]))
    for dc, b in placed["buildings"].items():
        lv = ", ".join(f"{x['name']}@{x['elevation_m']}m" for x in b["levels"])
        print(f"{dc}: {b.get('extent_m')} levels {lv}")
    issues = check(topo)
    if issues:
        print(f"\n{len(issues)} placement issue(s) left (see --check):")
        print("\n".join(f"  {i}" for i in issues[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
