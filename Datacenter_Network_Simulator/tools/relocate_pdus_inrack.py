#!/usr/bin/env python3
"""Re-architect rack power to the real model: 2 in-rack PDUs (A+B) per IT rack.

Replaces the cross-fed pool of 42 shared PDUs with a dedicated A-side + B-side
zero-U vertical PDU mounted INSIDE every IT rack; every powered device in the
rack is dual-corded to its own rack's A/B PDUs. Power chain stays three-tier:

    RPP-IT-<DC>-A<hall>  -->  rack A-PDU  -->  device  (cord A)
    RPP-IT-<DC>-B<hall>  -->  rack B-PDU  -->  device  (cord B)

Each new PDU also gets an OOB management uplink (PDU eth0 -> OOB switch port).

Mgmt addressing: the OOB net is grown from /24 to /23 (192.168.0.0/23) because
2-per-rack needs ~92 PDUs and the /24 had only 15 free hosts.

Edits topology (nodes + power/management edges) and the DCIM floor-plan asset
file, then the viewer is regenerated separately.
"""
from __future__ import annotations
import json, os, sys, random, copy
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._power_edges import fed_by  # noqa: E402

random.seed(42)   # deterministic ids/macs/ip ordering
TOPO = "topologies/dual_dc_enterprise.json"
FLOOR = "topologies/dual_dc_enterprise_floorplan.json"

A_VENDOR, A_MODEL = "APC by Schneider Electric", "APC AP8681"
B_VENDOR, B_MODEL = "Raritan", "Raritan PX3-5190R"
ROOM_SLUG = {"Server Hall A": "SHA", "Server Hall B": "SHB"}


def hexid(existing):
    while True:
        h = "%08x" % random.randrange(16**8)
        if h not in existing:
            existing.add(h); return h


def macaddr():
    return ":".join("%02x" % random.randrange(256) for _ in range(6))


