#!/usr/bin/env python3
"""Rebuild the chiller plant as three complete N+1 cooling trains, and feed them
from the MCCs by train instead of by round-robin.

WHAT WAS WRONG
--------------
Mechanically, the plant could not form three trains at all:

    CHL1  ->  CHWP1  CWP1  CT1        complete
    CHL2  ->  CHWP2  CWP2  CT2        complete
    CHL3  ->  CHWP3  CWP1  (none)     shares CHL1's condenser pump, NO tower

A chiller with no cooling tower cannot reject its condenser heat. CHL3 was
decorative. This adds a third condenser pump and a third tower cell so every
chiller owns a complete train: evaporator (CHW) pump, condenser (CW) pump, and
tower cell. CHWP4 stays on the chilled-water header as the N+1 standby pump —
any train's evaporator pump can be backed up by it.

    3 trains, 2 needed to carry design load, 1 spare  =>  N+1.

Electrically, the trains were split ROUND-ROBIN PER DEVICE TYPE across the two
MCCs (CHL1/CHL3 on A, CHL2 on B; CHWP1/CHWP3 on A, CHWP2/CHWP4 on B; and so on).
That gives each side an INCOMPLETE chain — chillers with no tower, pumps with no
chiller. Because the cooling model treats the plant as a series chain, losing one
MCC took out every stage at once and left 0% of cooling, not 50%. A whole train
now sits on one MCC:

    train 1 (CHL1 + CHWP1 + CWP1 + CT1)  -> MCC1
    train 2 (CHL2 + CHWP2 + CWP2 + CT2)  -> MCC2
    train 3 (CHL3 + CHWP3 + CWP3 + CT3)  -> MCC1
    CHWP4 (header standby)               -> MCC2
    VCHW / VCW (header isolation valves) -> MCC1 / MCC2

THE TIE BREAKER
---------------
Three trains do not divide across two sources such that either source can be lost
and still leave the two trains the load needs. That is why a real N+1 chiller
plant is fed from a main-tie-main mechanical switchboard: the two MCC buses have a
normally-open tie between them, and on loss of one source the tie closes so the
surviving source carries the whole mechanical load. Each MCC is rated for it (800 A
= 499 kW, against a mechanical load well under that).

The tie is modelled in core/device_state_store (an MCC falls back to its sibling's
source), not as a topology edge — a tie is a breaker between two buses of the same
rank, and the power cascade is a rank-ordered DAG.

Idempotent: a DC that already has a third condenser pump is skipped.
Run AFTER add_electrical_upstream.py, then re-export the floorplan.

Usage:
    python tools/fix_cooling_trains.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

PLANT_TYPES = ("chiller", "pump", "cooling_tower", "valve")

# Canvas coordinates are owned by tools/layout_canvas.py. New nodes start at the
# origin; run layout_canvas.py after this tool to place them.


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


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n for n in nodes}
    used_ids = set(byid)
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}

    def new_id() -> str:
        i = uuid.uuid4().hex[:8]
        while i in used_ids:
            i = uuid.uuid4().hex[:8]
        used_ids.add(i)
        return i

    def edge(s, t, layer):
        edges.append({"src": s, "dst": t, "src_iface": 0, "dst_iface": 0,
                      "broken": False, "layer": layer})

    def drop(pred):
        before = len(edges)
        edges[:] = [e for e in edges if not pred(e)]
        return before - len(edges)

    def clone(tmpl, name, room, mgmt_seed):
        nid = new_id()
        node = copy.deepcopy(tmpl)
        d = node["device"]
        node["id"] = nid
        d["id"] = nid
        d["name"] = name
        d["room"] = room
        node["position"] = {"x": 0, "y": 0}
        if d.get("mgmt_ip"):
            d["mgmt_ip"] = next_ip(mgmt_seed, used_mgmt)
            if d.get("snmp_community"):
                d["snmp_community"] = d["mgmt_ip"]
        d["ip_address"] = ""
        for iface in d.get("interfaces", []):
            iface["mac_address"] = ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6])
            iface["connected_to_device"] = None
            iface["connected_to_iface"] = None
        nodes.append(node)
        byid[nid] = node
        return node

    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc
                    and pred(n["device"])]

        by_name = {n["device"]["name"]: n for n in in_dc(lambda d: True)}
        if f"CWP3-{dc}-CP" in by_name:
            print(f"{dc}: already has three cooling trains — skipped")
            continue

        need = [f"CHL{i}-{dc}-CP" for i in (1, 2, 3)] + [f"CWP{i}-{dc}-CP" for i in (1, 2)]
        if any(n not in by_name for n in need):
            print(f"{dc}: expected 3 chillers + 2 condenser pumps — skipped")
            continue

        # ── Complete train 3: its own condenser pump and its own tower cell ───
        cwp3 = clone(by_name[f"CWP2-{dc}-CP"], f"CWP3-{dc}-CP", "Central Plant",
                     by_name[f"CWP2-{dc}-CP"]["device"].get("mgmt_ip", ""))
        ct3 = clone(by_name[f"CT2-{dc}-RF"], f"CT3-{dc}-RF", "Roof",
                    by_name[f"CT2-{dc}-RF"]["device"].get("mgmt_ip", ""))
        # clone() copies the node, never its edges — without this the new units are
        # unreachable over SNMP/BACnet. Facility gear goes on the plant BMS switch.
        bms = next((n for n in in_dc(lambda d: d["device_type"] == "oob_switch"
                                     and d.get("room") == "Central Plant")), None)
        if bms is not None:
            for unit in (cwp3, ct3):
                edge(unit["id"], bms["id"], "management")
        by_name[cwp3["device"]["name"]] = cwp3
        by_name[ct3["device"]["name"]] = ct3

        # ── Cooling loop: one condenser pump and one tower cell per chiller ───
        # CHL3 used to share CWP1 and had no tower at all.
        chl3, chl1 = by_name[f"CHL3-{dc}-CP"], by_name[f"CHL1-{dc}-CP"]
        cwp1 = by_name[f"CWP1-{dc}-CP"]
        removed = drop(lambda e: e.get("layer") == "cooling"
                       and {e["src"], e["dst"]} == {chl3["id"], cwp1["id"]})
        edge(chl3["id"], cwp3["id"], "cooling")        # chiller -> its condenser pump
        edge(cwp3["id"], by_name[f"VCW-{dc}-CP"]["id"], "cooling")   # pump -> CW header
        edge(by_name[f"VCW-{dc}-CP"]["id"], ct3["id"], "cooling")    # header -> tower cell
        edge(ct3["id"], chl3["id"], "cooling")         # tower -> back to the chiller

        # ── Feed whole TRAINS from one MCC each ──────────────────────────────
        mcc = [by_name.get(f"MCC{i}-{dc}-MR") for i in (1, 2)]
        if not all(mcc):
            print(f"{dc}: no MCCs — cooling loop fixed, power feeds left alone")
            continue
        mcc_ids = [m["id"] for m in mcc]

        plant_ids = {n["id"] for n in in_dc(lambda d: d["device_type"] in PLANT_TYPES)}
        dropped = drop(lambda e: e.get("layer") == "power"
                       and ((e["src"] in mcc_ids and e["dst"] in plant_ids)
                            or (e["dst"] in mcc_ids and e["src"] in plant_ids)))

        trains = {
            1: [f"CHL1-{dc}-CP", f"CHWP1-{dc}-CP", f"CWP1-{dc}-CP", f"CT1-{dc}-RF"],
            2: [f"CHL2-{dc}-CP", f"CHWP2-{dc}-CP", f"CWP2-{dc}-CP", f"CT2-{dc}-RF"],
            3: [f"CHL3-{dc}-CP", f"CHWP3-{dc}-CP", f"CWP3-{dc}-CP", f"CT3-{dc}-RF"],
        }
        train_mcc = {1: 0, 2: 1, 3: 0}          # trains 1 and 3 on MCC1, train 2 on MCC2
        for t, members in trains.items():
            for m in members:
                edge(mcc_ids[train_mcc[t]], by_name[m]["id"], "power")
        # Header equipment: the standby CHW pump and the two isolation valves are
        # not part of any train, so split them across the sides.
        edge(mcc_ids[1], by_name[f"CHWP4-{dc}-CP"]["id"], "power")
        edge(mcc_ids[0], by_name[f"VCHW-{dc}-CP"]["id"], "power")
        edge(mcc_ids[1], by_name[f"VCW-{dc}-CP"]["id"], "power")

        # CRAHs stay alternated A/B: adjacent air handlers in a hall should not
        # share a source, so a side loss thins the air side evenly instead of
        # killing one end of the room.
        crahs = sorted(in_dc(lambda d: d["device_type"] == "crah"),
                       key=lambda n: n["device"]["name"])
        crah_ids = {n["id"] for n in crahs}
        drop(lambda e: e.get("layer") == "power"
             and ((e["src"] in mcc_ids and e["dst"] in crah_ids)
                  or (e["dst"] in mcc_ids and e["src"] in crah_ids)))
        for i, c in enumerate(crahs):
            edge(mcc_ids[i % 2], c["id"], "power")

        print(f"{dc}: +CWP3 +CT3 | cooling: dropped {removed} shared-pump edge, "
              f"3 complete trains | power: re-fed {dropped} plant edges by train, "
              f"{len(crahs)} CRAHs alternated")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nWrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
