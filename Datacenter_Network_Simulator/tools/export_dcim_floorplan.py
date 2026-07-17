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
from collections import Counter, OrderedDict
from pathlib import Path

# Make `core` importable whether run as `python tools/export_dcim_floorplan.py`
# or `python -m tools.export_dcim_floorplan`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.rack_capacity import (  # noqa: E402
    leaf_port_roles, device_u_height, SERVER_U_HEIGHT,
    FIRST_SERVER_UNIT, LAST_SERVER_UNIT, RACK_POWER_BUDGET_W_DEFAULT,
)


def rack_id(dc: str, room: str, floor: str, row, num) -> str:
    """Stable rack key: DC:Room:F<floor>:R<row>:RACK<num>."""
    room_slug = room.replace(" ", "")
    return f"{dc}:{room_slug}:F{floor}:R{row}:RACK{num}"


def fabric_ifaces(topology: dict) -> dict:
    """device id -> iface indices carrying a production link to ANOTHER switch.

    A leaf's spine uplinks and its MLAG peer-link are fabric ports, not server slots,
    so they must not be counted as server-facing capacity. Index position cannot
    answer this — leaf_port_roles splits by a real chassis layout (48x25G before
    6x100G) while this topology wires its spines onto ports 0-3, the FRONT of the
    downlink range — so it is read from the edges. Mirrors
    TopologyEngine.fabric_ifaces, which the live API and fleet use.

    Reads the raw topology's src/dst, where src_iface belongs to src (unambiguous,
    unlike a networkx MultiGraph's edge iteration order)."""
    sw = {i for i, d in _devices_by_id(topology).items()
          if d.get("device_type") == "switch"}
    out: dict = {}
    for e in topology.get("edges", []):
        if e.get("layer", "production") != "production":
            continue
        s, d = e.get("src"), e.get("dst")
        if s not in sw or d not in sw:
            continue                       # only switch-to-switch is fabric
        if e.get("src_iface") is not None:
            out.setdefault(s, set()).add(e["src_iface"])
        if e.get("dst_iface") is not None:
            out.setdefault(d, set()).add(e["dst_iface"])
    return out


def _devices_by_id(topology: dict) -> dict:
    return {n["device"].get("id", n["id"]): n["device"] for n in topology["nodes"]}


