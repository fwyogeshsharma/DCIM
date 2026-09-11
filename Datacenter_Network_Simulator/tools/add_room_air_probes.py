"""Fit a room temperature/humidity transmitter to the rooms that hold no racks.

WHAT WAS WRONG
--------------
Every environmental probe in this estate hangs off a rack PDU's sensor port.
A switchroom and a generator hall hold no racks, therefore no PDU, therefore no
sensor port - so those rooms carried no thermometer at all. The DCIM's facility
table showed them with an empty temperature cell while the plant rooms beside
them read a switch chassis.

That is a gap in the MODEL rather than in the platform, and it is not a small
one. A battery room that drifts warm eats battery life - VRLA capacity and
service life halve for roughly each 10 K above the 25 C the cells are specified
at - and a generator hall that does is where a set fails to start on the one
night it is asked to. Both rooms are monitored on every real site.

WHAT THIS DOES
--------------
For each site, one two-channel transmitter in every room that holds no racks -
the UPS room, the generator hall, the central plant and the mechanical room:

  * on the BMS's RS-485 trunk, behind the same Modbus gateway the chilled-water
    thermowells already sit on. It owns no IP, exactly like them: a room sensor
    is a pair of wires back to a controller, not a network device;
  * it reports temperature AND humidity, because both matter in a battery room
    and because a transmitter that reported only one would be a different part;
  * its unit id continues the trunk's numbering.

The plant rooms are included even though something in them already reports a
temperature, because what they report is a SWITCH CHASSIS: a box that heats
itself, sitting a few degrees above the air around it. That is a floor under
the room temperature rather than a measurement of it.

The ROOF is deliberately excluded. It is outdoors, and the cooling towers on it
already carry the site's outdoor sensor - a tower is controlled to approach wet
bulb, so it has to. A "room air" transmitter there would be a second name for a
measurement the site already has.

Idempotent, and it REPAIRS: a transmitter that is in the file without a trunk
link or a place in its gateway's child list gets both. That failure is silent -
the instrument imports cleanly, polls nothing, and looks exactly like a probe
nobody has warmed up yet.

The Modbus points themselves render live state and need no .snmprec. But the
SNMP dataset fingerprint covers the whole topology, so adding devices does mean
regenerating the datasets before the SNMP plane will start - and the DCIM needs
an inventory re-import with `--protocols snmp,redfish,bacnet,modbus,gnmi`,
because its default of snmp,redfish creates no endpoint for a Modbus device.

Usage:
    python tools/add_room_air_probes.py topologies/dual_dc_enterprise.json
    python tools/add_room_air_probes.py <file> --dry-run
"""
from __future__ import annotations

import json
import sys
import uuid

#: The "Plant " prefix is what marks a device as a BMS field instrument rather
#: than a rack probe; the name prefix is what says which point it measures.
#: Both are read by core.device_state_store._probe_role.
PROBE_MODEL = "Plant Room Air T/RH"
PROBE_VENDOR = "Vertiv (Liebert)"
NAME_PREFIX = "THR"

#: Rooms that get one, and the suffix each carries in the estate's naming
#: scheme (CODE-DC-ROOM).
#:
#: The plant rooms are here even though something in them already reports a
#: temperature, because what they report is a SWITCH CHASSIS - a box that
#: heats itself, sitting a few degrees above the air around it. It is a floor
#: under the room temperature, not a measurement of it, and a chiller hall
#: drifting warm is exactly the thing that shows up first as a couple of
#: degrees on the air.
#:
#: The ROOF is deliberately absent. It is outdoors, and the cooling towers
#: standing on it already carry the site's outdoor sensor because a tower is
#: controlled to approach wet bulb. Fitting a "room air" transmitter there
#: would be a second name for a measurement the site already has.
ROOMS = {
    "UPS Room": "UR",
    "Generator Room": "GR",
    "Central Plant": "CP",
    "Mechanical Room": "MR",
}


