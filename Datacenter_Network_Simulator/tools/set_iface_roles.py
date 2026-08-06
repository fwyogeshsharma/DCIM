#!/usr/bin/env python3
"""Backfill Interface.role ("data" | "mgmt") onto an existing topology's ports.

Roles were added to the model after this topology was built, so every port in the
JSON reads as the default "data" — including the dedicated mgmt/BMC ports that
earlier passes (add_network_mgmt_port.py, add_redundant_mgmt_port.py) appended.
This is the ONE-SHOT migration that tags them.

Name-matching is used here and ONLY here. It is safe as a migration (a fixed set
of files, inspected once) and wrong as runtime logic: vendor port conventions
differ, fleet clones and future SKUs would drift, and a renamed port would
silently change role. After this runs, role is the source of truth and nothing
should sniff names again.

Rules, by what the device physically IS:

  facility gear   ALL ports mgmt. A PDU/UPS/CRAH/chiller/sensor has no data
                  plane at all — its only Ethernet is the monitoring card
                  (SNMP/Modbus/BACnet). Nothing production ever lands there.
  server          BMC NIC (iDRAC/iLO/XCC/IPMI/IMM) mgmt; eth1/N data.
  switch/router/  dedicated mgmt Ethernet (mgmt0/Management1/fxp0/management/
  firewall/lb     mgmt) mgmt; numbered front-panel ports data.
  oob_switch      ALL ports data — deliberately. An OOB switch's switchports ARE
                  its data plane; the mgmt VLAN is the traffic it carries, not a
                  property of its ports. Its management-layer edges landing on
                  data ports is CORRECT, not a defect to migrate away.

Power and cooling edges are ignored: they record iface 0 as a placeholder but a
power link is an outlet-to-inlet cord and a cooling link is a pipe — neither
occupies an Ethernet port, so neither says anything about a port's role.

Idempotent — re-running rewrites the same roles. Reports any port whose role
disagrees with the edges landing on it, which is how the remaining mgmt-on-data
consoles surface.

Usage:
    python tools/set_iface_roles.py topologies/dual_dc_enterprise.json
    python tools/set_iface_roles.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

DATA = "data"
MGMT = "mgmt"

# Facility / electrical / mechanical gear: the only NIC is the monitoring card.
FACILITY_TYPES = {
    "pdu", "ups", "rpp", "ats", "mcc", "mpp", "switchgear", "utility_feed",
    "generator", "energy_monitor", "sensor", "crah", "chiller", "cooling_tower",
    "pump", "valve", "cdu",
}
# Server baseboard management controllers, by vendor branding.
BMC_NAMES = {"idrac", "ilo", "xcc", "ipmi", "imm", "bmc", "cimc"}
# Dedicated management Ethernet on network gear, by vendor convention.
NET_MGMT_NAMES = {"mgmt0", "mgmt", "management", "management1", "fxp0", "ma1", "em0"}
NET_TYPES = {"switch", "router", "firewall", "load_balancer"}


def role_for(dtype: str, name: str) -> str:
    n = (name or "").strip().lower()
    if dtype == "oob_switch":
        return DATA
    if dtype in FACILITY_TYPES:
        return MGMT
    if dtype == "server":
        return MGMT if n in BMC_NAMES else DATA
    if dtype in NET_TYPES:
        return MGMT if n in NET_MGMT_NAMES else DATA
    return DATA


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]

    tagged = Counter()
    changed = 0
    for n in nodes:
        d = n["device"]
        dtype = d["device_type"]
        for itf in d.get("interfaces") or []:
            r = role_for(dtype, itf["name"])
            if itf.get("role") != r:
                changed += 1
            itf["role"] = r
            tagged[(dtype, r)] += 1

    # Cross-check the result against the edges: a management edge on a data port is
    # a real modelling defect (the console rides a switchport instead of mgmt0) —
    # but ONLY on a device that lives on the data plane. A device with no production
    # edges IS management-plane infrastructure (the OOB switches, and the OOB
    # firewalls/routers behind them), so its management edges are its own data
    # plane and belong on data ports. Same structural test as
    # add_network_mgmt_port.py — the two must agree about the same topology.
    byid = {n["id"]: n["device"] for n in nodes}
    has_prod = set()
    for e in edges:
        if e.get("layer") == "production":
            has_prod.update((e["src"], e["dst"]))
    defects = Counter()
    infra = Counter()
    for e in edges:
        if e.get("layer") != "management":
            continue
        for nid, ifc in ((e["src"], e["src_iface"]), (e["dst"], e["dst_iface"])):
            d = byid.get(nid)
            if not d:
                continue
            ifs = d.get("interfaces") or []
            if ifc is None or not (0 <= ifc < len(ifs)):
                continue
            if ifs[ifc].get("role") == MGMT:
                continue
            if nid not in has_prod:
                infra[f"{d['device_type']}: {d['name']}"] += 1
            else:
                defects[f"{d['device_type']}: {d['name']} -> {ifs[ifc]['name']}"] += 1

    if not dry_run:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would tag" if dry_run else "Tagged"
    print(f"{verb} {sum(tagged.values())} port(s) across {len(nodes)} device(s); "
          f"{changed} role(s) changed. {'(dry run)' if dry_run else f'Wrote {p}'}\n")
    for (dtype, r), c in sorted(tagged.items()):
        print(f"  {c:5d}  {dtype:15s} {r}")
    if defects:
        print(f"\n  DEFECT: {sum(defects.values())} console edge(s) on a DATA port "
              f"({len(defects)} device(s)) — run tools/add_network_mgmt_port.py:")
        for k, c in sorted(defects.items()):
            print(f"  {c:5d}  {k}")
    if infra:
        print(f"\n  OK: {sum(infra.values())} management edge(s) on data ports across "
              f"{len(infra)} management-plane device(s) — this is correct; the mgmt "
              f"network IS their data plane:")
        for k, c in sorted(infra.items()):
            print(f"  {c:5d}  {k}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
