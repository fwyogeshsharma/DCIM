#!/usr/bin/env python3
"""S3 increment 2b: the service transformer in the substation (docs/27 §8c.2).

Every site of this size takes its utility supply at medium voltage and steps
it down on its own compound: a 2 MVA oil-immersed ground-mounted unit in a
fenced yard, 11 kV in, 415 V out to the LV main switchboard. The transformer
has no electronics. What a DCIM polls is the monitor bolted to its tank - oil
and winding temperature, load current, tap position, the gas relay - over
Modbus TCP on the BMS plane, the way the ION meter beside it is polled.

Per datacenter this adds:

  Substation          an outdoor room (class "outdoor", level "Site") on the
                      compound's west margin, where complete_site() already
                      drew the utility substation. The room becomes the zone.
  TX1-<DC>-SB         device_type "transformer", Schneider Minera 2000 kVA ONAN/ONAF,
                      Modbus TCP on the next BMS-plane address after the
                      revenue meter, a management port on the plant's BMS
                      access switch, and a POWER edge into the revenue meter:
                      MV in, LV out, meter on the secondary.

Idempotent, and it repairs: a room without its extents, a transformer
without its edges, each gets what is missing. Run before tools/layout_buildings.py
if the room is new (the layout tool places the unit), after it otherwise;
either order converges.

    python tools/add_substation.py topologies/dual_dc_enterprise.json
    python tools/add_substation.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

ROOM = "Substation"
ROOM_SPEC = {"level": "Site", "origin": {"x": -16.0, "y": 2.0}, "width_m": 10.0, "depth_m": 11.0}
VENDOR, MODEL = "Schneider Electric", "Schneider Minera 2000kVA ONAN 11/0.415kV"
RATED_W = 2_000_000
#: room metres: mid-yard on its plinth, radiators east-west
SPOT = (5.00, 5.50, 90)


def _next_ip(seed: str, used: set) -> str:
    a, b, c, d = (int(x) for x in seed.split("."))
    for _ in range(65535):
        d += 1
        if d > 254:
            d = 1
            c += 1
        ip = f"{a}.{b}.{c}.{d}"
        if ip not in used:
            used.add(ip)
            return ip
    raise RuntimeError("no free address after " + seed)


def _free_port(edges: list, switch_id: str, count: int) -> int:
    """The lowest port on *switch_id* that no cable lands on, either end."""
    taken = set()
    for e in edges:
        if e.get("dst") == switch_id and e.get("dst_iface") is not None:
            taken.add(int(e["dst_iface"]))
        if e.get("src") == switch_id and e.get("src_iface") is not None:
            taken.add(int(e["src_iface"]))
    for p in range(count):
        if p not in taken:
            return p
    raise RuntimeError(f"no free port on {switch_id}")


def main(path: str, dry_run: bool = False) -> int:
    doc = json.load(open(path, encoding="utf-8"))
    nodes, edges = doc["nodes"], doc["edges"]
    rooms = doc.setdefault("floorplan", {}).setdefault("rooms", {})
    taken = {n["id"] for n in nodes}
    by_name = {(n.get("device") or {}).get("name"): n for n in nodes}
    devs = [n["device"] for n in nodes if n.get("device")]
    used_ips = {d.get("mgmt_ip") for d in devs if d.get("mgmt_ip")}

    changes = 0
    for dc in sorted({d.get("datacenter") for d in devs if d.get("datacenter")}):
        in_dc = [d for d in devs if d.get("datacenter") == dc]
        util = next((d for d in in_dc if d.get("device_type") == "utility_feed"), None)
        if util is None:
            print(f"{dc}: no utility feed - skipped")
            continue

        # The room, as tools/layout_buildings.py would create it.
        key = f"{dc}/{ROOM}"
        room = rooms.get(key)
        if room is None:
            rooms[key] = {"datacenter": dc, "room": ROOM, "class": "outdoor", "containment": "none",
                          "rows": [], "racks_per_row": 0, "aisles": [], "rotation_deg": 0, **ROOM_SPEC}
            print(f"  + room {key} (outdoor, Site)")
            changes += 1
        else:
            for k, v in ROOM_SPEC.items():
                if room.get(k) != v:
                    room[k] = v
                    changes += 1
            if room.get("class") != "outdoor":
                room["class"] = "outdoor"
                changes += 1

        name = f"TX1-{dc}-SB"
        util_node = by_name[util["name"]]
        if name in by_name:
            node = by_name[name]
        else:
            new_id = uuid.uuid4().hex[:8]
            while new_id in taken:
                new_id = uuid.uuid4().hex[:8]
            taken.add(new_id)
            device = copy.deepcopy(util)
            device.update({
                "name": name, "device_type": "transformer", "vendor": VENDOR, "model_name": MODEL,
                "id": new_id, "ip_address": "", "snmp_port": 0, "snmp_community": "",
                "mgmt_ip": _next_ip(util["mgmt_ip"], used_ips),
                "metrics_enabled": True, "rated_power_w": RATED_W, "power_draw_w": 0,
                "room": ROOM, "floor": "Site", "rack_row": 0, "rack_num": 0, "rack_unit": 0,
                "floor_x": SPOT[0], "floor_y": SPOT[1], "rotation_deg": SPOT[2], "mounting": "floor",
                "outlets": [], "psus": [],
            })
            for iface in device.get("interfaces", []):
                iface["in_octets"] = iface["out_octets"] = 0
                iface["in_errors"] = iface["out_errors"] = 0
                iface["connected_to_device"] = iface["connected_to_iface"] = None
            pos = dict(util_node.get("position") or {"x": 0, "y": 0})
            pos["x"] = int(pos.get("x", 0)) - 220
            node = {"id": new_id, "position": pos, "device": device}
            nodes.append(node)
            by_name[name] = node
            devs.append(device)
            print(f"  + {name} {MODEL} at {device['mgmt_ip']} in {ROOM}")
            changes += 1
        tx_id = node["id"]

        # Power: MV in from the street, LV out through the meter.
        if not any(e.get("layer") == "power" and {e["src"], e["dst"]} == {tx_id, util_node["id"]} for e in edges):
            edges.append({"src": tx_id, "dst": util_node["id"], "src_iface": 0, "dst_iface": 0,
                          "broken": False, "layer": "power"})
            print(f"  + power {name} -> {util['name']}")
            changes += 1

        # Management: the monitor's Ethernet lands where the meter's does - the
        # BMS access switch in the plant, never an IT out-of-band switch.
        if not any(e.get("layer") == "management" and tx_id in (e["src"], e["dst"]) for e in edges):
            sw = next((e["dst"] if e["src"] == util_node["id"] else e["src"] for e in edges
                       if e.get("layer") == "management" and util_node["id"] in (e["src"], e["dst"])), None)
            if sw is None:
                print(f"  ! {name}: the meter has no management edge to copy - left unmanaged")
            else:
                sw_dev = next(n["device"] for n in nodes if n["id"] == sw)
                port = _free_port(edges, sw, int(sw_dev.get("interface_count") or len(sw_dev.get("interfaces") or [])))
                edges.append({"src": tx_id, "dst": sw, "src_iface": 0, "dst_iface": port,
                              "broken": False, "layer": "management"})
                print(f"  + management {name} -> {sw_dev['name']} port {port}")
                changes += 1

    print(f"{changes} change(s)")
    if changes and not dry_run:
        Path(path).write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(args[0], dry_run="--dry-run" in sys.argv))
