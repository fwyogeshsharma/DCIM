#!/usr/bin/env python3
"""Give network devices a dedicated out-of-band management port for their console.

Switches, routers, firewalls and load balancers had their OOB console edge
(management -> OOB) riding on a DATA interface. Physically a switch/router has a
dedicated management Ethernet port (Cisco Nexus `mgmt0`, Arista `Management1`,
Juniper `fxp0`, Palo Alto `management`, F5 `mgmt`) that is separate from the
numbered data switchports — that separation is the point of OOB, since the mgmt
plane has to survive a data-plane outage.

This appends that dedicated mgmt port (1 GbE, role=mgmt) to each affected device
and moves the console edge onto it, leaving the data port free:

    iface 0     GigabitEthernet0/0 -> LF1   (production, unchanged)
    iface N     mgmt0              -> OOB   (management, moved here)

WHAT COUNTS AS A CONSOLE (this is the part that is easy to get wrong):

A management-layer edge is NOT automatically a console. On the devices that BUILD
the OOB network — the OOB firewalls, the OOB routers, the OOB switches themselves —
management-layer links are that device's DATA plane: FWO1 -> OOBC1/OOBC2/OOBR1/OOBR2
is the OOB firewall's transit path, four separate cables on four separate ports.
Moving those onto one mgmt port would collapse four links onto one interface and
model something that cannot physically exist.

The two cases are told apart structurally, by whether the device carries production
traffic at all:

  has production edges  -> it lives on the data plane; a management edge to an OOB
                          switch is its console. FIXED HERE.
  no production edges   -> it IS management-plane infrastructure; its management
                          edges are its data plane. LEFT ALONE, reported.

Prefer this over matching device-name prefixes (OOBR/FWO/...): the wiring is the
thing that actually decides, and a rename must not silently change the answer.

Data-port COUNT is unchanged in spirit — the mgmt port is an ADDITIONAL, dedicated
interface (as on real gear), so interface_count rises by one. No positions change,
so no layout_canvas / floorplan re-export. Idempotent: a console already on a
role=mgmt port is skipped, and an existing mgmt port is reused rather than doubled.

Run tools/set_iface_roles.py first — this reads Interface.role.

Usage:
    python tools/add_network_mgmt_port.py topologies/dual_dc_enterprise.json
    python tools/add_network_mgmt_port.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

NET_TYPES = ("switch", "router", "firewall", "load_balancer")
MGMT = "mgmt"                            # Interface.role — see core/device_manager.py
SPEED_LABEL = {
    100_000_000: "Fast Ethernet (100 Mbps)", 1_000_000_000: "Gigabit Ethernet (1 Gbps)",
    10_000_000_000: "10 Gigabit Ethernet (10 Gbps)", 25_000_000_000: "25 Gigabit Ethernet (25 Gbps)",
    40_000_000_000: "40 Gigabit Ethernet (40 Gbps)", 100_000_000_000: "100 Gigabit Ethernet (100 Gbps)",
}


def mgmt_name(vendor: str, dtype: str) -> str:
    v = (vendor or "").lower()
    if dtype == "firewall":
        return "management"
    if dtype == "load_balancer":
        return "mgmt"
    if "arista" in v:
        return "Management1"
    if "juniper" in v:
        return "fxp0"
    return "mgmt0"                       # Cisco / Dell / generic switch + router


def _mac(sid: str, idx: int) -> str:
    b = bytearray(hashlib.md5(f"{sid}:mgmt:{idx}".encode()).digest()[:6])
    b[0] = (b[0] & 0xFE) | 0x02
    return ":".join(f"{x:02x}" for x in b)


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    typ = {n["id"]: n["device"]["device_type"] for n in nodes}

    done = skipped = 0
    dist = Counter()
    infra = Counter()
    for n in nodes:
        d = n["device"]
        if d["device_type"] not in NET_TYPES:
            continue
        sid = n["id"]
        ifaces = d.get("interfaces") or []

        # This device's production edges and its candidate console (management ->
        # OOB switch) edges, keyed by which of ITS ifaces they land on.
        has_production = False
        console_edges = []      # (edge, key) for management -> oob_switch
        for e in edges:
            if sid not in (e["src"], e["dst"]):
                continue
            key = "src_iface" if e["src"] == sid else "dst_iface"
            peer = e["dst"] if e["src"] == sid else e["src"]
            if e.get("layer") == "production":
                has_production = True
            elif e.get("layer") == "management" and typ.get(peer) == "oob_switch":
                console_edges.append((e, key))
        if not console_edges:
            skipped += 1
            continue
        # No production traffic => this device IS the management plane, and those
        # "management" edges are its transit, not a console. See the module docstring.
        if not has_production:
            infra[f"{d['device_type']}: {d['name']}"] += len(console_edges)
            skipped += 1
            continue
        # Act only on consoles sitting on a DATA port. role is the source of truth;
        # a console that already found its mgmt port is left alone (idempotent).
        misplaced = [(e, key) for e, key in console_edges
                     if ifaces[e[key]].get("role") != MGMT]
        if not misplaced:
            skipped += 1
            continue

        # Reuse this device's mgmt port if it already has one, else append a
        # dedicated one (1 GbE). Real gear has a SINGLE mgmt Ethernet, so redundant
        # console cabling shares it rather than earning a second port.
        name = mgmt_name(d.get("vendor"), d["device_type"])
        existing = next((i for i, itf in enumerate(ifaces)
                         if itf.get("role") == MGMT), None)
        if existing is not None:
            new_ix = existing
        else:
            new_ix = len(ifaces)
            ifaces.append({
                "index": new_ix + 1, "name": name, "speed": 1_000_000_000, "oper_status": 1,
                "in_octets": 0, "out_octets": 0, "in_errors": 0, "out_errors": 0,
                "in_discards": 0, "out_discards": 0, "mac_address": _mac(sid, new_ix),
                "connected_to_device": None, "connected_to_iface": None, "role": MGMT,
            })
        d["interface_count"] = len(ifaces)
        # Recompute groups from the interfaces (aggregate by type, first-seen order).
        groups: dict = {}
        for itf in ifaces:
            t = SPEED_LABEL.get(itf.get("speed", 1_000_000_000), "Gigabit Ethernet (1 Gbps)")
            groups[t] = groups.get(t, 0) + 1
        d["interface_groups"] = [{"iface_type": t, "count": c} for t, c in groups.items()]
        for e, key in misplaced:
            e[key] = new_ix

        done += 1
        dist[f"{d['device_type']} -> {name}"] += 1

    if not dry_run:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would move" if dry_run else "Moved"
    print(f"{verb} the console onto a dedicated mgmt port on {done} network device(s); "
          f"{skipped} skipped. {'(dry run)' if dry_run else f'Wrote {p}'}\n")
    for k, c in sorted(dist.items()):
        print(f"  {c:4d}  {k}")
    if infra:
        print(f"\n  Left alone — management-plane infrastructure, whose management "
              f"edges are its DATA plane, not a console:")
        for k, c in sorted(infra.items()):
            print(f"  {c:4d}  {k}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