def build(topology: dict) -> "OrderedDict":
    nodes = topology["nodes"]
    fp = topology.get("floorplan", {})
    fabric = fabric_ifaces(topology)

    # id -> device name, for resolving power-feed references to PDU names.
    id_to_name = {}
    id_to_dev = {}
    for n in nodes:
        dev = n["device"]
        id_to_name[dev.get("id", n["id"])] = dev.get("name")
        id_to_name[n["id"]] = dev.get("name")
        id_to_dev[dev.get("id", n["id"])] = dev

    # A/B feeds read from the CORDS, never from Device.power_source_a/b. That field is
    # a cache, and it drifts exactly like Interface.connected_to_device: clone a device
    # and it copies the template's feed, re-cord it and nothing updates the field. In
    # this topology it had drifted four ways — 14 network devices naming the WRONG
    # HALL's PDUs, 6 CDUs corded but unrecorded, 55 records for cords that do not
    # exist, and 72 references to devices that no longer exist at all. The power edge
    # carries supply_node/load_node/outlet/psu and cannot lie about what is plugged in.
    # (core.topology_engine.power_feeds does the same thing at runtime; this is the
    # offline twin, reading the raw topology's edges.)
    cords_by_load: dict = {}
    for e in topology.get("edges", []):
        if e.get("layer") != "power":
            continue
        sup, load = e.get("supply_node"), e.get("load_node")
        if not sup or not load:
            continue          # upstream feed (rpp->pdu, mcc->pump): a breaker position,
                              # not an outlet — it terminates on nothing, so no feed.
        cords_by_load.setdefault(load, []).append((e.get("psu"), sup))

    def feeds_of(dev_id):
        """(feed_a, feed_b) — the PDUs actually corded to this device, ordered by the
        PSU each cord lands on, so feed_a is PSU1's supply and feed_b is PSU2's."""
        cs = sorted(cords_by_load.get(dev_id, []), key=lambda x: (x[0] is None, x[0]))
        names = [id_to_name.get(sup, sup) for _psu, sup in cs]
        return (names[0] if names else None, names[1] if len(names) > 1 else None)

    # Fleet-added rows carry synthetic rack_row labels >= _FLEET_ROW_BASE (chosen
    # globally-unique so a rack's internal (dc, row, num) key never collides across
    # halls — see core/fleet_lifecycle.py). Those labels are an internal detail and
    # must NOT leak to the floor plan (they read as "Row 1001" next to "Row 1/2").
    # Remap them PER ROOM to sequential numbers continuing the room's curated rows,
    # so the viewer shows Row 1,2,3,4 instead of 1,2,1001,1002. Display-only: the
    # devices' real rack_row is untouched; only this exported doc is renumbered
    # (self-consistent — racks + device.rack_id use the same remapped value, and
    # telemetry joins by device identity, not rack_id).
    _FLEET_ROW_BASE = 1000
    _per_room_rows: dict = {}
    for n in nodes:
        d = n["device"]
        rr = d.get("rack_row")
        if rr is None:
            continue
        _per_room_rows.setdefault(
            (d.get("datacenter"), d.get("room"), str(d.get("floor"))), set()).add(rr)
    row_remap: dict = {}
    for key, rowset in _per_room_rows.items():
        nums = [r for r in rowset if isinstance(r, (int, float))]
        curated = sorted(r for r in nums if r < _FLEET_ROW_BASE)
        fleet   = sorted(r for r in nums if r >= _FLEET_ROW_BASE)
        m = {r: r for r in curated}
        nxt = (max(curated) if curated else 0) + 1
        for r in fleet:
            m[r] = nxt
            nxt += 1
        row_remap[key] = m

    def disp_row(dc, room, floor, rr):
        return row_remap.get((dc, room, str(floor)), {}).get(rr, rr)

    racks: "OrderedDict[str, dict]" = OrderedDict()
    devices = []

    for n in nodes:
        dev = n["device"]
        dc = dev.get("datacenter")
        room = dev.get("room")
        floor = str(dev.get("floor"))
        row = disp_row(dc, room, floor, dev.get("rack_row"))
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
            # Height of the body, so an importer can draw the elevation. rack_unit is
            # only the BOTTOM of the device: a 2U server at U39 fills U39 AND U40, and
            # a DCIM told only "U39" would draw it 1U tall and leave U40 bookable.
            # Per-SKU (core/device_models.MODEL_U_HEIGHT) — a DL360 is 1U, a DL560 4U.
            "u_height": (device_u_height(dev.get("device_type"),
                                         dev.get("model_name") or "")
                         if dev.get("rack_unit") else None),
            # power topology: which PDU feeds (A/B) this device draws from.
            "power_draw_w": dev.get("power_draw_w"),
            # From the cords, not Device.power_source_a/b — see feeds_of().
            "feed_a": feeds_of(dev.get("id", n["id"]))[0],
            "feed_b": feeds_of(dev.get("id", n["id"]))[1],
        })

    # Per-rack derived inventory summary (asset reporting convenience).
    for r in racks.values():
        ids = set(r["device_ids"])
        members = [d for d in devices if d["id"] in ids]
        r["device_count"] = len(members)
        r["it_power_draw_w"] = sum(d.get("power_draw_w") or 0 for d in members)

        # Compute-rack capacity + dual-homing (MLAG) readiness. Only racks with a
        # leaf switch get a server_capacity; non-compute racks leave it null.
        #
        # Capacity is the binding minimum of the THREE real limits, measured against
        # what this rack actually holds — not a flat constant. It used to report
        # POWER_CAP_DEFAULT (22), which bakes in ~800W a server and ignores U-space
        # entirely; now that power and height both follow the SKU, 22 is right for no
        # rack at all: a rack of 500W 1U DL360s takes 35, one of 1000W 2U R7525s takes
        # 17. Still flip-invariant — a dual-homed server uses one downlink on EACH of
        # two leaves, so the count does not change when MLAG is adopted.
        #   ports  — the leaf's server-facing downlinks, less any already carrying a
        #            spine uplink or the peer-link (those are fabric, not server slots)
        #   U      — whole rack units left, divided by what one more of THIS rack's
        #            server actually stands (2U for most, 1U for a DL360)
        #   power  — headroom in the per-rack budget, divided by that server's real
        #            nameplate
        # device_ids is a list and keeps insertion order — iterate THAT, not the set,
        # or `full` (and every choice made from it) comes out in arbitrary order and
        # this export stops being reproducible.
        full = [id_to_dev[i] for i in r["device_ids"] if i in id_to_dev]
        sw_ids = [i for i in r["device_ids"] if i in id_to_dev
                  and id_to_dev[i].get("device_type") == "switch"]
        leaf_id = next((i for i in sw_ids if id_to_dev[i].get("mlag_ready")),
                       sw_ids[0] if sw_ids else None)
        leaf = id_to_dev[leaf_id] if leaf_id else None
        servers = [d for d in full if d.get("device_type") == "server"]
        servers_used = len(servers)
        r["servers_used"] = servers_used
        if leaf is not None and servers_used > 0:
            downlink, _ = leaf_port_roles(leaf.get("model_name") or "",
                                          leaf.get("interface_count") or 54)
            downlink -= sum(1 for i in fabric.get(leaf_id, ()) if i < downlink)
            downlink = max(0, downlink)
            # "One more of what this rack already holds" is the unit of capacity — the
            # same thing the fleet does when it clones a rack's template. A rack with a
            # mixed fit-out has no single answer, so take its DOMINANT SKU (mode, ties
            # broken by name): deterministic, and representative of the rack.
            skus = Counter(d.get("model_name") or "" for d in servers)
            top = min(skus.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            tmpl = next(d for d in servers if (d.get("model_name") or "") == top)
            u_each = device_u_height("server", top) or SERVER_U_HEIGHT
            w_each = int(tmpl.get("power_draw_w") or 0) or 1
            u_used = sum(device_u_height(d.get("device_type"), d.get("model_name") or "")
                         for d in full if (d.get("rack_unit") or 0) > 0
                         and (d.get("rack_unit") or 0) <= LAST_SERVER_UNIT)
            u_free = max(0, (LAST_SERVER_UNIT - FIRST_SERVER_UNIT + 1) - u_used)
            w_free = max(0, RACK_POWER_BUDGET_W_DEFAULT - r["it_power_draw_w"])
            r["server_capacity"] = servers_used + min(downlink - servers_used,
                                                      u_free // u_each,
                                                      w_free // w_each)
            r["mlag_ready"] = bool(leaf.get("mlag_ready"))
            peer = leaf.get("mlag_peer_unit") or 0
            r["reserved_units"] = [peer] if peer else []
        else:
            r["server_capacity"] = None
            r["mlag_ready"] = False
            r["reserved_units"] = []

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