def main():
    topo = json.load(open(TOPO, encoding="utf-8"))
    floor = json.load(open(FLOOR, encoding="utf-8"))
    nodes = topo["nodes"]
    dev = {n["device"]["id"]: n["device"] for n in nodes}
    nodeOf = {n["device"]["id"]: n for n in nodes}
    old_pdu = {i for i, d in dev.items() if d["device_type"] == "pdu"}
    template = copy.deepcopy(nodeOf[next(iter(old_pdu))])

    # RPP A/B per (dc, room) from the IT RPP nodes (name RPP-IT-<DC>-<A|B><n>)
    rpp_ab = {}   # (dc, room) -> {"A": id, "B": id}
    for d in dev.values():
        if d["device_type"] == "rpp" and "-IT-" in d["name"]:
            side = "A" if d["name"].rsplit("-", 1)[-1].startswith("A") else "B"
            rpp_ab.setdefault((d["datacenter"], d["room"]), {})[side] = d["id"]

    # OOB switches per (dc, room) + per-DC fallback, with a free-port counter
    oob_by_room = defaultdict(list); oob_by_dc = defaultdict(list)
    for d in dev.values():
        if d["device_type"] == "oob_switch":
            oob_by_room[(d["datacenter"], d["room"])].append(d["id"])
            oob_by_dc[d["datacenter"]].append(d["id"])
    oob_port = defaultdict(lambda: 20)   # next free mgmt port per OOB id

    # mgmt IP pool: free hosts in 192.168.0.0/23 (skip everything already used)
    #
    # HISTORICAL. This migration ran once, against an estate numbered out of
    # 192.168/16. That plane is gone — see tools/renumber_mgmt_plane.py — because
    # 192.168.1.0/24 collided with the host's own LAN and one device claimed the
    # default gateway. This pool is hard-coded rather than derived, so re-running
    # this script against a renumbered topology would quietly inject 192.168
    # addresses back into it and recreate the collision. Refuse instead.
    if not any((d.get("mgmt_ip") or "").startswith("192.168.") for d in dev.values()):
        raise SystemExit(
            "This topology is not on the 192.168 management plane — it has been\n"
            "renumbered (see tools/renumber_mgmt_plane.py). This one-shot migration\n"
            "hard-codes a 192.168.0.0/23 pool and would reintroduce the host-LAN\n"
            "collision it predates. Port it to the current plane before re-running."
        )
    used_ip = set()
    for d in dev.values():
        for k in ("mgmt_ip", "snmp_community", "ip_address"):
            v = d.get(k) or ""
            if v.startswith("192.168."):
                used_ip.add(v)
    ip_pool = [f"192.168.{b}.{h}" for b in (0, 1) for h in range(2, 255)
               if f"192.168.{b}.{h}" not in used_ip]
    ip_iter = iter(ip_pool)

    used_ids = set(dev.keys())

    # target racks = racks holding >=1 device currently fed by a PDU. Read from the
    # CORDS — there is no power_source_a/b field to ask (it was a cache of exactly this
    # and it drifted; see tools/_power_edges.py and Device in core/device_manager.py).
    fed_devices = fed_by(topo, old_pdu, dev)
    def rkey(d): return (d["datacenter"], d["room"], str(d.get("floor")), d.get("rack_row"), d.get("rack_num"))
    target_racks = {}
    for d in fed_devices:
        target_racks.setdefault(rkey(d), []).append(d)

    new_nodes, new_edges = [], []
    rack_pdu = {}   # rkey -> (A_id, B_id, A_name, B_name)
    warnings = []

    for rk, members in sorted(target_racks.items()):
        dc, room, fl, row, num = rk
        sample = members[0]
        slug = ROOM_SLUG.get(room, room.replace(" ", "")[:4].upper())
        ab = rpp_ab.get((dc, room)) or rpp_ab.get((dc, "Server Hall A")) or {}
        rpp_a, rpp_b = ab.get("A"), ab.get("B")
        if not (rpp_a and rpp_b):
            warnings.append(f"no RPP A/B for {rk}; upstream left blank")

        made = {}
        for side, vendor, model, rpp in (("A", A_VENDOR, A_MODEL, rpp_a),
                                          ("B", B_VENDOR, B_MODEL, rpp_b)):
            nd = copy.deepcopy(template)
            d = nd["device"]
            nid = hexid(used_ids)
            ip = next(ip_iter)
            d.update({
                "id": nid, "name": f"PDU-{dc}-{slug}-R{row}-{num}-{side}",
                "vendor": vendor, "model_name": model,
                "ip_address": "", "mgmt_ip": ip, "snmp_community": ip,
                "datacenter": dc, "room": room, "floor": fl,
                "datacenter_city": sample.get("datacenter_city"),
                "country": sample.get("country"),
                "rack_row": row, "rack_num": num, "rack_unit": 0,
                "power_draw_w": 0,
            })
            nd["id"] = nid
            nd["position"] = {"x": 0, "y": 0}   # placed by tools/layout_canvas.py
            d["interfaces"] = [dict(template["device"]["interfaces"][0],
                                    mac_address=macaddr(),
                                    connected_to_device=None, connected_to_iface=None)]
            new_nodes.append(nd); made[side] = (nid, d["name"])
            # upstream RPP -> PDU
            if rpp:
                new_edges.append({"src": rpp, "dst": nid, "src_iface": 0,
                                  "dst_iface": 0, "layer": "power"})
            # mgmt PDU -> OOB switch (same room, else same DC)
            pool = oob_by_room.get((dc, room)) or oob_by_dc.get(dc) or []
            if pool:
                oid = pool[(row + num + (0 if side == "A" else 1)) % len(pool)]
                port = oob_port[oid]; oob_port[oid] += 1
                new_edges.append({"src": nid, "dst": oid, "src_iface": 0,
                                  "dst_iface": port, "layer": "management"})
        rack_pdu[rk] = (made["A"][0], made["B"][0], made["A"][1], made["B"][1])

    # Re-cord every fed device onto its rack's A/B PDU. The edges ARE the record —
    # there is no field to repoint alongside them.
    for rk, members in target_racks.items():
        a_id, b_id, *_ = rack_pdu[rk]
        for d in members:
            for pid in (a_id, b_id):
                new_edges.append({"src": pid, "dst": d["id"], "src_iface": 0,
                                  "dst_iface": 0, "layer": "power"})

    # drop old PDU nodes + ALL their edges; add the new ones
    topo["nodes"] = [n for n in nodes if n["device"]["id"] not in old_pdu] + new_nodes
    topo["edges"] = [e for e in topo["edges"]
                     if e["src"] not in old_pdu and e["dst"] not in old_pdu] + new_edges
    json.dump(topo, open(TOPO, "w", encoding="utf-8"), indent=2)

    # ---- floor-plan asset file ----
    rackById = {r["rack_id"]: r for r in floor["racks"]}
    rid_by_key = {(r["datacenter"], r["room"], r["floor"], r["row"], r["rack_num"]): r["rack_id"]
                  for r in floor["racks"]}
    name_by_id = {d["id"]: d["name"] for d in floor["devices"]}
    # remove old PDU device entries
    floor["devices"] = [d for d in floor["devices"] if d["id"] not in old_pdu]
    # add new PDU device entries + repoint fed devices' feed names
    for rk, (a_id, b_id, a_name, b_name) in rack_pdu.items():
        dc, room, fl, row, num = rk
        tgt = rid_by_key.get((dc, room, fl, row, num))
        for nid, name, vendor, model in ((a_id, a_name, A_VENDOR, A_MODEL),
                                         (b_id, b_name, B_VENDOR, B_MODEL)):
            floor["devices"].append({
                "id": nid, "name": name, "device_type": "pdu", "vendor": vendor,
                "model": model, "rack_id": tgt, "rack_unit": 0, "power_draw_w": 0,
                "feed_a": None, "feed_b": None})
    fed_ids = {d["id"] for ms in target_racks.values() for d in ms}
    for d in floor["devices"]:
        if d["id"] in fed_ids:
            rk = next(k for k, ms in target_racks.items() if any(m["id"] == d["id"] for m in ms))
            d["feed_a"], d["feed_b"] = rack_pdu[rk][2], rack_pdu[rk][3]
    # rebuild racks
    members = defaultdict(list)
    for d in floor["devices"]:
        members[d["rack_id"]].append(d)
    kept = []
    for r in floor["racks"]:
        ds = members.get(r["rack_id"], [])
        if not ds:
            continue
        r["device_ids"] = [d["id"] for d in ds]
        r["device_count"] = len(ds)
        r["it_power_draw_w"] = sum(d.get("power_draw_w") or 0 for d in ds)
        kept.append(r)
    removed = len(floor["racks"]) - len(kept)
    floor["racks"] = kept
    floor["summary"]["racks"] = len(kept)
    floor["summary"]["devices"] = len(floor["devices"])
    json.dump(floor, open(FLOOR, "w", encoding="utf-8"), indent=2)

    print(f"Provisioned {len(new_nodes)} in-rack PDUs across {len(rack_pdu)} IT racks "
          f"(was {len(old_pdu)} shared)")
    print(f"Repointed {len(fed_ids)} devices to their rack A/B PDUs")
    print(f"Rebuilt power+mgmt edges; topology edges now {len(topo['edges'])}")
    print(f"Removed {removed} emptied PDU racks; floor-plan racks now {len(kept)}")
    print(f"mgmt IPs assigned from 192.168.0.0/23 ({len(ip_pool)} free in pool)")
    for w in warnings[:5]:
        print("  WARN:", w)


if __name__ == "__main__":
    sys.exit(main())
