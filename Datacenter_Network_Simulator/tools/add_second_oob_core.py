#!/usr/bin/env python3
"""Give each datacenter a second OOB core, and dual-home every access OOB to both.

BEFORE

    OOBC1-DC1-NR-R1-04  (Network Room, rack 4)
      <- 14 access + BMS OOB switches, each SINGLE-HOMED

One switch carried the entire management AND facility plane of the datacenter:
every server iDRAC, every switch console, every CRAH, the chiller controllers, the
transfer switches and the gensets. Lose it — a failed supervisor, a bad software
upgrade, a pulled cord in rack 4 — and you lose remote hands on all of it, during
exactly the event where you need them.

Management is not production, and plenty of enterprise datacenters accept N here.
This one does not get to: it carries the BMS.

AFTER

    OOBC1-DC1-NR-R1-04  (rack 4, beside the network core)
       |  peer link
    OOBC2-DC1-NR-R1-01  (rack 1, beside the edge routers)

    every access OOB uplinks to BOTH

Rack diversity is the point. Both cores are already dual-corded onto the 2N
critical bus, so a single UPS or transfer switch cannot take the pair — putting
them in the same rack would leave the rack itself as the failure domain, so the
second core goes in the furthest Network Room rack that has its own A/B PDU pair.

Port count grows to match: the cores were sized at exactly their uplink count
(14 ports for 14 access switches), which leaves no room for a peer link. Both are
re-sized to the full 48 ports the Catalyst 9300-48T / Dell N3248TE-ON carry, so
the core downlinks — not the switch itself — set the management-plane ceiling.

Not addressed here: the BMS switches still land on the same aggregation as the IT
OOB, so the OT/IT segmentation remains VLAN-deep. And the OOB network still has no
uplink at all — no management firewall, no jump host, no OOB WAN.

Idempotent: a DC that already has an OOBC2 is skipped.
Re-export the floorplan afterwards.

Usage:
    python tools/add_second_oob_core.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

TARGET_ROOM = "Network Room"
CORE_U = 38
CORE_PORTS = 48    # full port count of the Catalyst 9300-48T / Dell N3248TE-ON;
                   # each hall access OOB dual-homes to both cores, so the core
                   # downlink count is what caps a DC's management aggregation
                   # (see core/fleet_lifecycle._oob_cores_have_room)


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


def set_ports(dev: dict, count: int) -> None:
    """Resize the switch to *count* ports, preserving the first interface's shape."""
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
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n for n in nodes}
    used_ids = set(byid)
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}

    def edge(s, t, layer="management"):
        edges.append({"src": s, "dst": t, "src_iface": 0, "dst_iface": 0,
                      "broken": False, "layer": layer})

    added = 0
    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc
                    and pred(n["device"])]

        cores = sorted(in_dc(lambda d: (d.get("name") or "").startswith("OOBC")),
                       key=lambda n: n["device"]["name"])
        if len(cores) >= 2:
            print(f"{dc}: already has a redundant OOB core pair — skipped")
            continue
        if not cores:
            print(f"{dc}: no OOB core — skipped")
            continue
        core1 = cores[0]
        if core1["device"].get("room") != TARGET_ROOM:
            print(f"{dc}: OOB core is not in the {TARGET_ROOM} "
                  f"(run relocate_oob_core.py first) — skipped")
            continue

        # Furthest Network Room rack from core1 that carries its own A/B PDU pair.
        racks: dict = {}
        for n in in_dc(lambda d: d["device_type"] == "pdu" and d.get("room") == TARGET_ROOM):
            racks.setdefault((n["device"]["rack_row"], n["device"]["rack_num"]), []).append(n)
        here = (core1["device"]["rack_row"], core1["device"]["rack_num"])
        cands = [rk for rk, pdus in sorted(racks.items())
                 if rk != here
                 and any(x["device"]["name"].startswith("PDUA") for x in pdus)
                 and any(x["device"]["name"].startswith("PDUB") for x in pdus)]
        if not cands:
            print(f"{dc}: no other {TARGET_ROOM} rack with an A/B PDU pair — skipped")
            continue
        rk = max(cands, key=lambda r: abs(r[1] - here[1]))
        pdus = racks[rk]
        pdu_a = next(x for x in pdus if x["device"]["name"].startswith("PDUA"))
        pdu_b = next(x for x in pdus if x["device"]["name"].startswith("PDUB"))

        # ── Build the second core as a twin of the first ──────────────────────
        nid = uuid.uuid4().hex[:8]
        while nid in used_ids:
            nid = uuid.uuid4().hex[:8]
        used_ids.add(nid)
        core2 = copy.deepcopy(core1)
        d2 = core2["device"]
        core2["id"] = nid
        d2["id"] = nid
        d2["name"] = f"OOBC2-{dc}-NR-R{rk[0]}-{rk[1]:02d}"
        d2["mgmt_ip"] = next_ip(core1["device"].get("mgmt_ip") or "", used_mgmt)
        d2["snmp_community"] = d2["mgmt_ip"]
        d2["ip_address"] = ""
        d2["rack_row"], d2["rack_num"], d2["rack_unit"] = rk[0], rk[1], CORE_U
        d2["floor_x"] = pdu_a["device"].get("floor_x")
        d2["floor_y"] = pdu_a["device"].get("floor_y")
        # No power_source_a/b — the power edges added below ARE the record.
        core2["position"] = {"x": 0, "y": 0}   # placed by tools/layout_canvas.py
        set_ports(d2, CORE_PORTS)
        nodes.append(core2)
        byid[nid] = core2

        # Both cores need a port for the peer link; core1 was sized at exactly its
        # uplink count.
        set_ports(core1["device"], CORE_PORTS)

        # ── Power: this rack's own A/B pair, same 2N critical bus ─────────────
        edge(pdu_a["id"], nid, "power")
        edge(pdu_b["id"], nid, "power")

        # ── Dual-home every access OOB that reaches core1 ─────────────────────
        access = [e["src"] if e["dst"] == core1["id"] else e["dst"]
                  for e in edges if e.get("layer") == "management"
                  and core1["id"] in (e["src"], e["dst"])
                  and byid[e["src"] if e["dst"] == core1["id"] else e["dst"]]
                  ["device"]["device_type"] == "oob_switch"]
        for a in access:
            edge(a, nid, "management")       # access -> core, matching the convention

        # ── Peer link between the two cores ──────────────────────────────────
        edge(core1["id"], nid, "management")

        added += 1
        print(f"{dc}: +{d2['name']} in rack R{rk[0]}-{rk[1]:02d} "
              f"(core1 is R{here[0]}-{here[1]:02d}), corded onto "
              f"{pdu_a['device']['name']}/{pdu_b['device']['name']}, "
              f"dual-homed {len(access)} access OOB switches, +peer link, "
              f"both cores resized to {CORE_PORTS} ports")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nAdded {added} OOB core(s). Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
