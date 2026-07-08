"""Mount a Verdigris EV2 energy meter on each Network Room RPP.

Every RPP in the curated topology is sub-metered by a Verdigris EV2 (the DC is
metered per branch panel — that is how the BACnet/PUE model sees each panel's
downstream load). The room-local RPPs added by network_room_power.py
(RPP-NET-<dc>-A/-B) had no EV2, so the Network Room's power was unmetered. This
tool clamps an EV2 on every RPP-NET that lacks one, mirroring the curated pattern.

Idempotent: an RPP-NET that already has an EV2 clamped is skipped.

    python tools/mount_ev2_network_room.py
"""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOPO = "topologies/dual_dc_enterprise.json"
FLOOR = "topologies/dual_dc_enterprise_floorplan.json"


def main() -> None:
    with open(TOPO, "r", encoding="utf-8") as f:
        doc = json.load(f)
    nodes, edges = doc["nodes"], doc["edges"]
    by_id = {n["device"]["id"]: n["device"] for n in nodes if n.get("device")}
    by_name = {n["device"]["name"]: n["device"] for n in nodes if n.get("device")}

    def dtype(i):
        return (by_id.get(i) or {}).get("device_type")

    padj = defaultdict(list)
    for e in edges:
        if e.get("layer") == "power":
            padj[e["src"]].append(e)
            padj[e["dst"]].append(e)

    used_ips = set()
    for dev in by_id.values():
        for fld in ("ip_address", "mgmt_ip"):
            if dev.get(fld):
                used_ips.add(dev[fld])

    def alloc_ip(template_ip: str) -> str:
        base = template_ip.rsplit(".", 1)[0]
        for host in range(2, 255):
            cand = f"{base}.{host}"
            if cand not in used_ips:
                used_ips.add(cand)
                return cand
        raise RuntimeError(f"no free IP in {base}.0/24")

    def ev2_edge_dir(ev2_id: str, rpp_id: str) -> tuple[str, str]:
        """Mirror the template EV2<->RPP power edge orientation."""
        for e in padj.get(ev2_id, []):
            if rpp_id in (e["src"], e["dst"]):
                return ("src", "dst") if e["src"] == ev2_id else ("dst", "src")
        return ("src", "dst")   # default: EV2 as src

    # RPP-NET panels lacking an EV2.
    targets = []
    for dev in by_id.values():
        if dev.get("device_type") == "rpp" and "-NET-" in (dev.get("name") or "") \
                and dev.get("name", "").startswith("RPP-NET-"):
            has_ev2 = any(dtype(n["dst"] if n["src"] == dev["id"] else n["src"]) == "energy_monitor"
                          for n in padj.get(dev["id"], []))
            if not has_ev2:
                targets.append(dev)

    if not targets:
        print("Every RPP-NET already has an EV2 — nothing to do.")
        return

    added = 0
    for rpp in sorted(targets, key=lambda d: d["name"]):
        dc = rpp.get("datacenter") or ""
        # "RPP-NET-DC1-A" -> dc=DC1, side=A
        side = rpp["name"].rsplit("-", 1)[-1]
        tmpl = by_name.get(f"EV2-{dc}-RPP01") or next(
            (d for d in by_id.values()
             if d.get("device_type") == "energy_monitor" and d.get("datacenter") == dc), None)
        if tmpl is None:
            print(f"  {rpp['name']}: no EV2 template for {dc} — skip.")
            continue
        # Find the template's own RPP to learn the edge orientation.
        tmpl_rpp = next((n["dst"] if n["src"] == tmpl["id"] else n["src"]
                         for n in padj.get(tmpl["id"], []) if dtype(
                             n["dst"] if n["src"] == tmpl["id"] else n["src"]) == "rpp"), None)
        s_key, d_key = ev2_edge_dir(tmpl["id"], tmpl_rpp) if tmpl_rpp else ("src", "dst")

        ev2 = copy.deepcopy(tmpl)
        ev2["id"] = uuid.uuid4().hex[:8]
        ev2["name"] = f"EV2-{dc}-NET-{side}"
        ev2["mgmt_ip"] = alloc_ip(tmpl.get("mgmt_ip") or "192.168.0.0")
        ev2["ip_address"] = ""
        ev2["room"] = rpp.get("room")
        ev2["floor"] = rpp.get("floor")
        ev2["floor_x"] = rpp.get("floor_x")     # co-located on the panel (0U clamp)
        ev2["floor_y"] = rpp.get("floor_y")
        ev2["rack_row"] = rpp.get("rack_row")
        ev2["rack_num"] = rpp.get("rack_num")
        ev2["rack_unit"] = 0
        for ifc in ev2.get("interfaces", []):
            ifc["connected_to_device"] = None
            ifc["connected_to_iface"] = None
        nodes.append({"id": ev2["id"], "position": {"x": 0, "y": 0}, "device": ev2})
        by_id[ev2["id"]] = ev2

        edge = {s_key: ev2["id"], d_key: rpp["id"],
                "src_iface": 0, "dst_iface": 0, "layer": "power"}
        # normalise key order to src/dst
        edge = {"src": edge.get("src", ev2["id"]), "dst": edge.get("dst", rpp["id"]),
                "src_iface": 0, "dst_iface": 0, "layer": "power"}
        if s_key == "dst":   # template had RPP as src, EV2 as dst
            edge["src"], edge["dst"] = rpp["id"], ev2["id"]
        edges.append(edge)
        added += 1
        print(f"  {rpp['name']} <- {ev2['name']} ({ev2['mgmt_ip']})")

    with open(TOPO, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"Done: mounted {added} EV2 meter(s) on the Network Room RPPs.")


if __name__ == "__main__":
    main()
