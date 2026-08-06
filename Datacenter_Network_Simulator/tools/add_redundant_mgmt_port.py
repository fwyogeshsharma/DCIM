#!/usr/bin/env python3
"""Give PDUs and critical power gear a second, redundant management port.

Managed rack PDUs (Raritan PX3 ETH1/ETH2, Server Technology PRO2 Link, Vertiv
Geist) and critical power devices (UPS with dual NMC slots, ATS, switchgear) ship
with TWO network management interfaces — for a redundant network path or to
daisy-chain/cascade several units on one drop. The topology modelled a single
port each. This adds the second port (1 GbE, standby/uncabled) so the port COUNT
matches real hardware, leaving the primary port's existing OOB/BMS edge untouched.

The second port is left UNCONNECTED — a redundant NMC that is present but not yet
cabled (or the downstream end of a cascade). No positions change; no re-export.

Scope: pdu, floor_pdu, ups, ats, switchgear. NOT generator (genset controllers are
single-Ethernet) or MCC (mechanical, not critical power). Idempotent: a device that
already has >=2 ports is skipped.

Usage:
    python tools/add_redundant_mgmt_port.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

TARGET = {"pdu", "floor_pdu", "ups", "ats", "switchgear"}
SPEED_LABEL = {
    100_000_000: "Fast Ethernet (100 Mbps)", 1_000_000_000: "Gigabit Ethernet (1 Gbps)",
    10_000_000_000: "10 Gigabit Ethernet (10 Gbps)", 25_000_000_000: "25 Gigabit Ethernet (25 Gbps)",
}


def _mac(sid: str, idx: int) -> str:
    b = bytearray(hashlib.md5(f"{sid}:redmgmt:{idx}".encode()).digest()[:6])
    b[0] = (b[0] & 0xFE) | 0x02
    return ":".join(f"{x:02x}" for x in b)


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    done = skipped = 0
    dist = Counter()
    for n in nodes:
        d = n["device"]
        if d["device_type"] not in TARGET:
            continue
        ifaces = d.get("interfaces") or []
        if len(ifaces) >= 2:
            skipped += 1
            continue
        sid = n["id"]
        spd = ifaces[0]["speed"] if ifaces else 1_000_000_000
        new_ix = len(ifaces)
        ifaces.append({
            "index": new_ix + 1, "name": "eth1", "speed": spd, "oper_status": 1,
            "in_octets": 0, "out_octets": 0, "in_errors": 0, "out_errors": 0,
            "in_discards": 0, "out_discards": 0, "mac_address": _mac(sid, new_ix),
            "connected_to_device": None, "connected_to_iface": None,
        })
        d["interfaces"] = ifaces
        d["interface_count"] = len(ifaces)
        groups: dict = {}
        for itf in ifaces:
            t = SPEED_LABEL.get(itf.get("speed", 1_000_000_000), "Gigabit Ethernet (1 Gbps)")
            groups[t] = groups.get(t, 0) + 1
        d["interface_groups"] = [{"iface_type": t, "count": c} for t, c in groups.items()]
        done += 1
        dist[d["device_type"]] += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Added a redundant mgmt port to {done} device(s); {skipped} skipped "
          f"(already >=2 ports). Wrote {p}\n")
    for k, c in sorted(dist.items()):
        print(f"  {c:4d}  {k}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
