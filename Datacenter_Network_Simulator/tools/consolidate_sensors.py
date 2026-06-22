#!/usr/bin/env python3
"""Consolidate lone reference sensors onto populated racks (heatmap_design sec 9.3).

A standalone temp/humidity probe (Geist GTHD, APC NetBotz, Raritan DPX2-T3H1)
alone in its own cabinet is unrealistic -- those clip onto a populated rack's
cold-aisle face (~1 reference per row); per-rack sensing otherwise rides the
intelligent PDU. This folds each lone *reference* sensor onto a server rack in
the same hall (zero-U / door mount) and drops the emptied phantom cabinets.

Sensor placement policy:
  * Raritan DPX2-CC2 sub-floor leak sensors -> anchored to a CDU (cooling) rack in
    the same hall: that is where the CHW leak risk is. They are NOT given their own
    cabinet and NOT rack-powered (controller/low-voltage feed).
  * Reference temp/humidity probes -> folded onto a populated server rack.
  * Sensors already sharing a populated rack are left alone.

Edits topology (device emulation) + floor-plan asset file together.
"""
from __future__ import annotations
import json, re, sys
from collections import defaultdict

TOPO = "topologies/dual_dc_enterprise.json"
FLOOR = "topologies/dual_dc_enterprise_floorplan.json"
IT_TYPES = {"server", "switch", "router", "firewall", "load_balancer"}


def hall(r):
    # Room-level (NOT floor-scoped): a hall is one physical room and the
    # floor-plan viewer renders per room. Keying the anchor pool by floor too
    # strands lone sub-floor probes that carry a floor tag with no populated
    # rack on it -> they survive as phantom 1-device racks that collide with
    # the real rack at the same (x,y) on the flattened view.
    return (r["datacenter"], r["room"])


