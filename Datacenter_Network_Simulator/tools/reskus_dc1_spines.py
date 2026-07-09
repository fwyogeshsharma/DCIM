#!/usr/bin/env python3
"""Re-SKU DC1's spine switches from Cisco Nexus 93180YC-FX (a LEAF-class box, only
6×100G leaf-facing ports) to Cisco Nexus 9364C (64×100G) — a real high-radix spine
that can fan out to the hall's full ~39-leaf build-out.

The sim derives spine radix from interface_count (_spine_downlink_cap), and the
SNMP ifTable from the stored interfaces list, so both are updated: count -> 64 and
the interface list is extended to 64 ports. DC2 already uses a proper spine
(Dell Z9264F-ON, 64×100G) and is left untouched. Idempotent.

Usage:
    python tools/reskus_dc1_spines.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

MODEL = "Cisco Nexus 9364C"
VENDOR = "Cisco Systems"
PORTS = 64
IFTYPE = "Gigabit Ethernet (1 Gbps)"       # label used by the other switches
SPEED = 1_000_000_000


def is_dc1_spine(dv) -> bool:
    return (dv["datacenter"] == "DC1" and dv["device_type"] == "switch"
            and (dv.get("name") or "").split("-", 1)[0].upper().startswith("SP"))


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    n = 0
    for node in topo["nodes"]:
        dv = node["device"]
        if not is_dc1_spine(dv):
            continue
        dv["model_name"] = MODEL
        dv["vendor"] = VENDOR
        dv["interface_count"] = PORTS
        dv["interface_groups"] = [{"iface_type": IFTYPE, "count": PORTS}]
        ifaces = dv.get("interfaces") or []
        if len(ifaces) > PORTS:
            ifaces = ifaces[:PORTS]
        for i in range(len(ifaces), PORTS):      # extend to 64 ports
            ifaces.append({
                "index": i + 1,
                "name": f"GigabitEthernet0/{i}",
                "speed": SPEED,
                "oper_status": 1,
                "in_octets": 0, "out_octets": 0,
                "in_errors": 0, "out_errors": 0,
                "in_discards": 0, "out_discards": 0,
                "mac_address": ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6]),
                "connected_to_device": None, "connected_to_iface": None,
            })
        dv["interfaces"] = ifaces
        n += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Re-SKU'd {n} DC1 spines -> {MODEL} ({PORTS}×100G)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