def main(path: str, dry_run: bool = False) -> int:
    doc = json.load(open(path, encoding="utf-8"))
    nodes = doc["nodes"]
    taken = {n["id"] for n in nodes}

    gateways: dict[str, dict] = {}
    rooms: dict[tuple, list[dict]] = {}
    units: dict[str, set] = {}
    for n in nodes:
        d = n.get("device") or {}
        dc = d.get("datacenter")
        if d.get("device_type") == "modbus_gateway" and dc:
            gateways[dc] = d
        if d.get("modbus_gateway_ip"):
            units.setdefault(d["modbus_gateway_ip"], set()).add(
                int(d.get("modbus_unit_id") or 0))
        if dc and d.get("room"):
            rooms.setdefault((dc, d["room"]), []).append(d)

    added = 0
    for (dc, room), members in sorted(rooms.items()):
        suffix = ROOMS.get(str(room))
        if suffix is None:
            continue
        # Already has a ROOM AIR transmitter - not merely "has a sensor". A
        # plant room is full of thermowells, and a thermowell in a chilled-water
        # header has no opinion about the air in the room around it.
        if any(str(d.get("name", "")).startswith(NAME_PREFIX + "-")
               for d in members):
            continue
        gw = gateways.get(dc)
        if gw is None:
            print(f"  SKIP {dc} {room}: no Modbus gateway to hang a transmitter off")
            continue
        gw_ip = gw.get("mgmt_ip") or gw.get("ip_address")
        used = units.setdefault(gw_ip, set())
        unit_id = next(i for i in range(1, 248) if i not in used)
        used.add(unit_id)

        anchor = members[0]
        new_id = uuid.uuid4().hex[:8]
        while new_id in taken:
            new_id = uuid.uuid4().hex[:8]
        taken.add(new_id)

        # No index: the role is read off the name prefix alone
        # (device_state_store._probe_role_by_name splits on "-"), and there is
        # one of these per room anyway - the room is already in the name. Same
        # shape as the thermowells: CHWS-DC1-CP, not CHWS1-DC1-CP.
        name = f"{NAME_PREFIX}-{dc}-{suffix}"
        device = {
            "name": name,
            "device_type": "sensor",
            "vendor": PROBE_VENDOR,
            "ip_address": "",
            "snmp_port": 0,
            "gnmi_port": 57400,
            "snmp_community": "",
            "interface_count": 0,
            "interface_groups": [],
            "model_name": PROBE_MODEL,
            "metrics_enabled": False,
            "id": new_id,
            "interfaces": [],
            "mgmt_ip": "",
            "mgmt_vlan": anchor.get("mgmt_vlan", 10),
            # Loop-powered off the controller's own supply, like every other
            # transmitter on an RS-485 trunk: no cord, no outlet, and its draw
            # sits on the controller's budget rather than on its own record.
            "power_draw_w": 0,
            "ups_backup": "",
            "country": anchor.get("country"),
            "datacenter_city": anchor.get("datacenter_city"),
            "datacenter": dc,
            "room": room,
            "floor": anchor.get("floor"),
            "rack_row": anchor.get("rack_row"),
            "rack_num": anchor.get("rack_num"),
            "rack_unit": 0,
            "cpu_usage": 0,
            "memory_total": 0,
            "memory_used": 0,
            "disk_total": 0,
            "disk_used": 0,
            "sys_uptime": 0,
            "cpu_temp": 0.0,
            "inlet_temp": 24.0,
            "mid_temp": 0.0,
            "outlet_temp": 0.0,
            "humidity": 45.0,
            "dewpoint": 0.0,
            "airflow": 0.0,
            "sys_contact": "",
            "sys_location_override": "",
            "floor_x": anchor.get("floor_x"),
            "floor_y": anchor.get("floor_y"),
            "mounting": "wall",
            "outlets": [],
            "psus": [],
            "modbus_role": "rtu_slave",
            "modbus_unit_id": unit_id,
            "modbus_gateway_ip": gw_ip,
        }
        pos = dict((n for n in nodes if (n.get("device") or {}).get("name")
                    == anchor.get("name")).__next__().get("position")
                   or {"x": 0, "y": 0})
        pos["y"] = int(pos.get("y", 0)) + 60
        nodes.append({"id": new_id, "position": pos, "device": device})

        # The trunk's own record of what answers on it. A gateway republishes
        # its children BY NAME, so a transmitter missing from this list is a
        # device the master can address and the gateway will not answer for.
        #
        # APPENDED, never inserted. The first six children are the
        # ENTITY-SENSOR index order a poller template binds to, so putting a
        # transmitter among them would not add a reading — it would renumber
        # the chilled-water header and have CHWS answer as CHWR.
        gw.setdefault("modbus_children", []).append(name)
        # And the trunk itself. A portless device is reached THROUGH its
        # carrier, so without this edge the transmitter is an orphan: nothing
        # says which gateway answers for it, and a failure of that gateway is
        # not attributable to the instruments behind it.
        gw_node = next(n["id"] for n in nodes
                       if (n.get("device") or {}).get("name") == gw["name"])
        doc["edges"].append({"src": gw_node, "dst": new_id,
                             "src_iface": None, "dst_iface": None,
                             "layer": "fieldbus"})
        added += 1
        print(f"  + {name:<16} {room:<16} unit {unit_id} on {gw['name']} ({gw_ip})")

    # REPAIR. Every transmitter must sit on a trunk and be reachable through
    # its gateway, however it got into the file. Cheap to check, and what it
    # catches is silent: an instrument with no carrier edge imports cleanly,
    # polls nothing, and looks exactly like a probe nobody has warmed up yet.
    gws_by_ip = {(d.get("mgmt_ip") or d.get("ip_address")): (n["id"], d)
                 for n in nodes
                 for d in [n.get("device") or {}]
                 if d.get("device_type") == "modbus_gateway"}
    linked = {(e.get("src"), e.get("dst")) for e in doc["edges"]
              if e.get("layer") == "fieldbus"}
    repaired = 0
    for n in nodes:
        d = n.get("device") or {}
        if not str(d.get("name", "")).startswith(NAME_PREFIX + "-"):
            continue
        found = gws_by_ip.get(d.get("modbus_gateway_ip"))
        if found is None:
            print(f"  ORPHAN {d['name']}: no gateway at {d.get('modbus_gateway_ip')}")
            continue
        gw_node, gw_dev = found
        if (gw_node, n["id"]) not in linked and (n["id"], gw_node) not in linked:
            doc["edges"].append({"src": gw_node, "dst": n["id"],
                                 "src_iface": None, "dst_iface": None,
                                 "layer": "fieldbus"})
            repaired += 1
            print(f"  ~ {d['name']:<16} linked to its trunk")
        chain = gw_dev.setdefault("modbus_children", [])
        if d["name"] not in chain:
            chain.append(d["name"])
            repaired += 1
            print(f"  ~ {d['name']:<16} added to the trunk's child list")
        if d.get("power_draw_w"):
            d["power_draw_w"] = 0        # loop-powered: it has no cord
            repaired += 1

    print(f"\n{added} transmitter(s) fitted, {repaired} repair(s)")
    if dry_run:
        print("dry run: nothing written")
        return 0
    if added or repaired:
        json.dump(doc, open(path, "w", encoding="utf-8"), indent=2)
        print(f"wrote {path}")
        print("NEXT: regenerate the SNMP datasets (the fingerprint covers the "
              "whole topology, so the SNMP plane will refuse to start until you "
              "do), then re-import the DCIM's inventory with "
              "--protocols snmp,redfish,bacnet,modbus,gnmi.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], "--dry-run" in sys.argv))
