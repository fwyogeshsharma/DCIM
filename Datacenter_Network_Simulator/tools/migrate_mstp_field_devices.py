"""Move the chilled-water pumps and header valves onto a BACnet MS/TP trunk.

WHAT WAS WRONG
--------------
Every pump VFD and header valve carried its own management IP and its own
BACnet/IP stack. That is the minority product in both cases and the wrong shape
for a plant room. A Belimo "PR..A-BAC" actuator — the exact model this topology
carries — is a BACnet MS/TP device: two wires onto an RS-485 trunk, addressed by
MAC. Grundfos pumps take a CIM 300 MS/TP card in the same way. Only the router
that fronts the trunk holds an Ethernet port and an address.

WHAT THIS DOES
--------------
Per datacenter:
  * adds one BACNET_ROUTER in the Central Plant room, on the BMS management
    plane, taking over the LOWEST address the trunk is releasing;
  * strips IP / SNMP / Ethernet port from the pumps and valves in that room and
    gives them (mstp_net, mstp_mac) on that router;
  * records trunk membership on the router (mstp_children), in MAC order.

MACs are assigned from a fixed base per class so a pump and a valve can never
collide, and so re-running reproduces the same trunk rather than reshuffling
addresses that a client may already have bound.

WHAT STAYS THE SAME
-------------------
Everything about their telemetry. These devices keep their BACnet object trees
and their PlantTelemetryEngine — only their ADDRESSING changes, which is why the
cooling model, _plant_state_cache and the plant override channel are untouched.
The override channel was re-keyed from IP to device name first, precisely so a
device losing its address cannot silently fall out of fault injection.

Usage:
    python tools/migrate_mstp_field_devices.py topologies/dual_dc_enterprise.json
    python tools/migrate_mstp_field_devices.py <file> --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# MS/TP master MACs are 0..127. Separate bases per class keep a pump and a valve
# from ever landing on the same address, and leave room to grow either trunk.
PUMP_MAC_BASE = 1
VALVE_MAC_BASE = 20

# One trunk per datacenter, each its own BACnet network number.
NET_BY_DC = {"DC1": 2001, "DC2": 2002}

ROUTER_VENDOR = "Loytec"
ROUTER_MODEL = "LOYTEC LINX-151"


def migrate(path: Path, dry_run: bool = False) -> int:
    topo = json.loads(path.read_text(encoding="utf-8"))
    nodes = topo["nodes"]

    by_dc: dict = {}
    for n in nodes:
        d = n["device"]
        if d.get("device_type") not in ("pump", "valve"):
            continue
        if d.get("mstp_mac"):
            continue                      # already migrated
        by_dc.setdefault(d.get("datacenter") or "", []).append(n)

    if not by_dc:
        print("No un-migrated pumps or valves found — nothing to do.")
        return 0

    existing = {n["device"].get("name") for n in nodes}
    added, converted = [], []

    for dc, field_nodes in sorted(by_dc.items()):
        net = NET_BY_DC.get(dc, 2000 + len(NET_BY_DC) + 1)
        pumps = sorted((n for n in field_nodes if n["device"]["device_type"] == "pump"),
                       key=lambda n: n["device"]["name"])
        valves = sorted((n for n in field_nodes if n["device"]["device_type"] == "valve"),
                        key=lambda n: n["device"]["name"])

        released = sorted(
            (n["device"].get("mgmt_ip", "") for n in field_nodes if n["device"].get("mgmt_ip")),
            key=lambda ip: int(ip.split(".")[-1]))
        if not released:
            print(f"  {dc}: field devices already have no addresses — skipping")
            continue
        router_ip = released[0]
        router_name = f"BRTR1-{dc}-CP"
        if router_name in existing:
            print(f"  {router_name} already present — skipping {dc}")
            continue

        sample = field_nodes[0]["device"]
        assigned = []
        for base, group in ((PUMP_MAC_BASE, pumps), (VALVE_MAC_BASE, valves)):
            for i, n in enumerate(group):
                d = n["device"]
                mac = base + i
                converted.append((d["name"], d.get("mgmt_ip", ""), mac, d["device_type"]))
                assigned.append((mac, d["name"]))
                d["mgmt_ip"] = ""
                d["ip_address"] = ""
                d["snmp_port"] = 0
                d["snmp_community"] = ""
                d["metrics_enabled"] = False
                d["interfaces"] = []
                d["interface_count"] = 0
                d["interface_groups"] = []
                d["mstp_net"] = net
                d["mstp_mac"] = mac
                d["mstp_router_ip"] = router_ip

        octets = [int(o) for o in router_ip.split(".")]
        mac_addr = "02:%02x:%02x:%02x:%02x:%02x" % (
            octets[1], octets[2], octets[3], (octets[3] * 11) & 0xFF, 0x02)
        nodes.append({
            "id": f"brtr{dc.lower()}01",
            "position": {"x": sample.get("position", {}).get("x", 0) + 80
                         if isinstance(sample.get("position"), dict) else 0,
                         "y": sample.get("position", {}).get("y", 0) + 80
                         if isinstance(sample.get("position"), dict) else 0},
            "device": {
                # TopologyEngine keys graph nodes by device.id, NOT by the node id.
                # Omitting it makes Device.from_dict mint a random uuid, the node
                # lands under an id nothing references, and EVERY edge to this
                # device is silently dropped by add_link's node check.
                "id": f"brtr{dc.lower()}01",
                "name": router_name,
                "device_type": "bacnet_router",
                "vendor": ROUTER_VENDOR,
                "model_name": ROUTER_MODEL,
                "ip_address": "",
                "mgmt_ip": router_ip,
                "snmp_port": 0,
                "snmp_community": "",
                "metrics_enabled": False,
                "interface_count": 1,
                "interface_groups": [
                    {"iface_type": "Gigabit Ethernet (1 Gbps)", "count": 1}],
                "interfaces": [{
                    "index": 1, "name": "eth0", "speed": 1000000000,
                    "oper_status": 1, "in_octets": 0, "out_octets": 0,
                    "in_errors": 0, "out_errors": 0, "in_discards": 0,
                    "out_discards": 0, "mac_address": mac_addr,
                    "connected_to_device": None, "connected_to_iface": None,
                    "role": "mgmt",
                }],
                "mstp_net": net,
                "mstp_children": [nm for _m, nm in sorted(assigned)],
                "datacenter": sample.get("datacenter", ""),
                "datacenter_city": sample.get("datacenter_city", ""),
                "country": sample.get("country", ""),
                "room": sample.get("room", ""),
                "floor": sample.get("floor", ""),
                "rack_row": sample.get("rack_row", 0),
                "rack_num": sample.get("rack_num", 0),
                "power_draw_w": 12,
                "rated_power_w": 20,
            },
        })
        added.append((router_name, router_ip, net, len(assigned)))

    print(f"\nRouters added ({len(added)}):")
    for name, ip, net, n in added:
        print(f"  {name:16s} {ip:14s} network {net}  trunk of {n}")
    print(f"\nField devices moved onto MS/TP ({len(converted)}):")
    for name, old_ip, mac, dtype in converted:
        print(f"  {name:16s} {dtype:6s} MAC {mac:3d}   released {old_ip}")

    if dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    backup = path.with_suffix(path.suffix + ".premstp.bak")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nWrote {path}\nBackup {backup}")
    print(f"Released {len(converted)} management addresses "
          f"(kept {len(added)} for the routers).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("topology", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.topology.exists():
        print(f"No such file: {args.topology}", file=sys.stderr)
        return 2
    return migrate(args.topology, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
