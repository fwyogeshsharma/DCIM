#!/usr/bin/env python3
"""Reassign in-rack PDU mgmt IPs into the CORRECT per-DC /23.

The PDU relocation drew mgmt IPs from a single 192.168.0/1 pool, but the OOB
mgmt network is per-DC: DC1 = 192.168.0.0/23, DC2 = 192.168.4.0/23. This puts
each PDU's mgmt_ip / snmp_community back into its own DC's /23.
"""
import json, sys
from collections import Counter

TOPO = "topologies/dual_dc_enterprise.json"
# per-DC /23 = two consecutive /24s, derived from each DC's dominant mgmt /24
DC_BASE = {"DC1": [0, 1], "DC2": [4, 5]}


def main():
    topo = json.load(open(TOPO, encoding="utf-8"))
    nodes = [n["device"] for n in topo["nodes"]]

    # used mgmt IPs from NON-PDU devices (fixed reference) -> never reuse
    used = set()
    for d in nodes:
        if d["device_type"] == "pdu":
            continue
        for k in ("mgmt_ip", "snmp_community", "ip_address"):
            v = d.get(k) or ""
            if v.startswith("192.168."):
                used.add(v)

    # HISTORICAL — same reason as tools/relocate_pdus_inrack.py. The 192.168
    # management plane was retired by tools/renumber_mgmt_plane.py because it
    # overlapped the host's own LAN. These pools are hard-coded, so re-running this
    # against a renumbered topology would put PDUs back on colliding addresses.
    if not used:
        raise SystemExit(
            "No 192.168 addresses in this topology — it has been renumbered (see\n"
            "tools/renumber_mgmt_plane.py). This one-shot migration hard-codes\n"
            "192.168 pools and would reintroduce the host-LAN collision."
        )
    pools = {dc: iter([f"192.168.{b}.{h}" for b in bases for h in range(2, 255)
                       if f"192.168.{b}.{h}" not in used])
             for dc, bases in DC_BASE.items()}

    fixed = 0
    for d in nodes:
        if d["device_type"] != "pdu":
            continue
        dc = d["datacenter"]
        if dc not in pools:
            continue
        ip = next(pools[dc])
        d["mgmt_ip"] = ip
        d["snmp_community"] = ip
        used.add(ip)
        fixed += 1

    json.dump(topo, open(TOPO, "w", encoding="utf-8"), indent=2)
    after = Counter((d["datacenter"], '.'.join((d.get("mgmt_ip") or "").split('.')[:3]))
                    for d in nodes if d["device_type"] == "pdu")
    print(f"Reassigned {fixed} PDU mgmt IPs per-DC")
    for k, c in sorted(after.items()):
        print(f"  {k[0]} -> {k[1]}.x : {c}")


if __name__ == "__main__":
    sys.exit(main())
