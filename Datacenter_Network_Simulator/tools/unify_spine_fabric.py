#!/usr/bin/env python3
"""Bring every server hall's spine fabric to the same shape: 4 spines packed two
per rack (planes SP1/SP2 x two racks), matching the reference halls.

DC1 halls already run 4 spines in 2 racks (SP1/SP2 in R1-01, SP1/SP2 in R1-02).
DC2 halls drifted to 3 spines in ONE rack (SP1/SP2/SP3 in R1-01) with the access-
OOB cluster next door in col 2. This normalizes any 3-in-1-rack hall to the 4-in-2-
rack layout, keeping the hall's own spine vendor (DC2 stays Dell Z9264F-ON):

    col 1  SP1 (U42) + SP2 (U41)          <- unchanged
    col 2  SP1 (U42) + SP2 (U41)  R1-02   <- SP3 re-homed here + one NEW spine
    col 3  OOB1..N + OOBM1                 <- the OOB cluster slides over one rack
                                             onto its own new A/B PDU pair

The new spine is wired like its siblings: uplinks to BOTH core switches (COR1/COR2
in the Network Room) and a downlink to EVERY leaf in the hall (each leaf's 4th
uplink port), dual-corded to the col-2 PDUs, console on the hall's primary access
OOB. The re-homed SP3 keeps all its links (they follow its id); only its rack, U,
name suffix and power cords move.

The OOB access-switch COUNT is deliberately NOT changed — it is 1-per-compute-rack
and correctly reflects each hall's rack count. Only the spine fabric is unified.

Idempotent: a hall already at 4 spines in 2 racks is skipped. Run
tools/layout_canvas.py and re-export the floorplan afterwards.

Usage:
    python tools/unify_spine_fabric.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

RACK_X0, RACK_PITCH = 0.3, 0.6      # floor_x = RACK_X0 + (col-1)*RACK_PITCH
SPINE_U = {0: 42, 1: 41}            # two spines per rack: U42 (plane SP1), U41 (SP2)


def rack_x(col: int) -> float:
    return round(RACK_X0 + (col - 1) * RACK_PITCH, 4)


def role(nm: str) -> str:
    return "".join(c for c in (nm or "").split("-", 1)[0] if c.isalpha()).upper()


def next_ip(seed: str, used: set) -> str:
    if not seed:
        return ""
    a, b, c, e = (int(x) for x in seed.split("."))
    for _ in range(65535):
        e += 1
        if e > 254:
            e = 1
            c += 1
        ip = f"{a}.{b}.{c}.{e}"
        if ip not in used:
            used.add(ip)
            return ip
    return ""


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n for n in nodes}
    used_ids = set(byid)
    used_ips = {n["device"].get("ip_address") for n in nodes if n["device"].get("ip_address")}
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}

    def new_id() -> str:
        i = uuid.uuid4().hex[:8]
        while i in used_ids:
            i = uuid.uuid4().hex[:8]
        used_ids.add(i)
        return i

    def add_edge(s, t, layer, si=0, di=0):
        edges.append({"src": s, "dst": t, "src_iface": si, "dst_iface": di,
                      "broken": False, "layer": layer})

    def drop_power_to(dev_id):
        edges[:] = [e for e in edges
                    if not (e.get("layer") == "power" and e.get("dst") == dev_id)]

    def next_iface(dev_id) -> int:
        used = set()
        for e in edges:
            if e["src"] == dev_id:
                used.add(e["src_iface"])
            if e["dst"] == dev_id:
                used.add(e["dst_iface"])
        i = 0
        while i in used:
            i += 1
        return i

    def hall_dev(dc, room, pred):
        return [n for n in nodes if n["device"].get("datacenter") == dc
                and n["device"].get("room") == room and pred(n["device"])]

    changed = 0
    halls = sorted({(n["device"]["datacenter"], n["device"]["room"])
                    for n in nodes
                    if (n["device"].get("room") or "").startswith("Server Hall")
                    and n["device"].get("datacenter")})

    for dc, room in halls:
        spines = hall_dev(dc, room, lambda d: d["device_type"] == "switch"
                          and role(d.get("name", "")).startswith("SP"))
        spine_racks = {n["device"]["rack_num"] for n in spines}
        if len(spines) >= 4 and len(spine_racks) >= 2:
            print(f"{dc}/{room}: spine fabric already 4-in-2-racks — skipped")
            continue
        if len(spines) != 3 or len(spine_racks) != 1:
            print(f"{dc}/{room}: unexpected spine layout ({len(spines)} spines in "
                  f"{len(spine_racks)} rack(s)) — skipped, inspect manually")
            continue

        col1 = next(iter(spine_racks))                      # the single spine rack
        col2, col3 = col1 + 1, col1 + 2
        floor_y = spines[0]["device"].get("floor_y")

        leaves = hall_dev(dc, room, lambda d: d["device_type"] == "switch"
                          and not role(d.get("name", "")).startswith("SP"))
        oob_cluster = hall_dev(dc, room, lambda d: d["device_type"] == "oob_switch")
        oob1 = next((n for n in oob_cluster
                     if (n["device"].get("name") or "").startswith("OOB1")), None)
        col2_pdus = hall_dev(dc, room, lambda d: d["device_type"] == "pdu"
                             and d.get("rack_row") == 1 and d.get("rack_num") == col2)
        pdu_a = next((n for n in col2_pdus if role(n["device"]["name"]) == "PDUA"), None)
        pdu_b = next((n for n in col2_pdus if role(n["device"]["name"]) == "PDUB"), None)
        rppa = next(iter(hall_dev(dc, room, lambda d: role(d.get("name", "")) == "RPPA")), None)
        rppb = next(iter(hall_dev(dc, room, lambda d: role(d.get("name", "")) == "RPPB")), None)
        cores = [next(iter(hall_dev(dc, "Network Room",
                                    lambda d, c=c: (d.get("name") or "").startswith(c))), None)
                 for c in ("COR1", "COR2")]
        if not all([oob1, pdu_a, pdu_b, rppa, rppb]) or not all(cores):
            print(f"{dc}/{room}: missing OOB1/PDU/RPP/COR — skipped")
            continue

        # ── 1. New A/B PDU pair in col3 for the OOB cluster, fed from the hall RPP,
        #        mgmt on OOB1 (mirrors the existing network-row PDUs). ────────────
        new_pdus = {}
        for side, src_pdu, rpp in (("A", pdu_a, rppa), ("B", pdu_b, rppb)):
            nid = new_id()
            node = copy.deepcopy(src_pdu)
            d = node["device"]
            node["id"] = d["id"] = nid
            d["name"] = f"PDU{side}-{dc}-{_hall_code(room)}-R1-{col3:02d}"
            d["ip_address"] = next_ip(d.get("ip_address") or "", used_ips)
            d["mgmt_ip"] = next_ip(d.get("mgmt_ip") or "", used_mgmt)
            d["snmp_community"] = d["mgmt_ip"]
            d["rack_num"] = col3
            d["floor_x"] = rack_x(col3)
            node["position"] = {"x": 0, "y": 0}
            nodes.append(node)
            byid[nid] = node
            add_edge(rpp["id"], nid, "power")               # RPP feeds the PDU
            add_edge(nid, oob1["id"], "management")         # PDU mgmt on OOB1
            new_pdus[side] = node

        # ── 2. Slide the OOB cluster col2 -> col3 onto the new PDUs. ─────────────
        for n in oob_cluster:
            d = n["device"]
            d["rack_num"] = col3
            d["floor_x"] = rack_x(col3)
            parts = d["name"].split("-")
            parts[-1] = f"{col3:02d}"
            d["name"] = "-".join(parts)
            drop_power_to(n["id"])
            # The cords ARE the record — see Device in core/device_manager.py.
            add_edge(new_pdus["A"]["id"], n["id"], "power")
            add_edge(new_pdus["B"]["id"], n["id"], "power")

        # ── 3. Re-home SP3 into col2 U42 (plane SP1, rack 2); re-cord to col2 PDUs.
        sp3 = next(n for n in spines
                   if (n["device"].get("name") or "").split("-", 1)[0] == "SP3")
        d = sp3["device"]
        d["rack_num"] = col2
        d["rack_unit"] = SPINE_U[0]
        d["floor_x"] = rack_x(col2)
        d["name"] = f"SP1-{dc}-{_hall_code(room)}-R1-{col2:02d}"
        drop_power_to(sp3["id"])
        add_edge(pdu_a["id"], sp3["id"], "power")
        add_edge(pdu_b["id"], sp3["id"], "power")

        # ── 4. New 4th spine (plane SP2, rack 2), wired like its siblings. ──────
        tmpl = spines[0]
        nid = new_id()
        node = copy.deepcopy(tmpl)
        d = node["device"]
        node["id"] = d["id"] = nid
        d["name"] = f"SP2-{dc}-{_hall_code(room)}-R1-{col2:02d}"
        d["ip_address"] = next_ip(tmpl["device"].get("ip_address") or "", used_ips)
        d["mgmt_ip"] = next_ip(tmpl["device"].get("mgmt_ip") or "", used_mgmt)
        d["snmp_community"] = d["mgmt_ip"]
        d["rack_num"] = col2
        d["rack_unit"] = SPINE_U[1]
        d["floor_x"] = rack_x(col2)
        d["floor_y"] = floor_y
        node["position"] = {"x": 0, "y": 0}
        for i, itf in enumerate(d.get("interfaces") or []):
            itf["in_octets"] = itf["out_octets"] = 0
            itf["in_errors"] = itf["out_errors"] = 0
            itf["in_discards"] = itf["out_discards"] = 0
            itf["connected_to_device"] = None
            itf["connected_to_iface"] = None
            itf["mac_address"] = ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6])
        nodes.append(node)
        byid[nid] = node
        # uplinks to both cores, downlink to every leaf (its 4th uplink = port 3),
        # dual-corded to col2 PDUs, console on OOB1.
        add_edge(nid, cores[0]["id"], "production", si=0, di=next_iface(cores[0]["id"]))
        add_edge(nid, cores[1]["id"], "production", si=1, di=next_iface(cores[1]["id"]))
        for i, lf in enumerate(leaves):
            add_edge(nid, lf["id"], "production", si=2 + i, di=next_iface(lf["id"]))
        add_edge(pdu_a["id"], nid, "power")
        add_edge(pdu_b["id"], nid, "power")
        add_edge(nid, oob1["id"], "management")

        changed += 1
        print(f"{dc}/{room}: SP3 -> {sp3['device']['name']} (col{col2} U42); "
              f"+{d['name']} (col{col2} U41, {len(leaves)} leaf downlinks + 2 core "
              f"uplinks); OOB cluster ({len(oob_cluster)}) -> col{col3} on new "
              f"PDU pair; now 4 spines in 2 racks")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nUnified {changed} hall(s). Wrote {p}\n"
          f"Next: python tools/layout_canvas.py {p}  (then re-export the floorplan).")
    return 0


def _hall_code(room: str) -> str:
    # "Server Hall A" -> "HA"
    return "H" + (room.split()[-1] if room else "")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
