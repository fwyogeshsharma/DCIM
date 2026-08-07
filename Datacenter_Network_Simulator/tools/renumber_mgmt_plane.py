#!/usr/bin/env python3
"""Renumber the estate onto a structured, collision-free address plan.

WHY
---
The management plane was generated from `mgmt_subnet_base="192.168"`, giving DC1
192.168.0.0/22 and DC2 192.168.4.0/22. DC1's block spans 192.168.0.0-192.168.3.255,
which CONTAINS 192.168.1.0/24 — an extremely common home and small-office LAN, and
the LAN of the machine this simulator runs on. 47 simulated devices therefore claim
addresses that belong to real hosts, and one of them claims 192.168.1.1, the default
gateway. Those addresses are really bound on the adapter, so the host answers on them:

    ip route get 192.168.1.1  ->  local ... dev lo

Egress still works only because eth1's more specific /24 outranks dcim0's /23. That is
a routing-metric accident, not a design. RFC 1918 gives 192.168.0.0/16 to exactly the
kind of small network a workstation sits on, so ANY simulator using it will eventually
collide with its host. 10.0.0.0/8 is the block with room to be unambiguous.

THE PLAN
--------
Second octet is the network PLANE. Third octet is site and room. Host starts at .10,
leaving .1-.9 for the gateway and infrastructure the way a real subnet is laid out.

    10.50.<site><room>.x   production      (servers, switches — the data plane)
    10.51.<site><room>.x   IT out-of-band  (BMCs, switch management)
    10.52.<site><room>.x   BMS / facility  (PDU, CRAH, UPS, chiller, sensors)

    site: 1 = DC1, 2 = DC2          room: 1 = Server Hall A   3 = Network Room
                                          2 = Server Hall B   4 = plant rooms

So 10.51.12.34 reads as "DC1, Server Hall B, IT out-of-band". An address states where
the thing is and which network it is on, which is the entire point of a numbering plan
and is not true of the flat pools this replaces.

WHY IT AND BMS ARE SEPARATE SECOND OCTETS, NOT JUST SEPARATE SUBNETS
They are two different networks in a real datacenter, run by different people: IT
operations owns the BMCs and switch management, facilities or the BMS integrator owns
the PDUs, CRAHs and chiller plant. Different protocols (Redfish/IPMI/SSH vs
BACnet/Modbus), different change control, and the BMS is normally firewalled hard
because it is the network that gets a building owned. Giving them adjacent /16s makes
"block everything from 10.52 to 10.51" a one-line firewall rule instead of an
enumeration of subnets.

An in-rack CDU counts as FACILITY here. It is a coolant distribution unit — mechanical
plant that a BMS monitors — not IT equipment, whatever rack it happens to sit in.

WHAT THIS DOES NOT DO
Datasets are not touched. They are named by IP and their content is fingerprinted
against the topology (core/dataset_fingerprint.compute hashes ip_address and mgmt_ip),
so changing addresses invalidates the fingerprint, the generators rebuild under the new
names, and reap_orphans removes the old ones. Rebinding and the openDCIM repair are
also separate steps — see the runbook this prints at the end.

USAGE
    python tools/renumber_mgmt_plane.py --dry-run          # plan only, writes nothing
    python tools/renumber_mgmt_plane.py --apply            # rewrite, after a backup
"""
from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import os
import shutil
import sys
from collections import Counter, defaultdict

TOPOLOGY = "topologies/dual_dc_enterprise.json"

PLANE_OCTET = {"prod": 50, "it": 51, "bms": 52}

# Room -> third-octet room digit. Everything that is not a hall or the network room is
# a plant room: they hold a handful of devices each and splitting them further would
# produce five subnets of four hosts, which no one would thank you for.
ROOM_DIGIT = {
    "Server Hall A": 1,
    "Server Hall B": 2,
    "Network Room": 3,
    "Central Plant": 4,
    "Mechanical Room": 4,
    "UPS Room": 4,
    "Generator Room": 4,
    "Roof": 4,
}
PLANT_DIGIT = 4

SITE_DIGIT = {"DC1": 1, "DC2": 2}

