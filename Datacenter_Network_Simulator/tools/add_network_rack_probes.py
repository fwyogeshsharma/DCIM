"""Fit an environment probe to the racks that hold only network gear.

WHAT WAS WRONG
--------------
Every rack probe in the estate sits on a COMPUTE rack. The spine racks and the
management rack in each hall hold switches and PDUs, no servers, and carried no
probe - so the platform had no intake reading for them at all. They appeared on
the thermal page as a dash: no temperature, no in-band share, invisible to every
column, in rooms where the switches themselves were reporting 27 C.

That is a gap in the MODEL, not in the platform. Network racks are routinely
instrumented in real halls, and often first: top-of-rack is one of the hottest
places on a floor, a spine rack draws steadily whatever the compute load does,
and losing a spine costs a fabric rather than a workload.

WHAT THIS DOES
--------------
For each hall, every rack that holds network gear and no servers gets one
temperature-and-humidity probe on its A-feed strip, at slot 1:

  * the part is a Raritan DPX2-T1H1, because those racks carry Raritan PX2
    strips - an APC AP9335 does not plug into a PX2 sensor port, and the estate
    already keeps APC parts on APC strips;
  * it is a carried device: no IP, no agent, read through the strip's own
    external-sensor table at its slot, exactly like the compute-rack probes;
  * it is mounted at the front, low, which is where an intake probe goes.

Idempotent: a rack that already has a probe is left alone.

REGENERATE THE SNMP DATASETS afterwards. A probe that exists in the topology but
not in the strip's .snmprec is a probe nothing can read.

Usage:
    python tools/add_network_rack_probes.py topologies/dual_dc_enterprise.json
    python tools/add_network_rack_probes.py <file> --dry-run
"""
from __future__ import annotations

import json
import sys
import uuid

PROBE_MODEL = "Raritan DPX2-T1H1"
PROBE_VENDOR = "Raritan"
#: Front of the rack, low. An intake probe measures the air the rack is about
#: to breathe, so it belongs at the face; low because that is where the cold
#: aisle is coldest and where a starved rack shows first.
PROBE_UNIT = 6

NETWORK_TYPES = {"switch", "router", "firewall", "load_balancer", "oob_switch"}


def rack_key(d: dict) -> tuple:
    return (d.get("datacenter"), d.get("room"), d.get("rack_row"), d.get("rack_num"))


