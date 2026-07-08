"""Give the Network Room its own power distribution (run after split_network_room).

After the network core is split into a dedicated Network Room, its routers /
firewalls / load balancers still drew power from Server Hall A's rack PDUs and IT
RPPs — a power whip crossing rooms, which does not happen in a real datacenter.
Power distribution is room-local: the shared DC UPS feeds a room-local RPP pair,
which feeds the room's own rack PDUs.

This tool, per DC that has a Network Room:
  1. Creates a room-local RPP pair (RPP-NET-<dc>-A/-B), fed from the SAME UPS
     units that feed Hall A's IT RPPs (the UPS is a shared DC resource).
  2. Relocates the network racks' PDUs into the Network Room, renames them
     …-SHA-… -> …-NET-…, and re-feeds them from the new room RPP instead of the
     Hall A IT RPP.
The UPS stays shared; only the RPP + rack PDUs become room-local.

Idempotent: a DC whose RPP-NET-<dc>-A already exists is skipped.

    python tools/network_room_power.py
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
FLOOR = "topologies/dual_dc_enterprise_floorplan.json"
NEW_ROOM = "Network Room"


def main() -> None:
    with open(TOPO, "r", encoding="utf-8") as f:
        doc = json.load(f)

    nodes = doc["nodes"]
    edges = doc["edges"]
    by_id = {n["device"]["id"]: n["device"] for n in nodes if n.get("device")}
    by_name = {n["device"]["name"]: n["device"] for n in nodes if n.get("device")}
    names = set(by_name)

    def dtype(i):
        return (by_id.get(i) or {}).get("device_type")

    padj = defaultdict(list)
    for e in edges:
        if e.get("layer") == "power":
            padj[e["src"]].append(e)
            padj[e["dst"]].append(e)

    def add_power_edge(src: str, dst: str) -> None:
        for e in edges:
            if e.get("layer") == "power" and {e["src"], e["dst"]} == {src, dst}:
                return
        edges.append({"src": src, "dst": dst, "src_iface": 0, "dst_iface": 0, "layer": "power"})

    def remove_power_edges_to_rpp(pdu_id: str, keep: str) -> None:
        """Drop the PDU's power edge to any RPP that is not *keep*."""
        drop = []
        for e in list(edges):
            if e.get("layer") != "power":
                continue
            if pdu_id not in (e["src"], e["dst"]):
                continue
            other = e["dst"] if e["src"] == pdu_id else e["src"]
            if dtype(other) == "rpp" and other != keep:
                drop.append(e)
        for e in drop:
            edges.remove(e)

    def ups_behind(rpp_dev) -> str | None:
        for e in padj.get(rpp_dev["id"], []):
            other = e["dst"] if e["src"] == rpp_dev["id"] else e["src"]
            if dtype(other) == "ups":
                return other
        return None

    def clone_rpp(tmpl, name, fx, fy, rack_num):
        dev = copy.deepcopy(tmpl)
        dev["id"] = uuid.uuid4().hex[:8]
        dev["name"] = name
        dev["room"] = NEW_ROOM
        dev["floor"] = "1"
        dev["floor_x"] = fx
        dev["floor_y"] = fy
        dev["rack_row"] = 2
        dev["rack_num"] = rack_num
        dev["rack_unit"] = 0
        dev["ip_address"] = ""
        dev["mgmt_ip"] = ""
        for ifc in dev.get("interfaces", []):
            ifc["connected_to_device"] = None
            ifc["connected_to_iface"] = None
        return dev

    dcs = sorted({d.get("datacenter") for d in by_id.values()
                  if d.get("room") == NEW_ROOM and d.get("datacenter")})
    changed = 0
    for dc in dcs:
        if f"RPP-NET-{dc}-A" in names:
            print(f"  {dc}: RPP-NET-{dc}-A already exists — skip.")
            continue
        tmpl_a = by_name.get(f"RPP-IT-{dc}-A1")
        tmpl_b = by_name.get(f"RPP-IT-{dc}-B1")
        if tmpl_a is None or tmpl_b is None:
            print(f"  {dc}: no RPP-IT template — skip.")
            continue
        ups_a = ups_behind(tmpl_a)
        ups_b = ups_behind(tmpl_b)
        if not ups_a or not ups_b:
            print(f"  {dc}: could not resolve UPS behind RPP-IT — skip.")
            continue

        rpp_a = clone_rpp(tmpl_a, f"RPP-NET-{dc}-A", geo.rack_x(1), geo.row_y(2), 1)
        rpp_b = clone_rpp(tmpl_b, f"RPP-NET-{dc}-B", geo.rack_x(2), geo.row_y(2), 2)
        for r in (rpp_a, rpp_b):
            nodes.append({"id": r["id"], "position": {"x": 0, "y": 0}, "device": r})
            by_id[r["id"]] = r
        add_power_edge(ups_a, rpp_a["id"])       # shared DC UPS -> room-local RPP
        add_power_edge(ups_b, rpp_b["id"])

        # Relocate the network racks' PDUs and re-feed from the room RPP.
        netdevs = [d for d in by_id.values()
                   if d.get("room") == NEW_ROOM and d.get("datacenter") == dc
                   and d.get("device_type") in ("router", "firewall", "load_balancer")]
        a_pdus = {d.get("power_source_a") for d in netdevs if d.get("power_source_a")}
        b_pdus = {d.get("power_source_b") for d in netdevs if d.get("power_source_b")}
        for pdu_id, rpp in [(p, rpp_a) for p in a_pdus] + [(p, rpp_b) for p in b_pdus]:
            pdu = by_id.get(pdu_id)
            if pdu is None:
                continue
            pdu["room"] = NEW_ROOM
            pdu["floor"] = "1"
            if "-SHA-" in (pdu.get("name") or ""):
                pdu["name"] = pdu["name"].replace("-SHA-", "-NET-")
            remove_power_edges_to_rpp(pdu_id, keep=rpp["id"])
            add_power_edge(rpp["id"], pdu_id)    # room RPP -> rack PDU

        # Widen the Network Room extent to a 2nd row for the RPP pair.
        rooms = doc.setdefault("floorplan", {}).setdefault("rooms", {})
        ext = rooms.get(f"{dc}/{NEW_ROOM}")
        if ext:
            ext["rows"] = [1, 2]
            ext["depth_m"] = round(geo.row_y(2) + geo.RACK_D / 2 + 0.6, 4)
        changed += 1
        print(f"  {dc}: added RPP-NET pair (UPS {ups_a}/{ups_b}), "
              f"relocated {len(a_pdus) + len(b_pdus)} PDU(s).")

    if not changed:
        print("Nothing to do.")
        return

    with open(TOPO, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    if os.path.exists(FLOOR):
        with open(FLOOR, "r", encoding="utf-8") as f:
            fdoc = json.load(f)
        frooms = (fdoc.get("floorplan") if isinstance(fdoc.get("floorplan"), dict) else fdoc) \
            .setdefault("rooms", {})
        for dc in dcs:
            src = doc["floorplan"]["rooms"].get(f"{dc}/{NEW_ROOM}")
            if src:
                frooms[f"{dc}/{NEW_ROOM}"] = copy.deepcopy(src)
        with open(FLOOR, "w", encoding="utf-8") as f:
            json.dump(fdoc, f, indent=2)
        print("  synced", FLOOR)

    print(f"Done: {changed} DC network room(s) now self-powered.")


if __name__ == "__main__":
    main()
