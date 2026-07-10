"""Move the core switches into the Network Room and give the room its own OOB.

Follow-on to split_network_room / network_room_power. Two realism fixes:

  1. Core switches (the DC backbone that interconnects the hall spines and hands
     off to the edge routers) belong in the network core room, not a compute
     hall. This relocates DC?-CORE1/2 into the Network Room, localises their rack
     power (rack PDUs -> the room RPP pair), and widens the room by one rack.
     Their production uplinks to the hall spines stay (that inter-room fibre is
     the normal MDF<->hall backbone).

  2. Out-of-band management is a per-area network: each room has its OWN OOB
     access switch uplinked to the OOB core (exactly like OOB-PLANT-<dc> in the
     Central Plant). The Network Room had none — its gear was managed by a hall
     OOB across rooms. This adds OOB-NET-<dc>, uplinks it to OOB-CORE-<dc>, and
     rewires every Network Room device's management link onto it.

Idempotent: a DC whose core is already in the Network Room is skipped.

    python tools/network_room_core_oob.py
"""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import hall_geometry as geo  # noqa: E402

TOPO = "topologies/dual_dc_enterprise.json"
NEW_ROOM = "Network Room"
CORE_RACK = 4          # core switches + OOB share rack column 4 of the room


def main() -> None:
    with open(TOPO, "r", encoding="utf-8") as f:
        doc = json.load(f)
    nodes, edges = doc["nodes"], doc["edges"]
    by_id = {n["device"]["id"]: n["device"] for n in nodes if n.get("device")}
    by_name = {n["device"]["name"]: n["device"] for n in nodes if n.get("device")}
    node_by_name = {n["device"]["name"]: n for n in nodes if n.get("device")}

    def dt(i):
        return (by_id.get(i) or {}).get("device_type")

    def nm(i):
        return (by_id.get(i) or {}).get("name", "?")

    def room(i):
        return (by_id.get(i) or {}).get("room")

    used_ips = {dev[f] for dev in by_id.values() for f in ("ip_address", "mgmt_ip") if dev.get(f)}

    def alloc_ip(template_ip: str) -> str:
        base = template_ip.rsplit(".", 1)[0]
        for h in range(2, 255):
            cand = f"{base}.{h}"
            if cand not in used_ips:
                used_ips.add(cand)
                return cand
        raise RuntimeError("subnet exhausted")

    def add_edge(src, dst, layer):
        for e in edges:
            if e.get("layer") == layer and {e["src"], e["dst"]} == {src, dst}:
                return
        edges.append({"src": src, "dst": dst, "src_iface": 0, "dst_iface": 0, "layer": layer})

    def drop_edges(dev_id, layer, other_pred):
        for e in list(edges):
            if e.get("layer") != layer or dev_id not in (e["src"], e["dst"]):
                continue
            other = e["dst"] if e["src"] == dev_id else e["src"]
            if other_pred(other):
                edges.remove(e)

    changed = 0
    dcs = sorted({d.get("datacenter") for d in by_id.values() if d.get("room") == NEW_ROOM and d.get("datacenter")})
    for dc in dcs:
        cores = [d for d in by_id.values()
                 if d.get("datacenter") == dc and d.get("device_type") == "switch"
                 and "CORE" in (d.get("name") or "").upper()]
        if not cores:
            continue

        rpp_a = by_name.get(f"RPP-NET-{dc}-A")
        rpp_b = by_name.get(f"RPP-NET-{dc}-B")
        if not rpp_a or not rpp_b:
            print(f"  {dc}: no RPP-NET pair (run network_room_power first) — skip.")
            continue

        cx, cy = geo.rack_x(CORE_RACK), geo.row_y(1)

        # 1. Relocate the core switches into the room's rack 4 (idempotent).
        for c in cores:
            c["room"] = NEW_ROOM
            c["floor"] = "1"
            c["floor_x"], c["floor_y"] = cx, cy
            c["rack_row"], c["rack_num"] = 1, CORE_RACK

        # 2. Localise the core rack PDUs: relocate + re-feed from the room RPP.
        a_pdus = {c.get("power_source_a") for c in cores if c.get("power_source_a")}
        b_pdus = {c.get("power_source_b") for c in cores if c.get("power_source_b")}
        for pdu_ids, rpp in ((a_pdus, rpp_a), (b_pdus, rpp_b)):
            for pid in pdu_ids:
                pdu = by_id.get(pid)
                if not pdu:
                    continue
                pdu["room"] = NEW_ROOM
                pdu["floor"] = "1"
                pdu["floor_x"], pdu["floor_y"] = cx, cy
                if "-SHA-" in (pdu.get("name") or ""):
                    pdu["name"] = pdu["name"].replace("-SHA-", "-NET-")
                drop_edges(pid, "power", lambda o: dt(o) == "rpp" and o != rpp["id"])
                add_edge(rpp["id"], pid, "power")

        # 3. Room-local OOB access switch (find-or-create), powered from the core
        #    rack PDUs and uplinked to the OOB core.
        oob_core = by_name.get(f"OOB-CORE-{dc}")
        oob = by_name.get(f"OOB-NET-{dc}")
        if oob is None:
            oob_tmpl = by_name.get(f"OOB-SW-{dc}-03") or next(
                (d for d in by_id.values() if d.get("device_type") == "oob_switch"
                 and d.get("datacenter") == dc and d.get("room") != NEW_ROOM), None)
            oob = copy.deepcopy(oob_tmpl)
            oob["id"] = uuid.uuid4().hex[:8]
            oob["name"] = f"OOB-NET-{dc}"
            oob["mgmt_ip"] = alloc_ip(oob_tmpl.get("mgmt_ip") or "192.168.0.0")
            oob["ip_address"] = ""
            oob["room"], oob["floor"] = NEW_ROOM, "1"
            oob["floor_x"], oob["floor_y"] = cx, cy
            oob["rack_row"], oob["rack_num"], oob["rack_unit"] = 1, CORE_RACK, 30
            oob["power_source_a"] = next(iter(a_pdus), None)
            oob["power_source_b"] = next(iter(b_pdus), None)
            for ifc in oob.get("interfaces", []):
                ifc["connected_to_device"] = None
                ifc["connected_to_iface"] = None
            # Canvas coordinates are owned by tools/layout_canvas.py.
            nodes.append({"id": oob["id"],
                          "position": {"x": 0, "y": 0},
                          "device": oob})
            by_id[oob["id"]] = oob
            by_name[oob["name"]] = oob
            if oob["power_source_a"]:
                add_edge(oob["power_source_a"], oob["id"], "power")
            if oob["power_source_b"]:
                add_edge(oob["power_source_b"], oob["id"], "power")
        if oob_core:
            add_edge(oob["id"], oob_core["id"], "management")   # uplink to OOB core

        # 4. Rewire management: every managed Network Room device -> the local OOB,
        #    including the relocated rack PDUs. The OOB switch itself is excluded so
        #    its uplink to the OOB core survives.
        netdevs = [d for d in by_id.values()
                   if d.get("room") == NEW_ROOM and d.get("datacenter") == dc
                   and d.get("device_type") in ("router", "firewall", "load_balancer",
                                                "switch", "pdu", "floor_pdu")]
        for dev in netdevs:
            drop_edges(dev["id"], "management",
                       lambda o: dt(o) == "oob_switch" and o != oob["id"] and room(o) != NEW_ROOM)
            add_edge(dev["id"], oob["id"], "management")

        # 5. Widen the room to 4 rack columns.
        ext = doc.get("floorplan", {}).get("rooms", {}).get(f"{dc}/{NEW_ROOM}")
        if ext:
            ext["racks_per_row"] = CORE_RACK
            ext["width_m"] = round(CORE_RACK * geo.RACK_PITCH + 2 * geo.rack_x(1), 4)

        changed += 1
        print(f"  {dc}: moved {len(cores)} core switch(es) -> {NEW_ROOM}, "
              f"added {oob['name']} ({oob['mgmt_ip']}) uplinked to {nm(oob_core['id']) if oob_core else '?'}, "
              f"rewired {len(netdevs)} mgmt link(s).")

    if not changed:
        print("Nothing to do.")
        return
    with open(TOPO, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"Done: {changed} DC(s) updated.")


if __name__ == "__main__":
    main()
