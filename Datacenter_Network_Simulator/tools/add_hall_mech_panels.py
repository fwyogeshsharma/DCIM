#!/usr/bin/env python3
"""Give each server hall its own A/B mechanical power panels, and leave the MCCs
to the chiller plant.

BEFORE

    ATS1 -> MCC1-DC1-MR ─┬─> chillers / pumps / towers (Central Plant, Roof)
                         └─> 7 CRAHs scattered across Server Hall A and B
    ATS2 -> MCC2-DC1-MR ─┴─> (the other half of each)

One Motor Control Center feeding both the chiller plant and every hall's air
handlers collapses two distribution tiers into one. Chillers and their pumps are
100-400 hp motors with short, fat feeders and belong on an MCC standing at the
plant. CRAH fans are small (~6.5 kW) and numerous, and hang off a panelboard in
the hall they cool — you do not run 400 A bus across the site to reach them.

AFTER

    ATS1 -> MCC1-DC1-MR ─┬─> chiller plant (A-side trains)
                         ├─> MPPA-DC1-HA ─> CRAH 1,3,5,7   (Server Hall A)
                         └─> MPPA-DC1-HB ─> CRAH 1,3,5,7   (Server Hall B)
    ATS2 -> MCC2-DC1-MR ─┬─> chiller plant (B-side train)
                         ├─> MPPB-DC1-HA ─> CRAH 2,4,6     (Server Hall A)
                         └─> MPPB-DC1-HB ─> CRAH 2,4,6     (Server Hall B)

The MPP is the mechanical mirror of what the RPP is on the critical side: one rank
below its bus, feeding many small loads. It is NOT UPS-backed — it inherits its
source, and the mechanical bus tie, from the MCC above it. So none of the transfer
behaviour changes: a hall panel goes dark exactly when its MCC does, and its CRAHs
come back in mechanical load block 3, last, on a genset restart.

CRAHs keep alternating A/B within each hall, so losing one source thins the air
side evenly rather than killing one end of the room.

Idempotent: a hall that already has an MPP is skipped. Run after
fix_cooling_trains.py; re-export the floorplan afterwards.

Usage:
    python tools/add_hall_mech_panels.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOM_CODE = {"Server Hall A": "HA", "Server Hall B": "HB"}
MODEL = "Eaton Pow-R-Line 3a 150A"      # 150 A panelboard ≈ 93.5 kW at 400 V 3-phase
VENDOR = "Eaton"
CRAH_ROW = 9                            # CRAHs sit on the back wall, rack_row 9


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

    def edge(s, t, layer="power"):
        edges.append({"src": s, "dst": t, "src_iface": 0, "dst_iface": 0,
                      "broken": False, "layer": layer})

    def drop(pred):
        before = len(edges)
        edges[:] = [e for e in edges if not pred(e)]
        return before - len(edges)

    total = 0
    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc
                    and pred(n["device"])]

        mccs = sorted(in_dc(lambda d: d["device_type"] == "mcc"),
                      key=lambda n: n["device"]["name"])
        if len(mccs) < 2:
            print(f"{dc}: needs two MCCs — skipped")
            continue
        mcc_ids = [m["id"] for m in mccs]
        seed_ip = mccs[0]["device"].get("mgmt_ip") or ""
        base = {k: mccs[0]["device"].get(k) for k in
                ("country", "datacenter_city", "datacenter")}

        for room, rc in sorted(ROOM_CODE.items()):
            crahs = sorted(in_dc(lambda d, _r=room: d["device_type"] == "crah"
                                 and d.get("room") == _r),
                           key=lambda n: n["device"]["name"])
            if not crahs:
                continue
            if in_dc(lambda d, _r=room: d["device_type"] == "mpp" and d.get("room") == _r):
                print(f"{dc}/{room}: already has mechanical panels — skipped")
                continue

            floor = crahs[0]["device"].get("floor")
            # The panels stand in the two END BAYS of the CRAH back wall, flanking
            # the CRAHs they feed. The CRAH lineup is inset by CRAH_END_RESERVE
            # (seed_hall_crahs / fleet), which opens ~0.7 m at each end for exactly
            # this — so a panel at the ends no longer overlaps the end CRAH.
            fy = crahs[0]["device"].get("floor_y", 11.4)
            # Opposite ends of the CRAH wall. The far end is derived from the room's
            # own width — halls differ (8.4 m vs 7.8 m) and a hard-coded offset walks
            # one of them through the wall.
            room_w = float((topo.get("floorplan", {}).get("rooms", {})
                            .get(f"{dc}/{room}", {}) or {}).get("width_m") or 8.4)
            # Rack slots after the last CRAH, so a hall that grows more air handlers
            # never collides with its own panels.
            last = max((n["device"].get("rack_num") or 0) for n in crahs)
            geom = [("A", last + 1, 0.3),
                    ("B", last + 2, round(room_w - 0.3, 3))]

            panels = {}
            for side, rack_num, fx in geom:
                nid = new_id()
                mgmt = next_ip(seed_ip, used_mgmt)
                node = {
                    "id": nid,
                    # Canvas coordinates are owned by tools/layout_canvas.py.
                    "position": {"x": 0, "y": 0},
                    "device": {
                        "name": f"MPP{side}-{dc}-{rc}",
                        "device_type": "mpp",
                        "vendor": VENDOR,
                        "ip_address": "",
                        "snmp_port": 161,
                        "gnmi_port": 57400,
                        "snmp_community": mgmt,
                        "interface_count": 1,
                        "interface_groups": [{"iface_type": "Gigabit Ethernet (1 Gbps)", "count": 1}],
                        "interfaces": [{
                            "index": 1, "name": "eth0", "speed": 1000000000,
                            "oper_status": 1,
                            "in_octets": 0, "out_octets": 0,
                            "in_errors": 0, "out_errors": 0,
                            "in_discards": 0, "out_discards": 0,
                            "mac_address": ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6]),
                            "connected_to_device": None, "connected_to_iface": None,
                        }],
                        "model_name": MODEL,
                        "metrics_enabled": True,
                        "id": nid,
                        "mgmt_ip": mgmt,
                        "mgmt_vlan": 10,
                        "power_draw_w": 0,
                        "rated_power_w": None,     # derived from model_name at load
                        "room": room,
                        "floor": floor,
                        "rack_row": CRAH_ROW, "rack_num": rack_num, "rack_unit": 0,
                        "floor_x": fx, "floor_y": fy,
                        **base,
                    },
                }
                nodes.append(node)
                byid[nid] = node
                panels[side] = node

            # Each panel from the MCC on its own side.
            edge(mcc_ids[0], panels["A"]["id"])
            edge(mcc_ids[1], panels["B"]["id"])

            # Re-home this hall's CRAHs off the MCCs and onto the hall panels.
            crah_ids = {n["id"] for n in crahs}
            moved = drop(lambda e: e.get("layer") == "power"
                         and ((e["src"] in mcc_ids and e["dst"] in crah_ids)
                              or (e["dst"] in mcc_ids and e["src"] in crah_ids)))
            for i, c in enumerate(crahs):
                edge(panels["A" if i % 2 == 0 else "B"]["id"], c["id"])

            # Management: a mechanical panel is facility gear, so it belongs on the
            # hall's BMS OOB switch alongside the CRAHs it feeds — not on the IT OOB.
            bms = next((n for n in in_dc(
                lambda d, _r=room: d["device_type"] == "oob_switch"
                and d.get("room") == _r
                and (d.get("name") or "").startswith("OOBM"))), None)
            if bms is None:
                bms = next((n for n in in_dc(
                    lambda d: d["device_type"] == "oob_switch"
                    and (d.get("name") or "").startswith("OOB1"))), None)
            if bms is not None:
                for node in panels.values():
                    edge(node["id"], bms["id"], "management")

            total += 2
            print(f"{dc}/{room}: +MPPA/+MPPB, re-homed {len(crahs)} CRAHs "
                  f"(dropped {moved} MCC feeds), mgmt on "
                  f"{bms['device']['name'] if bms else 'nothing'}")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nAdded {total} mechanical panels. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
