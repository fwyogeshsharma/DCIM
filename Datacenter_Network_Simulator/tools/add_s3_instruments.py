#!/usr/bin/env python3
"""Fit the instruments a real site wires to its BMS and nobody networks
(dcim-platform docs/27 §8b.3, §8c.2 - S3 increment 2).

Each site gets, on the same RS-485 trunk the chilled-water thermowells and the
room transmitters already sit on, behind the same Moxa gateway:

  DOOR-<DC>-<ROOM>  a door contact on each room the BMS watches - the halls,
                    the Network Room, the UPS Room, the Meet-Me Room - at the
                    opening the floor plan draws for that room.
  H2-<DC>-UR        a hydrogen detector in the battery room. IEEE 1635 / NFPA 1
                    cap a VRLA room at 1 % H2 by volume, 25 % of the lower
                    explosive limit, so the room has a detector and it alarms.
  BTT-<DC>-UR       a battery string temperature probe. VRLA life halves per
                    ~10 K above 25 C; the string runs warmer than the room.
  FUEL-<DC>-GR      the bulk diesel tank's level transmitter, in the yard.
  WTR-<DC>-CP       the makeup water tank's level transmitter, read by the plant.
  THR-<DC>-OR       the operations room's temperature/humidity transmitter -
                    the one room tools/add_room_air_probes.py could not reach
                    because nothing stands in it yet.

None of these owns an IP, a port or a cord: a contact or a 4-20 mA loop is a
pair of wires back to the controller. Their roles are read off the leading
name code and the "Plant " model prefix (core.device_state_store._PROBE_ROLES),
their registers come from core.modbus_register_map.PROBE_MAPS.

Idempotent, and it REPAIRS: an instrument that is in the file without a trunk
edge or a place in its gateway's child list gets both. Run after
tools/layout_buildings.py, because the door positions come from the fabric the
building derives (core.equipment_geometry.complete_site).

    python tools/add_s3_instruments.py topologies/dual_dc_enterprise.json
    python tools/add_s3_instruments.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.equipment_geometry import complete_site  # noqa: E402

VENDOR = "Vertiv (Liebert)"
#: name code -> (model, mounting, where). "where" is a room name, or "door:<room>"
#: for a contact at that room's opening.
INSTRUMENTS = [
    ("DOOR", "Plant Door Contact",          "wall",  "door:Server Hall A",  "HA"),
    ("DOOR", "Plant Door Contact",          "wall",  "door:Server Hall B",  "HB"),
    ("DOOR", "Plant Door Contact",          "wall",  "door:Network Room",   "NR"),
    ("DOOR", "Plant Door Contact",          "wall",  "door:UPS Room",       "UR"),
    ("DOOR", "Plant Door Contact",          "wall",  "door:Meet-Me Room",   "MM"),
    ("H2",   "Plant Hydrogen Detector",     "wall",  "UPS Room",            "UR"),
    ("BTT",  "Plant Battery Temperature",   "panel", "UPS Room",            "UR"),
    ("FUEL", "Plant Tank Level Transmitter", "panel", "Generator Yard",     "GR"),
    ("WTR",  "Plant Tank Level Transmitter", "pipe",  "Central Plant",      "CP"),
    ("THR",  "Plant Room Air T/RH",         "wall",  "Operations Room",     "OR"),
]
#: Where in its room each non-door instrument sits (room metres).
SPOT = {
    ("H2", "UR"):   (4.50, 0.05),    # high on the north wall above the UPS line - hydrogen rises
    ("BTT", "UR"):  (2.33, 5.60),    # on the A string, by UPSA
    ("FUEL", "GR"): (9.95, 6.30),    # the yard's east fence line, toward the tank
    ("WTR", "CP"):  (15.95, 11.00),  # the plant's north-east corner, where the makeup main enters
    ("THR", "OR"):  (0.05, 2.50),
}


def _door_spot(placed: dict, dc: str, room: str) -> tuple[float, float] | None:
    """The room-relative centre of the opening the fabric draws for the room."""
    key = f"{dc}/{room}"
    r = placed.get("rooms", {}).get(key)
    if not r or not isinstance(r.get("origin"), dict):
        return None
    for f in placed.get("fabric", {}).get(dc, []):
        if f.get("kind") == "door" and (f.get("attrs") or {}).get("room") == key:
            xs = [p[0] for p in f["polygon_m"]]
            ys = [p[1] for p in f["polygon_m"]]
            cx, cy = (min(xs) + max(xs)) / 2 - float(r["origin"]["x"]), (min(ys) + max(ys)) / 2 - float(r["origin"]["y"])
            # On the wall, just inside the room.
            w, d = float(r.get("width_m") or 0), float(r.get("depth_m") or 0)
            cx = min(max(cx, 0.05), max(0.05, w - 0.05))
            cy = min(max(cy, 0.05), max(0.05, d - 0.05))
            return round(cx, 2), round(cy, 2)
    return None


def main(path: str, dry_run: bool = False) -> int:
    doc = json.load(open(path, encoding="utf-8"))
    nodes = doc["nodes"]
    taken = {n["id"] for n in nodes}
    by_name = {(n.get("device") or {}).get("name"): n for n in nodes}
    devs = [n["device"] for n in nodes if n.get("device")]
    placed = complete_site(doc.get("floorplan") or {}, devs)
    rooms = placed.get("rooms", {})

    gateways: dict[str, dict] = {}
    units: dict[str, set] = {}
    anchors: dict[str, dict] = {}
    for d in devs:
        dc = d.get("datacenter")
        if d.get("device_type") == "modbus_gateway" and dc:
            gateways[dc] = d
        if d.get("modbus_gateway_ip"):
            units.setdefault(d["modbus_gateway_ip"], set()).add(int(d.get("modbus_unit_id") or 0))
        if dc and dc not in anchors:
            anchors[dc] = d

    added = repaired = 0
    for dc in sorted(gateways):
        gw = gateways[dc]
        gw_ip = gw.get("mgmt_ip") or gw.get("ip_address")
        used = units.setdefault(gw_ip, set())
        gw_node = by_name[gw["name"]]["id"]
        for code, model, mounting, where, suffix in INSTRUMENTS:
            name = f"{code}-{dc}-{suffix}"
            room = where.split(":", 1)[1] if where.startswith("door:") else where
            rkey = f"{dc}/{room}"
            if rkey not in rooms:
                print(f"  SKIP {name}: no room {rkey} in the floor plan")
                continue
            if name in by_name:
                # Repair the trunk and the child list; never re-place.
                if name not in gw.get("modbus_children", []):
                    gw.setdefault("modbus_children", []).append(name); repaired += 1
                nid = by_name[name]["id"]
                if not any(e.get("src") == gw_node and e.get("dst") == nid for e in doc["edges"]):
                    doc["edges"].append({"src": gw_node, "dst": nid, "src_iface": None, "dst_iface": None, "layer": "fieldbus"})
                    repaired += 1
                continue
            spot = _door_spot(placed, dc, room) if where.startswith("door:") else SPOT.get((code, suffix))
            if spot is None:
                print(f"  SKIP {name}: no place for it in {rkey}")
                continue
            unit_id = next(i for i in range(1, 248) if i not in used)
            used.add(unit_id)
            new_id = uuid.uuid4().hex[:8]
            while new_id in taken:
                new_id = uuid.uuid4().hex[:8]
            taken.add(new_id)
            a = anchors[dc]
            device = {
                "name": name, "device_type": "sensor", "vendor": VENDOR, "ip_address": "",
                "snmp_port": 0, "gnmi_port": 57400, "snmp_community": "", "interface_count": 0,
                "interface_groups": [], "model_name": model, "metrics_enabled": False, "id": new_id,
                "interfaces": [], "mgmt_ip": "", "mgmt_vlan": a.get("mgmt_vlan", 10),
                "power_draw_w": 0, "ups_backup": "",
                "country": a.get("country"), "datacenter_city": a.get("datacenter_city"),
                "datacenter": dc, "room": room, "floor": rooms[rkey].get("level"),
                "rack_row": 0, "rack_num": 0, "rack_unit": 0,
                "cpu_usage": 0, "memory_total": 0, "memory_used": 0, "disk_total": 0, "disk_used": 0,
                "sys_uptime": 0, "cpu_temp": 0.0, "inlet_temp": 0.0, "mid_temp": 0.0, "outlet_temp": 0.0,
                "humidity": 0.0, "dewpoint": 0.0, "airflow": 0.0, "sys_contact": "", "sys_location_override": "",
                "floor_x": spot[0], "floor_y": spot[1], "mounting": mounting,
                "outlets": [], "psus": [],
                "modbus_role": "rtu_slave", "modbus_unit_id": unit_id, "modbus_gateway_ip": gw_ip,
            }
            if code == "THR":
                device["inlet_temp"], device["humidity"] = 22.0, 45.0
            pos = dict(by_name[gw["name"]].get("position") or {"x": 0, "y": 0})
            pos["y"] = int(pos.get("y", 0)) + 60 + 20 * len(gw.get("modbus_children", []))
            node = {"id": new_id, "position": pos, "device": device}
            nodes.append(node)
            by_name[name] = node
            gw.setdefault("modbus_children", []).append(name)
            doc["edges"].append({"src": gw_node, "dst": new_id, "src_iface": None, "dst_iface": None, "layer": "fieldbus"})
            added += 1
            print(f"  + {name:14} unit {unit_id:3} in {room} at {spot}")
    print(f"{added} instrument(s) added, {repaired} repair(s)")
    if not dry_run and (added or repaired):
        Path(path).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], "--dry-run" in sys.argv))
