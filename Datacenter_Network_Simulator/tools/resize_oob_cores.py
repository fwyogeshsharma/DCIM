#!/usr/bin/env python3
"""Populate every OOB core switch (OOBC*) to its hardware's full port count.

WHY

    The OOB cores were seeded at 24 ports — half of what the modelled hardware
    actually carries (Cisco Catalyst 9300-48T, Dell N3248TE-ON: both 48x1G). Each
    hall access OOB dual-homes into BOTH cores, so a 24-port core capped the whole
    datacenter's management aggregation at ~18 access OOBs (~830 managed endpoints)
    — well under a build-out DC of ~1560 servers, whose BMCs + PDUs + switch
    consoles need ~1800 management ports. The core, not the access tier, was the
    bottleneck.

    These are 48-port switches. Using all 48 ports (no model change, just the real
    port count) lifts the per-core cap to 48, so the management plane matches the
    DC's compute build-out. core/fleet_lifecycle reads interface_count for the
    core-downlink cap, so this is all that is needed — the fleet's OOB-core gate
    then measures against 48.

This does NOT touch the OOB WAN routers (OOBR*), the BMS OOB (OOBM*), or the hall
access OOBs (OOB*) — only the cores (OOBC*).

Idempotent: a core already at >= TARGET_PORTS is left alone.
Canvas layout is unaffected (port count is not a coordinate) — no re-layout needed.

Usage:
    python tools/resize_oob_cores.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

TARGET_PORTS = 48


def set_ports(dev: dict, count: int) -> None:
    """Resize the switch to *count* ports, preserving the first interface's shape.
    (Same rule as tools/add_second_oob_core.set_ports.)"""
    tmpl = (dev.get("interfaces") or [{}])[0]
    ifaces = []
    for i in range(1, count + 1):
        f = copy.deepcopy(tmpl)
        f["index"] = i
        f["name"] = f"GigabitEthernet0/{i - 1}"
        f["oper_status"] = 1
        f["in_octets"] = f["out_octets"] = 0
        f["in_errors"] = f["out_errors"] = 0
        f["in_discards"] = f["out_discards"] = 0
        f["mac_address"] = ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6])
        f["connected_to_device"] = None
        f["connected_to_iface"] = None
        ifaces.append(f)
    dev["interfaces"] = ifaces
    dev["interface_count"] = count
    dev["interface_groups"] = [{"iface_type": "Gigabit Ethernet (1 Gbps)", "count": count}]


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    resized = 0
    for n in topo["nodes"]:
        d = n["device"]
        if not (d.get("name") or "").split("-", 1)[0].upper().startswith("OOBC"):
            continue
        if int(d.get("interface_count") or 0) >= TARGET_PORTS:
            print(f"{d['name']}: already >= {TARGET_PORTS} ports — skipped")
            continue
        was = d.get("interface_count")
        set_ports(d, TARGET_PORTS)
        resized += 1
        print(f"{d['name']}: {was} -> {TARGET_PORTS} ports ({d.get('model_name')})")

    if resized:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
        print(f"\nResized {resized} OOB core(s). Wrote {p}")
    else:
        print("\nNo OOB cores needed resizing.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
