#!/usr/bin/env python3
"""Repair cross-boundary link anomalies (findings 1, 2, 4 of the cross-link audit).

1. CDU cooling servers in ANOTHER hall — a direct-to-chip loop can't cross rooms.
   Re-point the CDU end of every cross-room CDU<->server edge to the CDU in the
   SERVER's own rack (one CDU per compute rack).
2. Rack PDUs wired straight to the DC OOB-CORE — an end device must hit an ACCESS
   OOB, not the aggregation core. Re-point each non-OOB device on the OOB-CORE to
   an access OOB in its OWN hall (OOB-CORE keeps only its access-OOB uplinks).
4. Central-Plant PDUs fed by a Server-Hall IT RPP — re-point the feed to the local
   plant RPP (the one in the Central Plant).

Edge-only surgery (no devices added/removed). Idempotent. Run
export_dcim_floorplan.py afterwards.

Usage:
    python tools/fix_crosslinks.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    N = {n["id"]: n["device"] for n in topo["nodes"]}
    E = topo["edges"]
    T = lambda i: N[i]["device_type"]
    NM = lambda i: N[i].get("name")
    RM = lambda i: N[i].get("room")
    DC = lambda i: N[i]["datacenter"]
    rackkey = lambda i: (DC(i), RM(i), N[i].get("rack_row"), N[i].get("rack_num"))

    # Lookups
    cdu_by_rack = {rackkey(i): i for i in N if T(i) == "cdu"}
    cdu_by_hall = defaultdict(list)                     # (dc, room) -> [ids]
    for i in N:
        if T(i) == "cdu":
            cdu_by_hall[(DC(i), RM(i))].append(i)
    for k in cdu_by_hall:
        cdu_by_hall[k].sort(key=lambda i: NM(i))
    access_oob = defaultdict(list)                       # (dc, room) -> [ids]
    for i in N:
        if T(i) == "oob_switch" and (NM(i) or "").startswith("OOB") \
                and not (NM(i) or "").startswith("OOBC"):
            access_oob[(DC(i), RM(i))].append(i)
    for k in access_oob:
        access_oob[k].sort(key=lambda i: NM(i))
    plant_rpp = {DC(i): i for i in N if T(i) == "rpp" and RM(i) == "Central Plant"}
    plant_oob = {DC(i): i for i in N if T(i) == "oob_switch" and RM(i) == "Central Plant"}
    oob_core = {i for i in N if (NM(i) or "").startswith("OOBC")}
    # Electrical/mechanical rooms belong on the facility/BMS network (the plant
    # OOB), not a server-hall OOB. (Roof towers already hang off the plant OOB.)
    FACILITY_ROOMS = {"Generator Room", "UPS Room", "Mechanical Room"}

    n1 = n2 = n3 = n4 = 0
    for e in E:
        s, t, lay = e["src"], e["dst"], e.get("layer")

        # ── 1. CDU <-> server cross-room ──
        if lay == "cooling" and {T(s), T(t)} == {"cdu", "server"}:
            cdu, srv = (s, t) if T(s) == "cdu" else (t, s)
            if (DC(cdu), RM(cdu)) != (DC(srv), RM(srv)):
                # Prefer the CDU in the server's own rack; else any CDU in its
                # hall (CDUs are row-based and can serve adjacent racks).
                hall_cdus = cdu_by_hall.get((DC(srv), RM(srv))) or []
                tgt = cdu_by_rack.get(rackkey(srv)) or (hall_cdus[0] if hall_cdus else None)
                if tgt:
                    if e["src"] == cdu:
                        e["src"] = tgt
                    else:
                        e["dst"] = tgt
                    n1 += 1
            continue

        # ── 2. non-OOB device wired to the OOB-CORE ──
        if lay == "management" and (s in oob_core or t in oob_core):
            core, dev = (s, t) if s in oob_core else (t, s)
            if T(dev) != "oob_switch":
                cand = access_oob.get((DC(dev), RM(dev))) or access_oob.get((DC(core), RM(core)))
                if cand:
                    tgt = cand[0]
                    if e["src"] == core:
                        e["src"] = tgt
                    else:
                        e["dst"] = tgt
                    n2 += 1
            continue

        # ── 3. facility gear (Gen/UPS/Mech room) on a server-hall OOB ──
        if lay == "management" and (T(s) == "oob_switch" or T(t) == "oob_switch"):
            oob, dev = (s, t) if T(s) == "oob_switch" else (t, s)
            if T(dev) != "oob_switch" and RM(dev) in FACILITY_ROOMS \
                    and (RM(oob) or "").startswith("Server Hall"):
                tgt = plant_oob.get(DC(dev))
                if tgt:
                    if e["src"] == oob:
                        e["src"] = tgt
                    else:
                        e["dst"] = tgt
                    n3 += 1
            continue

        # ── 4. plant PDU fed by a non-plant RPP ──
        if lay == "power" and T(s) == "rpp" and T(t) == "pdu" \
                and RM(t) == "Central Plant" and RM(s) != "Central Plant":
            tgt = plant_rpp.get(DC(t))
            if tgt:
                e["src"] = tgt
                n4 += 1
            continue

    # Drop any edge that became a self-loop or an exact duplicate.
    seen = set()
    clean = []
    for e in E:
        if e["src"] == e["dst"]:
            continue
        k = (e["src"], e["dst"], e.get("layer"))
        if k in seen:
            continue
        seen.add(k)
        clean.append(e)
    topo["edges"] = clean

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Fix 1 (CDU cross-hall): {n1} edges re-pointed")
    print(f"Fix 2 (PDU->OOB-CORE):  {n2} edges re-homed to access OOB")
    print(f"Fix 3 (facility->plant OOB): {n3} edges re-homed")
    print(f"Fix 4 (plant PDU feed): {n4} edges re-fed from plant RPP")
    print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
