#!/usr/bin/env python3
"""Promote each DC's Server Hall B from a compute ANNEX into its OWN network pod,
a structural mirror of that DC's Server Hall A.

Before: Hall B has no local spine — its leaves uplink to Hall A's spines (one pod
spanning both halls), and _hall_grid treats it as an annex (compute from row 1,
4 compute rows). After: Hall B owns a full spine set + access-OOB switches on a
reserved network row (row 1), its leaves uplink to ITS spines, those spines uplink
to the DC cores, and Hall-B devices are managed by Hall-B OOB. _hall_grid then
sees a local spine → network-hall grid (3 compute rows × 13 = identical to Hall A).

Per DC this tool:
  1. Reflows existing Hall-B compute (servers, leaves, CDUs, rack PDUs, rack
     sensors) from row 1 → row 2, freeing row 1 for network gear. RPP + EV2 stay.
  2. Adds a network-row rack-PDU pair (A/B), fed by the hall's RPP-A/B.
  3. Adds N spines (clone of Hall A's spine model/count) on row 1, powered by the
     new PDU pair, mgmt'd by a new OOB, uplinked to both DC cores.
  4. Adds the same access-OOB count as Hall A on row 1, uplinked to OOB-CORE-DC*.
  5. Rewires every Hall-B leaf's uplinks off the Hall-A spines onto the Hall-B
     spines (leaf-side port preserved, spine-side port freshly allocated).
  6. Re-homes Hall-B device management edges from Hall-A OOB onto Hall-B OOB.

Idempotent: a hall that already has a local spine is skipped. Run
seed_hall_crahs.py + export_dcim_floorplan.py afterwards.

Usage:
    python tools/promote_hall_b_pod.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import hall_geometry as geo  # noqa: E402

PROD_MASK = 16      # production /16 (10.50.0.0/16)
MGMT_MASK = 22      # mgmt /22


def next_ip(seed: str, used: set) -> str:
    """Next free dotted-quad at/above *seed*, walking the last two octets."""
    if not seed:
        return ""
    a, b, c, d = (int(x) for x in seed.split("."))
    for _ in range(65535):
        d += 1
        if d > 254:
            d = 1; c += 1
        if c > 255:
            c = 0; b += 1
        ip = f"{a}.{b}.{c}.{d}"
        if ip not in used:
            used.add(ip)
            return ip
    return ""


class Ports:
    """Per-device interface-index allocator, seeded from existing edge usage."""
    def __init__(self, edges):
        self.used = {}
        for e in edges:
            self.used.setdefault(e["src"], set()).add(e.get("src_iface", 0))
            self.used.setdefault(e["dst"], set()).add(e.get("dst_iface", 0))

    def alloc(self, nid: str, ifcount: int = 54) -> int:
        s = self.used.setdefault(nid, set())
        n = 1
        while n in s:
            n += 1
        s.add(n)
        return n


def edge(src, dst, layer, si=0, di=0):
    return {"src": src, "dst": dst, "src_iface": si, "dst_iface": di,
            "broken": False, "layer": layer}


def clone_switch(tmpl_node, dc, name, room, floor, rack_num, fx, fy,
                 used_ids, used_prod, used_mgmt):
    """Deep-clone a switch/oob node with a fresh id/name/IPs/placement."""
    new = copy.deepcopy(tmpl_node)
    nid = uuid.uuid4().hex[:8]
    while nid in used_ids:
        nid = uuid.uuid4().hex[:8]
    used_ids.add(nid)
    dv = new["device"]
    new["id"] = nid
    dv["id"] = nid
    dv["name"] = name
    dv["datacenter"] = dc
    dv["room"] = room
    dv["floor"] = floor
    dv["rack_row"] = 1
    dv["rack_num"] = rack_num
    dv["floor_x"] = round(fx, 4)
    dv["floor_y"] = round(fy, 4)
    dv["ip_address"] = next_ip(tmpl_node["device"].get("ip_address") or "", used_prod)
    dv["mgmt_ip"] = next_ip(tmpl_node["device"].get("mgmt_ip") or "", used_mgmt)
    dv["snmp_community"] = dv["mgmt_ip"]
    for iface in dv.get("interfaces", []):
        iface["mac_address"] = ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6])
        iface["connected_to_device"] = None
        iface["connected_to_iface"] = None
    # Canvas coordinates are owned by tools/layout_canvas.py.
    new["position"] = {"x": 0, "y": 0}
    return new


def promote_dc(topo, dc, log):
    nodes = topo["nodes"]
    edges = topo["edges"]
    by_id = {n["id"]: n for n in nodes}
    name_to = {n["device"].get("name"): n["id"] for n in nodes}

    def devs(room=None, dtype=None, pred=None):
        out = []
        for n in nodes:
            d = n["device"]
            if d["datacenter"] != dc:
                continue
            if room is not None and d.get("room") != room:
                continue
            if dtype is not None and d["device_type"] != dtype:
                continue
            if pred is not None and not pred(d):
                continue
            out.append(n)
        return out

    HA, HB = "Server Hall A", "Server Hall B"
    # Already promoted? (local spine in Hall B)
    if devs(HB, "switch", lambda d: "-SP" in (d.get("name") or "")):
        log(f"{dc}: Hall B already has a local spine — skipped")
        return

    ha_spines = devs(HA, "switch", lambda d: "-SP" in (d.get("name") or ""))
    hb_leaves = devs(HB, "switch", lambda d: "-LF" in (d.get("name") or ""))
    ha_oob = devs(HA, "oob_switch", lambda d: "OOB-SW" in (d.get("name") or ""))
    oob_core = devs(dc_room := None, "oob_switch", lambda d: "OOB-CORE" in (d.get("name") or ""))
    cores = devs(None, "switch", lambda d: "-CORE" in (d.get("name") or ""))
    if not (ha_spines and hb_leaves and ha_oob and oob_core and cores):
        log(f"{dc}: missing template gear (spine/leaf/oob/core) — skipped")
        return
    n_sp, n_oob = len(ha_spines), len(ha_oob)
    floor = devs(HB)[0]["device"]["floor"]
    oob_core_id = oob_core[0]["id"]

    used_ids = set(by_id)
    used_prod = {n["device"].get("ip_address") for n in nodes if n["device"].get("ip_address")}
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}
    ports = Ports(edges)

    # ── 1. Reflow Hall-B compute row 1 → row 2 (free row 1 for network gear) ──
    KEEP_ROW1 = {"rpp", "energy_monitor"}          # network power gear stays
    hot, cold, facing = geo.row_aisles(2)
    moved = 0
    for n in devs(HB):
        d = n["device"]
        if d["device_type"] in KEEP_ROW1 or d["device_type"] == "crah":
            continue
        if (d.get("rack_row") or 0) == 1:
            d["rack_row"] = 2
            d["floor_y"] = round(geo.row_y(2), 4)
            d["hot_aisle"], d["cold_aisle"], d["rack_facing"] = hot, cold, facing
            moved += 1

    # ── 2. Network-row PDU pair, fed by the hall's RPP-A/B ──
    rpps = devs(HB, "rpp")
    rpp_a = next((n for n in rpps if (n["device"].get("name") or "").rstrip("012").endswith("A")), rpps[0] if rpps else None)
    rpp_b = next((n for n in rpps if (n["device"].get("name") or "").rstrip("012").endswith("B")), rpps[-1] if rpps else None)
    pdu_tmpl = devs(HB, "pdu")[0]
    new_pdus = {}
    for side, rpp in (("A", rpp_a), ("B", rpp_b)):
        p = copy.deepcopy(pdu_tmpl)
        nid = uuid.uuid4().hex[:8]
        while nid in used_ids:
            nid = uuid.uuid4().hex[:8]
        used_ids.add(nid)
        dvp = p["device"]
        p["id"] = nid; dvp["id"] = nid
        dvp["name"] = f"PDU-{dc}-SHB-R1-1-{side}"
        dvp["room"] = HB; dvp["rack_row"] = 1; dvp["rack_num"] = 1
        dvp["floor_x"] = round(geo.rack_x(1), 4); dvp["floor_y"] = round(geo.row_y(1), 4)
        if dvp.get("ip_address"):
            dvp["ip_address"] = next_ip(pdu_tmpl["device"].get("ip_address") or "", used_prod)
        if dvp.get("mgmt_ip"):
            dvp["mgmt_ip"] = next_ip(pdu_tmpl["device"].get("mgmt_ip") or "", used_mgmt)
            dvp["snmp_community"] = dvp["mgmt_ip"]
        nodes.append(p); by_id[nid] = p; new_pdus[side] = nid
        if rpp is not None:
            edges.append(edge(rpp["id"], nid, "power",
                              ports.alloc(rpp["id"]), ports.alloc(nid)))

    def power_pair(dst_id):
        for side in ("A", "B"):
            edges.append(edge(new_pdus[side], dst_id, "power",
                              ports.alloc(new_pdus[side]), ports.alloc(dst_id)))

    # ── 3. + 4. Spines and access-OOB on row 1 ──
    # SP name sequence continues per-DC (DC1-SP1..4 -> SP5..); OOB likewise.
    import re

    def max_seq(items):
        """Highest trailing-integer in a set of names (handles 'DC1-SP4' and
        'OOB-SW-DC1-05' — the number may be glued to letters or after a dash)."""
        m = 0
        for n in items:
            mt = re.search(r"(\d+)$", n["device"].get("name") or "")
            if mt:
                m = max(m, int(mt.group(1)))
        return m
    sp_i = max_seq(ha_spines)
    oob_i = max_seq(ha_oob)

    new_oob = []
    rack = 8
    for k in range(n_oob):
        oob_i += 1; rack += 1
        o = clone_switch(ha_oob[k % len(ha_oob)], dc, f"OOB-SW-{dc}-{oob_i:02d}",
                         HB, floor, rack, geo.rack_x(rack), geo.row_y(1),
                         used_ids, used_prod, used_mgmt)
        nodes.append(o); by_id[o["id"]] = o; new_oob.append(o["id"])
        edges.append(edge(oob_core_id, o["id"], "management",
                          ports.alloc(oob_core_id), ports.alloc(o["id"])))  # access -> core
        power_pair(o["id"])

    new_spines = []
    for k in range(n_sp):
        sp_i += 1
        s = clone_switch(ha_spines[k % len(ha_spines)], dc, f"{dc}-SP{sp_i}",
                         HB, floor, 2 + k, geo.rack_x(2 + k), geo.row_y(1),
                         used_ids, used_prod, used_mgmt)
        nodes.append(s); by_id[s["id"]] = s; new_spines.append(s["id"])
        power_pair(s["id"])
        edges.append(edge(s["id"], new_oob[k % len(new_oob)], "management",
                          ports.alloc(s["id"]), ports.alloc(new_oob[k % len(new_oob)])))
        for core in cores:                                   # uplink to both DC cores
            edges.append(edge(core["id"], s["id"], "production",
                              ports.alloc(core["id"]), ports.alloc(s["id"])))

    # ── 5. Rewire Hall-B leaves off Hall-A spines onto Hall-B spines ──
    ha_spine_ids = {n["id"] for n in ha_spines}
    rewired = 0
    for lf in hb_leaves:
        lid = lf["id"]
        # capture + drop the leaf's uplink edges to Hall-A spines
        keep = []
        leaf_ports = []
        for e in edges:
            if e.get("layer") == "production" and (
                (e["src"] == lid and e["dst"] in ha_spine_ids) or
                (e["dst"] == lid and e["src"] in ha_spine_ids)):
                leaf_ports.append(e["src_iface"] if e["src"] == lid else e["dst_iface"])
            else:
                keep.append(e)
        edges[:] = keep
        # new uplink to every Hall-B spine (reuse leaf ports where available)
        for j, sid in enumerate(new_spines):
            lp = leaf_ports[j] if j < len(leaf_ports) else ports.alloc(lid)
            edges.append(edge(lid, sid, "production", lp, ports.alloc(sid)))
        rewired += 1

    # ── 6. Re-home Hall-B device management onto Hall-B OOB ──
    ha_oob_ids = {n["id"] for n in ha_oob}
    hb_dev_ids = {n["id"] for n in devs(HB)}
    rehomed = 0
    rr = 0
    for e in edges:
        if e.get("layer") == "management" and e["dst"] in ha_oob_ids and e["src"] in hb_dev_ids:
            e["dst"] = new_oob[rr % len(new_oob)]; rr += 1; rehomed += 1
        elif e.get("layer") == "management" and e["src"] in ha_oob_ids and e["dst"] in hb_dev_ids:
            e["src"] = new_oob[rr % len(new_oob)]; rr += 1; rehomed += 1

    log(f"{dc}: +{n_sp} spines +{n_oob} OOB +2 net-PDU | reflow {moved} devs row1->2 | "
        f"rewired {rewired} leaves | re-homed {rehomed} mgmt edges")


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    dcs = sorted({(n["floorplan"] if False else n["device"]["datacenter"]) for n in topo["nodes"]})
    msgs = []
    for dc in dcs:
        promote_dc(topo, dc, lambda m: msgs.append(m))
    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print("\n".join(msgs))
    print(f"\nWrote {p}")
    print("Next:")
    print(f"  python tools/seed_hall_crahs.py {path}")
    print(f"  python tools/export_dcim_floorplan.py {path} {path.replace('.json','_floorplan.json')}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
