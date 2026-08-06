#!/usr/bin/env python3
"""Strip the phantom Ethernet port off passive RPP panels.

An RPP (remote power panel / branch panelboard) is passive gear: a main breaker and
12-42 branch breakers on a busbar feeding rack PDUs. There is no controller, no
monitoring card, and therefore no network port. The simulator already treats it that
way everywhere it matters -- core/snmprec_generator.py lists RPP in _NO_SNMP_TYPES, so
no SNMP dataset is written and nothing answers on its address -- but the authored
topology still gave each panel:

    interface_count: 1, interface_groups: [1 x 1GbE], interfaces: [eth0 role=mgmt],
    mgmt_vlan: 10, metrics_enabled: true

with an empty ip_address/mgmt_ip and eth0 cabled to nothing. A port that cannot be
polled, addressed, or patched -- it only made a breaker panel read as a pollable
network node in port counts and the device inspector.

Panels that DO ship branch-circuit monitoring (Schneider PowerLogic BCPM, Vertiv
PowerIT, Packet Power, Starline) are modelled as MPP in this topology, and those keep
their metering NIC on the OOBM plane. Only rpp is touched here.

Power edges are untouched: they already carry src_iface/dst_iface = None (power does
not land on an interface), so the UPS -> RPP -> PDU chain survives intact.

Addresses go with the port: a panel with no card cannot answer on an IP or an SNMP
port, and Device.__post_init__ now clears both at load time regardless of what the file
says. Clearing them here too keeps the saved topology and the runtime in agreement --
otherwise the file would keep claiming an address the app silently ignores.

Idempotent: a panel already stripped is left alone. Refuses to strip a panel that has
an Ethernet-layer edge -- a cable is a graph edge the runtime does NOT remove, so
cutting it is a topology change, not a field cleanup, and not this tool's call.

Usage:
    python tools/strip_rpp_phantom_port.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PASSIVE_TYPES = {"rpp"}
ETHERNET_LAYERS = {"production", "management"}
BAK_SUFFIX = ".prerppport.bak"


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]
    edges = topo.get("edges", [])

    passive = {n["id"]: n["device"] for n in nodes
               if (n["device"].get("device_type") or "") in PASSIVE_TYPES}
    if not passive:
        print(f"No passive panels ({'/'.join(sorted(PASSIVE_TYPES))}) in {p} — nothing to do")
        return 0

    # A panel someone cabled onto the network is out of scope: report it and leave it.
    wired = {e["src"] if e["src"] in passive else e["dst"]
             for e in edges
             if e.get("layer") in ETHERNET_LAYERS
             and (e["src"] in passive or e["dst"] in passive)}

    stripped, skipped, already = 0, 0, 0
    for nid, d in passive.items():
        name = d.get("name", nid)
        if nid in wired:
            print(f"SKIP {name}: has an Ethernet-layer link — strip the cable first")
            skipped += 1
            continue
        addr = d.get("ip_address") or d.get("mgmt_ip")
        if (not d.get("interfaces") and not d.get("interface_count")
                and not addr and not d.get("snmp_port")):
            already += 1
            continue
        if addr:
            print(f"NOTE {name}: dropping address {addr} — a panel with no card cannot "
                  f"answer on it, and the runtime clears it at load either way")
        d["interfaces"] = []
        d["interface_groups"] = []
        d["interface_count"] = 0
        d["mgmt_vlan"] = 0            # no port to tag
        d["metrics_enabled"] = False  # nothing to poll it with
        d["ip_address"] = ""
        d["mgmt_ip"] = ""
        d["snmp_community"] = ""
        d["snmp_port"] = 0            # not "the default port" — no port
        stripped += 1
        print(f"{name}: stripped eth0 — 0 ports, no address, passive panel")

    if stripped:
        shutil.copyfile(p, p.with_suffix(p.suffix + BAK_SUFFIX))
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")

    print(f"\nDone: stripped {stripped}, already passive {already}, skipped {skipped}. "
          f"{'Wrote ' + str(p) if stripped else 'No write needed'}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
