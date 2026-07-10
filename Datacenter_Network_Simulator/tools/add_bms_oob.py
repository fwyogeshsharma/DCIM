#!/usr/bin/env python3
"""Give each server hall a dedicated BMS/facility OOB switch and move the CRAHs
onto it — so HVAC is segmented from the IT out-of-band network.

Best practice keeps mechanical/facility gear (CRAH, chillers, pumps) on a
BMS/facility network separate from the IT OOB that manages servers/switches/PDUs
(OT/IT segmentation — HVAC is a known attack vector). Per hall this:
  1. adds one BMS OOB switch (OOBM…) in the network row, cloned from the hall's IT
     OOB, powered by the same network-row PDU pair,
  2. re-homes every CRAH's management link off the IT OOBs onto the BMS switch,
  3. uplinks the BMS switch to the DC's BMS AGGREGATION pair (BMSC*), which sits
     behind the management firewalls. See separate_bms_network.py.

Servers/leaves/PDUs stay on the IT OOBs. Idempotent (skips a hall that already has
an OOBM). Run export_dcim_floorplan.py + fix_edge_directions.py afterwards.

Usage:
    python tools/add_bms_oob.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

ROOM_CODE = {"Server Hall A": "HA", "Server Hall B": "HB"}


def next_ip(seed, used):
    if not seed:
        return ""
    a, b, c, d = (int(x) for x in seed.split("."))
    for _ in range(65535):
        d += 1
        if d > 254:
            d = 1; c += 1
        ip = f"{a}.{b}.{c}.{d}"
        if ip not in used:
            used.add(ip); return ip
    return ""


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]
    edges = topo["edges"]
    byid = {n["id"]: n for n in nodes}
    dev = lambda i: byid[i]["device"]
    used_ids = set(byid)
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}
    used_prod = {n["device"].get("ip_address") for n in nodes if n["device"].get("ip_address")}

    def sel(dc, room, dtype, pred=None):
        return [n for n in nodes if n["device"]["datacenter"] == dc
                and n["device"].get("room") == room
                and n["device"]["device_type"] == dtype
                and (pred is None or pred(n["device"]))]

    added = rehomed = 0
    for dc in sorted({n["device"]["datacenter"] for n in nodes}):
        # A BMS access switch uplinks to the BMS AGGREGATION pair, never to the IT
        # OOB core — that is the OT/IT boundary, enforced at the management firewalls
        # above the aggregation. Falls back to the OOB cores for a topology that has
        # not been split yet (see separate_bms_network.py).
        cores = [n for n in nodes if (n["device"].get("name") or "").startswith("BMSC")
                 and n["device"]["datacenter"] == dc]
        if not cores:
            cores = [n for n in nodes if (n["device"].get("name") or "").startswith("OOBC")
                     and n["device"]["datacenter"] == dc]
        for room in ("Server Hall A", "Server Hall B"):
            crahs = [n["id"] for n in sel(dc, room, "crah")]
            if not crahs:
                continue
            if sel(dc, room, "oob_switch", lambda d: (d.get("name") or "").startswith("OOBM")):
                continue                                        # already has a BMS switch
            it_oobs = sel(dc, room, "oob_switch",
                          lambda d: (d.get("name") or "").startswith("OOB")
                          and not (d.get("name") or "").startswith("OOBC")
                          and not (d.get("name") or "").startswith("OOBM"))
            if not it_oobs:
                continue
            tmpl = it_oobs[0]
            td = tmpl["device"]
            # power feeds of the template IT OOB (network-row PDU pair)
            pdus = [e["src"] for e in edges if e["dst"] == tmpl["id"] and e.get("layer") == "power"]

            # ── build the BMS switch (clone of the hall's IT OOB) ──
            bms = copy.deepcopy(tmpl)
            nid = uuid.uuid4().hex[:8]
            while nid in used_ids:
                nid = uuid.uuid4().hex[:8]
            used_ids.add(nid)
            bd = bms["device"]
            bms["id"] = nid; bd["id"] = nid
            rc = ROOM_CODE.get(room, "HX")
            bd["name"] = f"OOBM1-{dc}-{rc}-R1-{int(td.get('rack_num') or 1):02d}"
            bd["mgmt_ip"] = next_ip(td.get("mgmt_ip") or "", used_mgmt)
            bd["snmp_community"] = bd["mgmt_ip"]
            if bd.get("ip_address"):
                bd["ip_address"] = next_ip(td.get("ip_address") or "", used_prod)
            for iface in bd.get("interfaces", []):
                iface["mac_address"] = ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6])
                iface["connected_to_device"] = None
                iface["connected_to_iface"] = None
            pos = bms.get("position") or {"x": 0, "y": 0}
            bms["position"] = {"x": pos.get("x", 0), "y": pos.get("y", 0) + 40}
            nodes.append(bms); byid[nid] = bms; added += 1

            def edge(s, t, layer):
                edges.append({"src": s, "dst": t, "src_iface": 0, "dst_iface": 0,
                              "broken": False, "layer": layer})

            # power from the same network PDU pair; uplink to OOB-CORE
            for pdu in pdus:
                edge(pdu, nid, "power")
            for core in cores:
                edge(nid, core["id"], "management")            # BMS access -> core

            # ── re-home CRAHs: drop CRAH<->IT-OOB mgmt, add CRAH -> BMS ──
            it_ids = {n["id"] for n in it_oobs}
            crah_set = set(crahs)
            kept = []
            for e in edges:
                if e.get("layer") == "management" and (
                        (e["src"] in crah_set and e["dst"] in it_ids) or
                        (e["dst"] in crah_set and e["src"] in it_ids)):
                    continue                                    # drop
                kept.append(e)
            edges[:] = kept
            for cid in crahs:
                edge(cid, nid, "management")                    # CRAH -> BMS
                rehomed += 1
            print(f"{dc}/{room}: +BMS {bd['name']} | re-homed {len(crahs)} CRAH")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nAdded {added} BMS switches, re-homed {rehomed} CRAH. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