def main(path: str, dry_run: bool = False) -> int:
    doc = json.load(open(path, encoding="utf-8"))
    nodes = doc["nodes"]
    taken_ids = {n["id"] for n in nodes}

    racks: dict[tuple, list[dict]] = {}
    for n in nodes:
        d = n.get("device") or {}
        if d.get("rack_row") is None or d.get("rack_num") is None:
            continue
        racks.setdefault(rack_key(d), []).append(n)

    added = 0
    for key, members in sorted(racks.items(), key=lambda kv: [str(x) for x in kv[0]]):
        dc, room, row, num = key
        if not room or "Hall" not in str(room):
            continue                      # white space only; plant is not a rack
        types = {(n["device"] or {}).get("device_type") for n in members}
        if "server" in types:
            continue                      # compute racks are already covered
        if not (types & NETWORK_TYPES):
            continue                      # nothing here that breathes rack air
        if "sensor" in types:
            continue                      # already instrumented

        hosts = [n["device"] for n in members
                 if (n["device"] or {}).get("device_type") == "pdu"
                 and str((n["device"] or {}).get("name", "")).startswith("PDUA")]
        if not hosts:
            print(f"  SKIP {dc} {room} R{row}-{num:02d}: no A-feed strip to host a probe")
            continue
        host = hosts[0]
        anchor = members[0]["device"]

        new_id = uuid.uuid4().hex[:8]
        while new_id in taken_ids:
            new_id = uuid.uuid4().hex[:8]
        taken_ids.add(new_id)

        name = f"SEN1-{dc}-{str(room).replace('Server Hall ', 'H')}-R{row}-{num:02d}"
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
            "mgmt_vlan": host.get("mgmt_vlan", 10),
            # A DPX2 is a lead with a thermistor on the end. It is powered by
            # the strip's sensor port, so it has no cord and no draw.
            "power_draw_w": 0,
            "ups_backup": "",
            "country": anchor.get("country"),
            "datacenter_city": anchor.get("datacenter_city"),
            "datacenter": dc,
            "room": room,
            "floor": anchor.get("floor"),
            "rack_row": row,
            "rack_num": num,
            "rack_unit": PROBE_UNIT,
            "cpu_usage": 0,
            "memory_total": 0,
            "memory_used": 0,
            "disk_total": 0,
            "disk_used": 0,
            "sys_uptime": 0,
            "cpu_temp": 0.0,
            "inlet_temp": 22.0,
            "mid_temp": 0.0,
            "outlet_temp": 0.0,
            "humidity": 45.0,
            "dewpoint": 0.0,
            "airflow": 0.0,
            "sys_contact": "",
            "sys_location_override": "",
            "floor_x": anchor.get("floor_x"),
            "floor_y": anchor.get("floor_y"),
            "rack_facing": anchor.get("rack_facing"),
            "cold_aisle": anchor.get("cold_aisle"),
            "mounting": "rack_front",
            "hot_aisle": anchor.get("hot_aisle"),
            "outlets": [],
            "psus": [],
            "host_pdu_ip": host.get("mgmt_ip") or host.get("ip_address"),
            "sensor_slot": 1,
        }
        pos = dict(members[0].get("position") or {"x": 0, "y": 0})
        pos["y"] = int(pos.get("y", 0)) + 40
        nodes.append({"id": new_id, "position": pos, "device": device})
        # The lead from the strip's SENSOR port to the probe. It is the only
        # link a carried device has, and it is what says which agent answers
        # for it: no edge, no carrier, and the probe becomes an orphan that
        # nothing polls and nothing can raise a trap for.
        host_node = next(n["id"] for n in members
                         if (n["device"] or {}).get("name") == host["name"])
        doc["edges"].append({"src": host_node, "dst": new_id,
                             "src_iface": None, "dst_iface": None,
                             "layer": "fieldbus"})
        # And the strip's own record of what is plugged into its sensor port.
        # This is what the dataset generator walks to decide whether to publish
        # an external-sensor table at all: a strip with no children publishes
        # nothing, which is what a real strip with an empty port does. Fitting
        # a probe and not recording it here produced a probe that existed in
        # inventory, imported cleanly, polled its strip successfully - and read
        # empty forever, because the strip was never asked about it.
        host.setdefault("sensor_children", []).append(name)
        added += 1
        print(f"  + {name:<22} on {host['name']} slot 1  ({PROBE_MODEL})")

    # REPAIR. Any probe that names a host strip must appear in that strip's
    # chain, however it got there. Cheap to check, and the failure it catches
    # is silent: a probe that reads empty forever looks exactly like a probe
    # nobody has warmed up yet.
    by_ip = {}
    for n in nodes:
        d = n.get("device") or {}
        if d.get("device_type") == "pdu":
            for ip in (d.get("mgmt_ip"), d.get("ip_address")):
                if ip:
                    by_ip[ip] = d
    repaired = 0
    for n in nodes:
        d = n.get("device") or {}
        host_ip = d.get("host_pdu_ip")
        if not host_ip:
            continue
        strip = by_ip.get(host_ip)
        if strip is None:
            print("  ORPHAN " + d["name"] + ": no strip at " + str(host_ip))
            continue
        chain = strip.setdefault("sensor_children", [])
        if d["name"] not in chain:
            chain.append(d["name"])
            repaired += 1
            print("  ~ " + d["name"].ljust(22) + " added to "
                  + strip["name"] + "'s sensor chain")

    print(f"\n{added} probe(s) fitted, {repaired} chain entr(ies) repaired")
    if dry_run:
        print("dry run: nothing written")
        return 0
    if added or repaired:
        json.dump(doc, open(path, "w", encoding="utf-8"), indent=2)
        print(f"wrote {path}")
        print("REGENERATE THE SNMP DATASETS: a probe the strip's .snmprec does "
              "not carry is a probe nothing can read.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], "--dry-run" in sys.argv))
