#!/usr/bin/env python3
"""Re-lay each DC's Server Hall B network row (row 1) to mirror Hall A's rack
packing exactly.

promote_hall_b_pod.py placed Hall B's spines/OOB one-per-rack (rack 1 held only
PDUs, each spine its own rack, each OOB its own rack). Hall A packs them densely
(2–3 spines/rack, all OOB in one rack, RPP+EV2 on the flanks). This copies each
Hall B network device's rack position (rack_num, floor_x, floor_y) from its
Hall A positional twin, so the two halls' row 1 look identical:

  spine[i]     <- Hall A spine[i]           (sorted by name)
  access-oob[i]<- Hall A OOB-SW[i]          (all land in Hall A's single OOB rack)
  RPP-IT-*-A2  <- Hall A RPP-IT-*-A1        (matched by A/B side)
  EV2[i]       <- Hall A EV2[i]             (sorted by rack, co-racks with its RPP)
  PDU ...-R1-N <- Hall A PDU ...-R1-N       (matched by R1-<n>-<side> suffix)

Hall A's OOB-CORE has no Hall B twin (one per DC) and is skipped. Idempotent.
Run export_dcim_floorplan.py afterwards.

Usage:
    python tools/relay_hall_b_row1.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def pos_of(dv):
    return {"rack_num": dv.get("rack_num"), "floor_x": dv.get("floor_x"),
            "floor_y": dv.get("floor_y")}


def apply_pos(dv, pos):
    dv["rack_num"] = pos["rack_num"]
    dv["floor_x"] = pos["floor_x"]
    dv["floor_y"] = pos["floor_y"]


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nd = [n["device"] for n in topo["nodes"]]

    def sel(dc, room, pred):
        return [d for d in nd if d["datacenter"] == dc and d.get("room") == room
                and (d.get("rack_row") or 0) == 1 and pred(d)]

    moved = 0
    for dc in sorted({d["datacenter"] for d in nd}):
        HA, HB = "Server Hall A", "Server Hall B"
        is_sp = lambda d: d["device_type"] == "switch" and "-SP" in (d.get("name") or "")
        is_oob = lambda d: d["device_type"] == "oob_switch" and "OOB-SW" in (d.get("name") or "")
        is_rpp = lambda d: d["device_type"] == "rpp"
        is_ev2 = lambda d: d["device_type"] == "energy_monitor"
        is_pdu = lambda d: d["device_type"] == "pdu"
        byname = lambda L: sorted(L, key=lambda d: d.get("name") or "")
        byrack = lambda L: sorted(L, key=lambda d: (d.get("rack_num") or 0))

        # spines / access-OOB — positional by sorted name
        for a, b in zip(byname(sel(dc, HA, is_sp)), byname(sel(dc, HB, is_sp))):
            apply_pos(b, pos_of(a)); moved += 1
        for a, b in zip(byname(sel(dc, HA, is_oob)), byname(sel(dc, HB, is_oob))):
            apply_pos(b, pos_of(a)); moved += 1

        # RPP — matched by A/B side (…-A1 -> …-A2, …-B1 -> …-B2)
        def side(d):
            m = re.search(r"-([AB])\d*$", d.get("name") or "")
            return m.group(1) if m else "?"
        ha_rpp = {side(d): d for d in sel(dc, HA, is_rpp)}
        for b in sel(dc, HB, is_rpp):
            a = ha_rpp.get(side(b))
            if a:
                apply_pos(b, pos_of(a)); moved += 1

        # EV2 — sorted by rack (co-racks with its RPP), positional
        for a, b in zip(byrack(sel(dc, HA, is_ev2)), byname(sel(dc, HB, is_ev2))):
            apply_pos(b, pos_of(a)); moved += 1

        # PDU — matched by R1-<n>-<side> suffix
        def suffix(d):
            m = re.search(r"R1-(\d+)-([AB])$", d.get("name") or "")
            return m.groups() if m else None
        ha_pdu = {suffix(d): d for d in sel(dc, HA, is_pdu) if suffix(d)}
        for b in sel(dc, HB, is_pdu):
            a = ha_pdu.get(suffix(b))
            if a:
                apply_pos(b, pos_of(a)); moved += 1

        print(f"{dc}: re-laid Hall B row 1 to mirror Hall A")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nMoved {moved} devices. Wrote {p}\nNext: python tools/export_dcim_floorplan.py "
          f"{path} {path.replace('.json','_floorplan.json')}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