# Which plane a device's MANAGEMENT address belongs on.
#
# oob_switch stays on the IT plane even for the OOBM-* units that carry BMS traffic:
# it is a switch, IT operations configures it, and its management address belongs with
# the other switch management addresses. What rides ACROSS it is a separate question
# from who administers it.
BMS_TYPES = {
    "pdu", "sensor", "crah", "energy_monitor", "pump", "valve", "chiller",
    "cooling_tower", "ups", "rpp", "mpp", "mcc", "ats", "generator",
    "switchgear", "utility_feed", "floor_pdu", "cdu",
}

HOST_START = 10          # .1-.9 reserved: gateway, HSRP/VRRP, future infrastructure


def plane_for(device_type: str) -> str:
    return "bms" if device_type in BMS_TYPES else "it"


def subnet_for(plane: str, dc: str, room: str) -> str:
    site = SITE_DIGIT.get(dc)
    if site is None:
        raise ValueError(f"unknown datacenter {dc!r} — no site digit assigned")
    return f"10.{PLANE_OCTET[plane]}.{site}{ROOM_DIGIT.get(room, PLANT_DIGIT)}"


def build_map(devices: list[dict]) -> tuple[dict, dict, list[str]]:
    """(old mgmt -> new, old prod -> new, warnings). Deterministic: sorted by name."""
    mgmt_map: dict[str, str] = {}
    prod_map: dict[str, str] = {}
    warnings: list[str] = []
    cursor: dict[str, int] = defaultdict(lambda: HOST_START)

    def allocate(subnet: str) -> str:
        n = cursor[subnet]
        if n > 254:
            raise RuntimeError(f"{subnet}.0/24 exhausted — the room outgrew a /24")
        cursor[subnet] = n + 1
        return f"{subnet}.{n}"

    for d in sorted(devices, key=lambda x: x.get("name", "")):
        dc = d.get("datacenter") or ""
        room = d.get("room") or ""
        if not dc:
            warnings.append(f"{d.get('name')}: no datacenter — left unchanged")
            continue
        if room not in ROOM_DIGIT:
            warnings.append(f"{d.get('name')}: room {room!r} not in the plan "
                            f"— placed in the plant block")

        old_mgmt = d.get("mgmt_ip")
        if old_mgmt and old_mgmt not in mgmt_map:
            mgmt_map[old_mgmt] = allocate(subnet_for(plane_for(d.get("device_type", "")),
                                                     dc, room))
        old_prod = d.get("ip_address")
        if old_prod and old_prod not in prod_map:
            prod_map[old_prod] = allocate(subnet_for("prod", dc, room))

    return mgmt_map, prod_map, warnings


def rewrite(topo: dict, mgmt_map: dict, prod_map: dict) -> Counter:
    """Apply the maps in place. snmp_community tracks mgmt_ip — see below."""
    counts: Counter = Counter()
    for node in topo["nodes"]:
        d = node.get("device") or {}
        old_mgmt = d.get("mgmt_ip")
        if old_mgmt and old_mgmt in mgmt_map:
            d["mgmt_ip"] = mgmt_map[old_mgmt]
            counts["mgmt_ip"] += 1
            # The community string IS the device IP: one snmpsim process serves the
            # whole estate on a single wildcard endpoint, so it cannot tell devices
            # apart by destination address and resolves the .snmprec by community
            # instead (simulator/snmpsim_controller.py, _build_command). Leaving the
            # old value here would point every agent at a dataset that no longer
            # exists, and the request would be dropped with no response at all.
            #
            # A community that does not equal the device's OWN mgmt_ip is a bug, not
            # a variation to preserve. Six devices carried a NEIGHBOUR's address here
            # (EV21-DC1-NR-R2-01 held EV21-DC1-HA-R1-04's), which resolves to that
            # neighbour's dataset — so two different energy meters answered with byte
            # for byte identical readings and neither was wrong-looking enough to
            # notice. Point every device at its own address.
            if d.get("snmp_community") != old_mgmt and d.get("snmp_community"):
                counts["snmp_community_repaired"] += 1
            d["snmp_community"] = mgmt_map[old_mgmt]
            counts["snmp_community"] += 1
        old_prod = d.get("ip_address")
        if old_prod and old_prod in prod_map:
            d["ip_address"] = prod_map[old_prod]
            counts["ip_address"] += 1
    return counts