def main():
    topo = json.load(open(TOPO, encoding="utf-8"))
    floor = json.load(open(FLOOR, encoding="utf-8"))

    devById = {d["id"]: d for d in floor["devices"]}
    rackById = {r["rack_id"]: r for r in floor["racks"]}
    members = defaultdict(list)
    for d in floor["devices"]:
        members[d["rack_id"]].append(d)

    # populated racks per hall: server racks (reference probes) and cooling/CDU
    # racks (anchor for sub-floor CHW leak sensors).
    server_racks = defaultdict(list)
    cooling_racks = defaultdict(list)
    for r in floor["racks"]:
        types = {devById[i]["device_type"] for i in r["device_ids"]}
        if "server" in types:
            server_racks[hall(r)].append(r["rack_id"])
        if "cdu" in types:
            cooling_racks[hall(r)].append(r["rack_id"])
    for k in server_racks:
        server_racks[k].sort()
    for k in cooling_racks:
        cooling_racks[k].sort()

    # lone sensors = single-device sensor racks. CC2 = sub-floor leak probe ->
    # anchor to a CDU rack; everything else = reference probe -> a server rack.
    lone = [(rid, ds[0]) for rid, ds in members.items()
            if len(ds) == 1 and ds[0]["device_type"] == "sensor"]

    rr = defaultdict(int)
    moves = {}   # sensor id -> target rack_id
    for rid, sensor in lone:
        h = hall(rackById[rid])
        leak = sensor.get("model") == "Raritan DPX2-CC2"
        pool = (cooling_racks.get(h) or server_racks.get(h)) if leak \
               else (server_racks.get(h) or cooling_racks.get(h))
        if not pool:
            continue   # no anchor rack in this hall -> leave it
        tgt = pool[rr[(h, leak)] % len(pool)]
        rr[(h, leak)] += 1
        moves[sensor["id"]] = tgt

    # CHW leak probes are spot sensors in each CDU's drip pan -> anchor every
    # `<dc>-CDUn-LEAK` sensor to its matching `CDU-<dc>-n` rack (not piled under
    # one unrelated cabinet). Name-matched so each probe sits at its real CDU.
    cdu_rack = {}
    for dev in floor["devices"]:
        if dev["device_type"] == "cdu":
            m = re.match(r"CDU-(.+)-(\d+)$", dev.get("name") or "")
            if m:
                cdu_rack[(m.group(1), m.group(2))] = dev["rack_id"]
    for dev in floor["devices"]:
        if dev["device_type"] != "sensor":
            continue
        m = re.search(r"-(DC\d+)-CDU(\d+)", dev.get("name") or "")
        if m and "LEAK" in (dev.get("name") or "").upper():
            tgt = cdu_rack.get((m.group(1), m.group(2)))
            if tgt and dev["rack_id"] != tgt:
                moves[dev["id"]] = tgt

    # --- even out underfloor probes: a CDU rack realistically carries at most a
    # sub-floor temp probe + its own CDU leak probe (2). Spread any surplus
    # NON-leak probe to the least-loaded cooling rack in the same room (servers
    # only if every cooling rack is full) so pucks don't pile under one cabinet.
    def eff(d):
        return moves.get(d["id"], d["rack_id"])
    def is_uf(d):
        return d["device_type"] == "sensor" and d.get("model") == "Raritan DPX2-CC2"
    def is_leak(d):
        return "LEAK" in (d.get("name") or "").upper()
    uf_by_rack = defaultdict(list)
    for dev in floor["devices"]:
        if is_uf(dev):
            uf_by_rack[eff(dev)].append(dev)
    load = {rid: len(v) for rid, v in uf_by_rack.items()}
    for rid in list(uf_by_rack):
        room = hall(rackById[rid])
        surplus = [d for d in uf_by_rack[rid] if not is_leak(d)]
        while load.get(rid, 0) > 2 and surplus:
            probe = surplus.pop()
            pool = [a for a in (cooling_racks.get(room) or [])
                    if a != rid and load.get(a, 0) < 2]
            if not pool:
                pool = [a for a in (server_racks.get(room) or [])
                        if a != rid and load.get(a, 0) < 2]
            if not pool:
                break   # nowhere with room -> leave it
            tgt = min(pool, key=lambda a: (load.get(a, 0), a))
            moves[probe["id"]] = tgt
            load[rid] -= 1
            load[tgt] = load.get(tgt, 0) + 1

    # --- floor-plan: re-point devices, set zero-U mount ---
    for dev in floor["devices"]:
        if dev["id"] in moves:
            dev["rack_id"] = moves[dev["id"]]
            dev["rack_unit"] = 0          # zero-U: door / rail / aisle-face mount

    # --- tag environmental sensor mounting (floor-void vs cold-aisle face) ---
    # Real DCIM models the raised-floor plenum as a VIEW LAYER, not a room: CHW
    # leak / sub-floor probes (Raritan DPX2-CC2) live UNDER the tiles; reference
    # temp/humidity probes clip to the cold-aisle (front) face of a populated
    # rack. The viewer renders `underfloor` sensors down in the plenum void
    # (Cutaway toggle) and `rack_front` sensors on the rack's intake face.
    def mounting_for(model):
        return "underfloor" if model == "Raritan DPX2-CC2" else "rack_front"
    for dev in floor["devices"]:
        if dev["device_type"] == "sensor":
            dev["mounting"] = mounting_for(dev.get("model"))

    # rebuild membership + derived fields, drop emptied racks
    members = defaultdict(list)
    for dev in floor["devices"]:
        members[dev["rack_id"]].append(dev)
    kept = []
    for r in floor["racks"]:
        devs = members.get(r["rack_id"], [])
        if not devs:
            continue
        r["device_ids"] = [d["id"] for d in devs]
        r["device_count"] = len(devs)
        r["it_power_draw_w"] = sum(d.get("power_draw_w") or 0 for d in devs)
        kept.append(r)
    removed = len(floor["racks"]) - len(kept)
    floor["racks"] = kept
    floor["summary"]["racks"] = len(kept)
    json.dump(floor, open(FLOOR, "w", encoding="utf-8"), indent=2)

    # --- topology: mirror the move (rack_row/rack_num from target rack) ---
    tgt_coords = {}   # rack_id -> (row, num)
    for r in kept:
        tgt_coords[r["rack_id"]] = (r["row"], r["rack_num"])
    for n in topo["nodes"]:
        d = n["device"]
        if d.get("id") in moves:
            row, num = tgt_coords[moves[d["id"]]]
            d["rack_row"], d["rack_num"], d["rack_unit"] = row, num, 0
        if d.get("device_type") == "sensor":
            d["mounting"] = mounting_for(d.get("model"))
    json.dump(topo, open(TOPO, "w", encoding="utf-8"), indent=2)

    print(f"Consolidated {len(moves)} lone reference sensors onto populated racks")
    print(f"Removed {removed} emptied phantom racks; floor-plan racks now {len(kept)}")
    for sid, tgt in sorted(moves.items(), key=lambda kv: devById[kv[0]]["name"]):
        print(f"  {devById[sid]['name']:16} ({devById[sid].get('model')}) -> {tgt}")


if __name__ == "__main__":
    sys.exit(main())
