#!/usr/bin/env python3
"""Export a standalone DCIM asset / floor-plan file from a simulator topology.

The simulator topology (topologies/*.json) is the *device emulation* source of
truth: it defines what each simulated device exposes over SNMP/Redfish/gNMI.
Physical placement (floor x/y, aisle, rack) is **not** device telemetry -- a
real device never reports its floor coordinates. In production that data lives
in the DCIM's own asset database.

This script extracts exactly that asset-DB layer into a self-contained file an
external DCIM can import once, then bind to live telemetry by device identity
(Redfish Location.Placement / SNMP sysLocation).

Design choices (deliberately a clean asset model, not a device dump):
  * NORMALIZED: floor coordinates live once per *rack*; devices reference their
    rack by id (no per-device coordinate denormalization).
  * PLACEMENT ONLY: identity + location + power feeds. No interfaces, metrics,
    SNMP communities, or live telemetry -- those belong to the simulator.
  * Power feeds resolved from internal node ids to PDU device names so the file
    is human-readable and DCIM-importable on its own.

Usage:
    python tools/export_dcim_floorplan.py \
        topologies/dual_dc_enterprise.json \
        topologies/dual_dc_enterprise_floorplan.json
"""
from __future__ import annotations

import json
import sys
from collections import OrderedDict


def rack_id(dc: str, room: str, floor: str, row, num) -> str:
    """Stable rack key: DC:Room:F<floor>:R<row>:RACK<num>."""
    room_slug = room.replace(" ", "")
    return f"{dc}:{room_slug}:F{floor}:R{row}:RACK{num}"


def build(topology: dict) -> "OrderedDict":
    nodes = topology["nodes"]
    fp = topology.get("floorplan", {})

    # id -> device name, for resolving power-feed references to PDU names.
    id_to_name = {}
    for n in nodes:
        dev = n["device"]
        id_to_name[dev.get("id", n["id"])] = dev.get("name")
        id_to_name[n["id"]] = dev.get("name")

    def feed_name(ref):
        if not ref:
            return None
        return id_to_name.get(ref, ref)

    racks: "OrderedDict[str, dict]" = OrderedDict()
    devices = []

    for n in nodes:
        dev = n["device"]
        dc = dev.get("datacenter")
        room = dev.get("room")
        floor = str(dev.get("floor"))
        row = dev.get("rack_row")
        num = dev.get("rack_num")
        rid = rack_id(dc, room, floor, row, num)

        if rid not in racks:
            racks[rid] = {
                "rack_id": rid,
                "datacenter": dc,
                "datacenter_city": dev.get("datacenter_city"),
                "room": room,
                "floor": floor,
                "row": row,
                "rack_num": num,
                # placement -- the data a device can NEVER report over a protocol
                "floor_x": dev.get("floor_x"),
                "floor_y": dev.get("floor_y"),
                "rack_facing": dev.get("rack_facing") or None,
                "cold_aisle": dev.get("cold_aisle") or None,
                "hot_aisle": dev.get("hot_aisle") or None,
                "device_ids": [],
            }
        racks[rid]["device_ids"].append(dev.get("id", n["id"]))

        devices.append({
            "id": dev.get("id", n["id"]),
            "name": dev.get("name"),
            "device_type": dev.get("device_type"),
            "vendor": dev.get("vendor"),
            "model": dev.get("model_name") or None,
            # binding key: which rack + RU. This is what a DCIM resolves from the
            # device-side hint (Redfish Location.Placement / SNMP sysLocation).
            "rack_id": rid,
            "rack_unit": dev.get("rack_unit"),
            # power topology: which PDU feeds (A/B) this device draws from.
            "power_draw_w": dev.get("power_draw_w"),
            "feed_a": feed_name(dev.get("power_source_a")),
            "feed_b": feed_name(dev.get("power_source_b")),
        })

    # Per-rack derived inventory summary (asset reporting convenience).
    for r in racks.values():
        ids = set(r["device_ids"])
        members = [d for d in devices if d["id"] in ids]
        r["device_count"] = len(members)
        r["it_power_draw_w"] = sum(d.get("power_draw_w") or 0 for d in members)

    out = OrderedDict()
    out["schema"] = "dcim-floorplan/1.0"
    out["description"] = (
        "DCIM asset / floor-plan export. Physical placement DB intended to be "
        "imported by a DCIM and joined to live device telemetry by device "
        "identity. Contains NO telemetry -- poll the simulator's device "
        "protocols (Redfish/SNMP/gNMI) for live values."
    )
    src_meta = topology.get("metadata", {})
    out["source_topology"] = {
        "name": src_meta.get("name"),
        "description": src_meta.get("description"),
    }
    # Floor geometry: units + per-room extent and aisle containment, for
    # drawing the floor background and running spatial interpolation.
    out["floorplan"] = {
        "units": fp.get("units"),
        "origin": fp.get("origin"),
        "rack_footprint": fp.get("rack_footprint"),
        "rack_pitch": fp.get("rack_pitch"),
        "row_pitch": fp.get("row_pitch"),
        "aisle_width": fp.get("aisle_width"),
        "rooms": fp.get("rooms", {}),
    }
    out["racks"] = list(racks.values())
    out["devices"] = devices

    out["summary"] = {
        "datacenters": sorted({r["datacenter"] for r in racks.values()}),
        "rooms": len(out["floorplan"]["rooms"]),
        "racks": len(racks),
        "devices": len(devices),
    }
    return out


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = argv[1], argv[2]
    with open(src, "r", encoding="utf-8") as f:
        topology = json.load(f)
    out = build(topology)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    s = out["summary"]
    print(
        f"Wrote {dst}\n"
        f"  datacenters: {', '.join(s['datacenters'])}\n"
        f"  rooms: {s['rooms']}  racks: {s['racks']}  devices: {s['devices']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))