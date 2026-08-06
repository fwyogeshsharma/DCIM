#!/usr/bin/env python3
"""Split each DC's co-located redundant network pairs across A/B racks in the
Network Room, so no redundant pair shares a single-rack fault domain.

PROBLEM

    Network Room, Row 1 — every core PAIR sat in ONE rack:
        R1  RTR1 + RTR2         (border / core routers)
        R2  FW1  + FW2          (perimeter firewalls)
        R3  LB1  + LB2          (load balancers)
        R4  COR1 + COR2         (core switches)

    A rack is a fault domain. Lose it — both branch PDUs on a panel fault, a
    thermal or physical incident, a maintenance slip — and you lose the WHOLE
    function, not half. Dual-cording A/B power does not remove the rack itself as
    the shared risk. The OOB/edge pairs (OOBC, OOBR, FWO) were ALREADY split across
    R1/R4; only the core data-path pairs were left doubled-up. This makes the split
    uniform.

FIX (two U-for-U rack swaps per DC; power re-corded to the destination rack)

    Core cabinets    : swap RTR2 <-> COR1
                         -> RTR-rack = RTR1 + COR1   (A-core)
                            COR-rack = RTR2 + COR2   (B-core)
    Service cabinets : swap FW2  <-> LB1
                         -> FW-rack  = FW1  + LB1    (A-services)
                            LB-rack  = FW2  + LB2    (B-services)

    Each moved device inherits the OTHER's exact rack + U slot + floor position and
    is re-corded onto its NEW rack's A/B PDU pair. Every management and production
    uplink is untouched — they reference the device id, which does not change.

Not touched here (out of scope): the OOB/mgmt gear (already split), FWM/FWO/JUMP/
OOB1/OOBM singletons, and any pre-existing U double-book among those.

Idempotent: a DC whose RTR (or FW) pair is already split is skipped. Run
tools/layout_canvas.py and re-export the floorplan afterwards — canvas coordinates
are derived there, not set here.

Usage:
    python tools/separate_network_pairs.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOM = "Network Room"
# Each swap moves the first device into the second's rack/slot and vice-versa.
SWAPS = [("RTR2", "COR1"), ("FW2", "LB1")]


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]

    def code(n) -> str:
        return (n["device"].get("name") or "").split("-", 1)[0]

    swaps_done = 0
    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def find(prefix):
            return next((n for n in nodes
                         if n["device"].get("datacenter") == dc
                         and n["device"].get("room") == ROOM
                         and code(n) == prefix), None)

        def pdus_in(row, num):
            got = [n for n in nodes
                   if n["device"].get("datacenter") == dc
                   and n["device"].get("room") == ROOM
                   and n["device"].get("device_type") == "pdu"
                   and n["device"].get("rack_row") == row
                   and n["device"].get("rack_num") == num]
            a = next((n for n in got if code(n) == "PDUA"), None)
            b = next((n for n in got if code(n) == "PDUB"), None)
            return a, b

        for a_name, b_name in SWAPS:
            a, b = find(a_name), find(b_name)
            if a is None or b is None:
                print(f"{dc}: {a_name}/{b_name} not both in {ROOM} — skipped")
                continue
            da, db = a["device"], b["device"]

            # Idempotency: only act while the moving device's OWN pair is still
            # co-located (e.g. RTR1 and RTR2 in one rack). After the swap they are
            # split, so a re-run finds them apart and skips.
            base, idx = a_name[:-1], a_name[-1]
            sib = find(base + ("1" if idx == "2" else "2"))
            co = (sib is not None
                  and sib["device"].get("rack_row") == da.get("rack_row")
                  and sib["device"].get("rack_num") == da.get("rack_num"))
            if not co:
                print(f"{dc}: {base} pair already split — skipped")
                continue

            # Capture both slots, then exchange them (rack + U + floor position).
            sa = {k: da.get(k) for k in
                  ("rack_row", "rack_num", "rack_unit", "floor_x", "floor_y", "floor")}
            sb = {k: db.get(k) for k in
                  ("rack_row", "rack_num", "rack_unit", "floor_x", "floor_y", "floor")}
            _place(da, sb)   # a takes b's slot
            _place(db, sa)   # b takes a's slot

            # Re-cord each device onto its NEW rack's A/B PDU pair.
            _record_power(nodes, edges, a, dc, pdus_in, ROOM)
            _record_power(nodes, edges, b, dc, pdus_in, ROOM)

            swaps_done += 1
            print(f"{dc}: swapped {da['name']} <-> {db['name']} "
                  f"(R{da['rack_row']}-{da['rack_num']:02d} U{da['rack_unit']} / "
                  f"R{db['rack_row']}-{db['rack_num']:02d} U{db['rack_unit']})")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nDone: {swaps_done} rack swap(s). Wrote {p}\n"
          f"Next: python tools/layout_canvas.py {p}  (then re-export the floorplan).")
    return 0


def _place(dev: dict, slot: dict) -> None:
    """Move *dev* into *slot* (rack_row/num/unit + floor position) and rewrite the
    trailing R<row>-<num> segments of its name to match. Leading role code and the
    DC/room segments are preserved."""
    for k, v in slot.items():
        if v is not None:
            dev[k] = v
    parts = dev["name"].split("-")
    if len(parts) >= 2:
        parts[-2] = f"R{dev['rack_row']}"
        parts[-1] = f"{dev['rack_num']:02d}"
        dev["name"] = "-".join(parts)


def _record_power(nodes, edges, node, dc, pdus_in, room) -> None:
    """Drop *node*'s existing power feeds and re-cord it onto the A/B PDU pair in
    the rack it now sits in."""
    dev = node["device"]
    pdu_a, pdu_b = pdus_in(dev["rack_row"], dev["rack_num"])
    if pdu_a is None or pdu_b is None:
        print(f"  ! {dev['name']}: destination rack has no A/B PDU pair — power left as-is")
        return
    dev_id = node["id"]
    edges[:] = [e for e in edges
                if not (e.get("layer") == "power" and e.get("dst") == dev_id)]
    # The cords ARE the record — see Device in core/device_manager.py.
    for pdu in (pdu_a, pdu_b):
        edges.append({"src": pdu["id"], "dst": dev_id, "src_iface": 0,
                      "dst_iface": 0, "broken": False, "layer": "power"})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