def audit(mgmt_map: dict, prod_map: dict) -> list[str]:
    """Refuse to write a plan that is not strictly better than what it replaces."""
    problems = []
    new = list(mgmt_map.values()) + list(prod_map.values())
    dupes = [ip for ip, n in Counter(new).items() if n > 1]
    if dupes:
        problems.append(f"COLLISION: {len(dupes)} address(es) assigned twice: {dupes[:5]}")
    for ip in new:
        addr = ipaddress.ip_address(ip)
        if not addr.is_private:
            problems.append(f"{ip} is not RFC1918")
        if ip.endswith(".0") or ip.endswith(".255"):
            problems.append(f"{ip} is a network/broadcast address")
    leftover = [ip for ip in new if ip.startswith("192.168.")]
    if leftover:
        problems.append(f"{len(leftover)} address(es) still in 192.168/16")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topology", default=TOPOLOGY)
    ap.add_argument("--apply", action="store_true", help="write the file (default: plan only)")
    ap.add_argument("--dry-run", action="store_true", help="explicit no-op, the default")
    ap.add_argument("--map-out", default="renumber_map.csv")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = args.topology if os.path.isabs(args.topology) else os.path.join(root, args.topology)
    with open(path, encoding="utf-8") as fh:
        topo = json.load(fh)

    devices = [n["device"] for n in topo["nodes"] if n.get("device")]
    mgmt_map, prod_map, warnings = build_map(devices)

    print(f"topology: {path}")
    print(f"  devices          : {len(devices)}")
    print(f"  mgmt addresses   : {len(mgmt_map)}")
    print(f"  prod addresses   : {len(prod_map)}")

    per_subnet: Counter = Counter()
    for ip in list(mgmt_map.values()) + list(prod_map.values()):
        per_subnet[".".join(ip.split(".")[:3]) + ".0/24"] += 1
    print("\n  new subnets:")
    plane_name = {"50": "production", "51": "IT out-of-band", "52": "BMS / facility"}
    for sn in sorted(per_subnet, key=lambda s: [int(x) for x in s.split("/")[0].split(".")]):
        octets = sn.split(".")
        print(f"    {sn:<18} {per_subnet[sn]:>4} hosts   "
              f"{plane_name.get(octets[1], '?')}, DC{octets[2][0]} room {octets[2][1]}")

    problems = audit(mgmt_map, prod_map)
    if problems:
        print("\n  REFUSING TO WRITE:")
        for p in problems:
            print(f"    ! {p}")
        return 2
    print("\n  audit: no duplicates, all RFC1918, nothing left in 192.168/16")

    if warnings:
        print(f"\n  warnings ({len(warnings)}):")
        for w in warnings[:10]:
            print(f"    ~ {w}")

    if not args.apply:
        print("\nplan only — nothing written. Re-run with --apply.")
        return 0

    backup = path + ".before-renumber"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
        print(f"\n  backup -> {backup}")
    else:
        print(f"\n  backup already exists, keeping it -> {backup}")

    counts = rewrite(topo, mgmt_map, prod_map)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(topo, fh, indent=2)
    print(f"  rewrote {path}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    map_path = os.path.join(root, args.map_out)
    with open(map_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["plane", "old_ip", "new_ip"])
        for old, new in sorted(mgmt_map.items()):
            w.writerow(["mgmt", old, new])
        for old, new in sorted(prod_map.items()):
            w.writerow(["prod", old, new])
    print(f"  wrote {map_path} ({len(mgmt_map) + len(prod_map)} rows)")

    print("""
NEXT, IN THIS ORDER — the addresses are live in three other places:
  1. Unbind the old addresses in the Binding panel BEFORE restarting. They are
     aliases on the adapter; nothing removes them just because a file changed, and
     the stale 192.168.1.1 alias keeps shadowing the real gateway until it goes.
  2. Restart the app. The dataset fingerprint covers ip_address and mgmt_ip, so it
     will not match, the SNMP/gNMI datasets rebuild under the new names, and
     reap_orphans deletes the old IP-named files.
  3. Bind again.
  4. python tools/export_to_opendcim.py --dcim-url ... --dcim-user ... --dcim-pass ...
     to repair PrimaryIP and SNMPCommunity on the openDCIM records.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
