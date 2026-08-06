#!/usr/bin/env python3
"""Move each datacenter's OOB core switch out of a compute hall and into the
Network Room, beside the network core it exists to manage.

BEFORE

    Server Hall A, rack R1-03, U42
      OOBC1-DC1-HA-R1-03
        power <- PDUA/PDUB-DC1-HA-R1-03      (Hall A's network-row rack PDUs)
        mgmt  -> 12 access + BMS OOB switches across Hall A and Hall B
              -> OOB1-DC1-CP                  (Central Plant BMS)
              -> OOB1-DC1-NR-R1-04            (Network Room)

The whole datacenter's management plane — both halls, the Central Plant BMS, and
the Network Room's own routers, firewalls and core switches — hung off a single
switch in one compute hall, drawing from that hall's rack PDUs. Three problems:

  * INVERTED HIERARCHY. The Network Room is the MDF: routers, firewalls, load
    balancers, core switches, and the fibre aggregation. Its access OOB uplinked
    INTO a compute hall. A real OOB core sits with the network core, because that
    is where the fibre lands and where the management uplink terminates.

  * FAULT DOMAIN. De-energize Hall A for electrical work and you lose remote hands
    on Hall B, on the network core, and on the chiller plant's BMS — the facility
    gear you most need visibility into during exactly that kind of event.

  * CABLE PLANT. Hall B's out-of-band trombones through Hall A to reach an
    aggregation switch it has no other reason to touch.

AFTER

    Network Room, rack R1-04, U38  (beside COR1/COR2)
      OOBC1-DC1-NR-R1-04
        power <- PDUA/PDUB-DC1-NR-R1-04 -> RPPA/RPPB-DC1-NR -> UPSA/UPSB

The POWER PATH was never the problem and does not change in character: the OOB
core stays dual-corded on the 2N critical bus, behind both transfer switches. It
simply now draws from the room it lives in. Every management uplink is untouched —
only the switch's room, rack, and its two power cords move.

Not addressed here, deliberately: the OOB core remains a single point of failure
for the management AND BMS plane (there is one per DC, and every access OOB is
single-homed to it), and the BMS switches still terminate on the same aggregation
as the IT OOB, so the OT/IT segmentation is VLAN-deep at best.

Idempotent: a DC whose OOB core already sits in the Network Room is skipped.
Re-export the floorplan afterwards.

Usage:
    python tools/relocate_oob_core.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TARGET_ROOM = "Network Room"
CORE_U = 38          # below COR2 (U40), above the NR access OOB (U30)


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n for n in nodes}

    def drop(pred):
        before = len(edges)
        edges[:] = [e for e in edges if not pred(e)]
        return before - len(edges)

    moved = 0
    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc
                    and pred(n["device"])]

        core = next((n for n in in_dc(
            lambda d: (d.get("name") or "").startswith("OOBC"))), None)
        if core is None:
            print(f"{dc}: no OOB core — skipped")
            continue
        if core["device"].get("room") == TARGET_ROOM:
            print(f"{dc}: OOB core already in the {TARGET_ROOM} — skipped")
            continue

        # The core-switch rack is the one holding COR1; the OOB core joins it.
        cor = next((n for n in in_dc(
            lambda d: (d.get("name") or "").startswith("COR1")
            and d.get("room") == TARGET_ROOM)), None)
        if cor is None:
            print(f"{dc}: no COR1 in the {TARGET_ROOM} — skipped")
            continue
        rack_row = cor["device"]["rack_row"]
        rack_num = cor["device"]["rack_num"]

        pdus = [n for n in in_dc(
            lambda d: d["device_type"] == "pdu" and d.get("room") == TARGET_ROOM
            and d.get("rack_row") == rack_row and d.get("rack_num") == rack_num)]
        pdu_a = next((n for n in pdus if n["device"]["name"].startswith("PDUA")), None)
        pdu_b = next((n for n in pdus if n["device"]["name"].startswith("PDUB")), None)
        if pdu_a is None or pdu_b is None:
            print(f"{dc}: rack R{rack_row}-{rack_num:02d} has no A/B PDU pair — skipped")
            continue

        d = core["device"]
        old_name, old_room = d["name"], d.get("room")

        # ── Re-cord onto the Network Room rack's own A/B pair ─────────────────
        dropped = drop(lambda e: e.get("layer") == "power" and e["dst"] == core["id"])
        # The cords ARE the record — see Device in core/device_manager.py.
        for pdu in (pdu_a, pdu_b):
            edges.append({"src": pdu["id"], "dst": core["id"], "src_iface": 0,
                          "dst_iface": 0, "broken": False, "layer": "power"})

        # ── Re-home it physically ────────────────────────────────────────────
        d["name"] = f"OOBC1-{dc}-NR-R{rack_row}-{rack_num:02d}"
        d["room"] = TARGET_ROOM
        d["floor"] = cor["device"].get("floor")
        d["rack_row"] = rack_row
        d["rack_num"] = rack_num
        d["rack_unit"] = CORE_U
        d["floor_x"] = cor["device"].get("floor_x")
        d["floor_y"] = cor["device"].get("floor_y")

        # Canvas coordinates are NOT set here: the switch changed rooms, and
        # tools/layout_canvas.py derives every coordinate from the room it is in.

        uplinks = sum(1 for e in edges if e.get("layer") == "management"
                      and core["id"] in (e["src"], e["dst"]))
        moved += 1
        print(f"{dc}: {old_name} ({old_room}) -> {d['name']} "
              f"({TARGET_ROOM} R{rack_row}-{rack_num:02d} U{CORE_U}), "
              f"dropped {dropped} hall power cords, re-corded onto "
              f"{pdu_a['device']['name']}/{pdu_b['device']['name']}, "
              f"{uplinks} management uplinks untouched")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nRelocated {moved} OOB core switch(es). Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
