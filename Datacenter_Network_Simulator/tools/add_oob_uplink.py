#!/usr/bin/env python3
"""Give the out-of-band network a way in and out: a jump host, a perimeter firewall
pair, and dedicated OOB WAN routers cross-linked between the two datacenters.

THE PROBLEM

    OOBC1 / OOBC2   <- every access OOB   <- every iDRAC, console, PDU
       (nothing above)

The management network was an island. Nothing uplinked it: no bastion to log into,
no perimeter enforcement, no WAN circuit. Whatever an operator actually used to
reach 192.168.x.x was simply not in the model — which mattered more once the BMS
sat behind a firewall below the same cores, because the picture implied a boundary
that had no outside.

AFTER

               DC1                                    DC2
      OOBR1-NR-R1-01  OOBR2-NR-R1-04  <==DCI==>  OOBR1  OOBR2
            \\           /                            (mirrored)
             FWO1     FWO2          perimeter firewalls
               \\     /
             OOBC1 OOBC2            OOB core pair
              /   |   \\
        JUMP1   access OOB ...      bastion + the rest of the DC

An out-of-band network earns its name by being reachable when production is down,
so the OOB WAN routers carry their OWN circuit — not the production WAN through
RTR1/RTR2. That circuit is outside the modelled graph; what IS modelled is the
management DCI between the two sites, so a datacenter whose OOB circuit fails is
still reachable through its peer.

JUMP1 is the bastion. It hangs off the OOB core pair, not off a hall OOB: it is
the only host on the management network an operator logs into, and everything
else is reached from it.

Everything is a pair in two different Network Room racks, matching the OOB cores
(rack 1 and rack 4) — both were always dual-corded on the 2N critical bus, so the
rack was the remaining failure domain.

Idempotent: a DC that already has an OOBR1 is skipped.
Re-export the floorplan afterwards.

Usage:
    python tools/add_oob_uplink.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

ROOM = "Network Room"

# (name prefix, device_type, vendor, model, rack_num, rack_unit, interface_count)
GEAR = [
    ("OOBR1", "router",   "Cisco Systems",      "Cisco ISR 4431", 1, 36, 4),
    ("OOBR2", "router",   "Cisco Systems",      "Cisco ISR 4431", 4, 36, 4),
    ("FWO1",  "firewall", "Palo Alto Networks", "PA-5220",        1, 34, 12),
    ("FWO2",  "firewall", "Palo Alto Networks", "PA-5220",        4, 34, 12),
    ("JUMP1", "server",   "Dell Technologies",  "PowerEdge R640", 3, 36, 2),
]


def next_ip(seed: str, used: set) -> str:
    if not seed:
        return ""
    a, b, c, d = (int(x) for x in seed.split("."))
    for _ in range(65535):
        d += 1
        if d > 254:
            d = 1
            c += 1
        ip = f"{a}.{b}.{c}.{d}"
        if ip not in used:
            used.add(ip)
            return ip
    return ""


def ifaces(count: int) -> list:
    out = []
    for i in range(1, count + 1):
        out.append({
            "index": i, "name": f"GigabitEthernet0/{i - 1}", "speed": 1000000000,
            "oper_status": 1, "in_octets": 0, "out_octets": 0,
            "in_errors": 0, "out_errors": 0, "in_discards": 0, "out_discards": 0,
            "mac_address": ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6]),
            "connected_to_device": None, "connected_to_iface": None,
        })
    return out


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n for n in nodes}
    used_ids = set(byid)
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}

    def edge(s, t, layer="management"):
        edges.append({"src": s, "dst": t, "src_iface": 0, "dst_iface": 0,
                      "broken": False, "layer": layer})

    made_by_dc: dict = {}
    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc
                    and pred(n["device"])]

        if in_dc(lambda d: (d.get("name") or "").startswith("OOBR")):
            print(f"{dc}: OOB uplink already present — skipped")
            continue

        cores = sorted(in_dc(lambda d: (d.get("name") or "").startswith("OOBC")),
                       key=lambda n: n["device"]["name"])
        if len(cores) < 2:
            print(f"{dc}: needs an OOB core pair (run add_second_oob_core.py) — skipped")
            continue

        by_name = {n["device"]["name"]: n for n in in_dc(lambda d: True)}
        anchor = cores[0]["device"]
        seed_ip = anchor.get("mgmt_ip") or ""
        base = {k: anchor.get(k) for k in
                ("country", "datacenter_city", "datacenter", "floor")}

        made = {}
        for prefix, dtype, vendor, model, rack, unit, ifc in GEAR:
            pa = by_name.get(f"PDUA-{dc}-NR-R1-{rack:02d}")
            pb = by_name.get(f"PDUB-{dc}-NR-R1-{rack:02d}")
            if pa is None or pb is None:
                print(f"{dc}: rack R1-{rack:02d} has no A/B PDU pair — skipped")
                return 1
            nid = uuid.uuid4().hex[:8]
            while nid in used_ids:
                nid = uuid.uuid4().hex[:8]
            used_ids.add(nid)
            mgmt = next_ip(seed_ip, used_mgmt)
            name = f"{prefix}-{dc}-NR-R1-{rack:02d}"
            node = {
                "id": nid,
                # Canvas coordinates are owned by tools/layout_canvas.py.
                "position": {"x": 0, "y": 0},
                "device": {
                    "name": name, "device_type": dtype, "vendor": vendor,
                    # Management-plane only: no production IP. The OOB WAN circuit
                    # these routers terminate is outside the modelled graph.
                    "ip_address": "", "snmp_port": 161, "gnmi_port": 57400,
                    "snmp_community": mgmt,
                    "interface_count": ifc,
                    "interface_groups": [{"iface_type": "Gigabit Ethernet (1 Gbps)",
                                          "count": ifc}],
                    "interfaces": ifaces(ifc),
                    "model_name": model, "metrics_enabled": True, "id": nid,
                    "mgmt_ip": mgmt, "mgmt_vlan": 10,
                    "power_draw_w": 0,          # filled from the SKU catalog at load
                    "room": ROOM,
                    "rack_row": 1, "rack_num": rack, "rack_unit": unit,
                    "floor_x": pa["device"].get("floor_x"),
                    "floor_y": pa["device"].get("floor_y"),
                    # No power_source_a/b — the power edges added below ARE the record.
                    **base,
                },
            }
            nodes.append(node)
            byid[nid] = node
            made[prefix] = node
            edge(pa["id"], nid, "power")
            edge(pb["id"], nid, "power")

        # ── Core -> perimeter firewalls -> OOB WAN routers ───────────────────
        for c in cores:
            for fw in ("FWO1", "FWO2"):
                edge(c["id"], made[fw]["id"])
        for fw in ("FWO1", "FWO2"):
            for r in ("OOBR1", "OOBR2"):
                edge(made[fw]["id"], made[r]["id"])

        # ── Bastion sits on the core pair, like an access switch would ───────
        for c in cores:
            edge(made["JUMP1"]["id"], c["id"])

        made_by_dc[dc] = made
        print(f"{dc}: +OOBR1/+OOBR2 (OOB WAN, racks 1 and 4), "
              f"+FWO1/+FWO2 (perimeter), +JUMP1 (bastion) | "
              f"cores -> firewalls -> routers, bastion on both cores")

    # ── Management DCI: each site's OOB reachable through its peer ───────────
    dcs = sorted(made_by_dc)
    if len(dcs) == 2:
        a, b = made_by_dc[dcs[0]], made_by_dc[dcs[1]]
        for r in ("OOBR1", "OOBR2"):
            edge(a[r]["id"], b[r]["id"])
        print(f"\n{dcs[0]} <-> {dcs[1]}: 2 management DCI links between the OOB WAN "
              f"routers — a site whose own circuit fails is reachable via its peer")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nWrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
