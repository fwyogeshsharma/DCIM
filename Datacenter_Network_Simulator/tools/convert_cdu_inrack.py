#!/usr/bin/env python3
"""One-shot migration: floor-standing CDUs -> in-rack 4U CDUs.

Converts the Vertiv Liebert XDU 1350 (floor-standing, ~1.35 MW, each occupying
its own rack position) into CoolIT CHx80-class in-rack CDUs (4U, ~80 kW liquid-
to-liquid) mounted INSIDE a liquid-cooled server rack in the same hall.

Edits both the simulator topology (device emulation) and the DCIM floor-plan
asset file, keeping them consistent:
  * topology nodes: retag model/vendor/power, move into a server rack at U38.
  * floor-plan: re-point the CDU device to the server rack, recompute per-rack
    device counts / IT load, and drop CDU-only racks that become empty.

Each CDU lands in the Nth server rack of its hall (N = its order in the hall),
at U38 (the free band above the top server, below the U42 ToR switch).
"""
from __future__ import annotations
import json, sys
from collections import defaultdict

TOPO = "topologies/dual_dc_enterprise.json"
FLOOR = "topologies/dual_dc_enterprise_floorplan.json"

NEW_MODEL  = "CoolIT CHx80"
NEW_VENDOR = "CoolIT Systems"
NEW_PW     = 750     # pump-only electrical draw for a 4U in-rack CDU (W)
NEW_RU     = 38      # 4U unit occupies U38-41; switch sits at U42


def server_rack_key(dc, room, floor, row, num):
    return (dc, room, str(floor), row, num)


def main():
    topo = json.load(open(TOPO, encoding="utf-8"))
    floor = json.load(open(FLOOR, encoding="utf-8"))

    # --- identify CDUs and the server racks available per hall (topology) ---
    nodes = topo["nodes"]
    cdus = [n["device"] for n in nodes if n["device"].get("device_type") == "cdu"]
    server_racks = defaultdict(set)   # (dc,room,floor) -> {(row,num)}
    for n in nodes:
        d = n["device"]
        if d.get("device_type") == "server":
            server_racks[(d["datacenter"], d["room"], str(d.get("floor")))].add(
                (d.get("rack_row"), d.get("rack_num")))

    # assign each CDU to the Nth server rack (sorted) of its hall, by CDU name
    by_hall = defaultdict(list)
    for d in cdus:
        by_hall[(d["datacenter"], d["room"], str(d.get("floor")))].append(d)

    target = {}   # cdu name -> (row, num)
    for hall, group in by_hall.items():
        srv = sorted(server_racks.get(hall, []))
        if not srv:
            print(f"!! no server racks in {hall}; skipping {[d['name'] for d in group]}")
            continue
        for i, d in enumerate(sorted(group, key=lambda x: x["name"])):
            target[d["name"]] = srv[i % len(srv)]

    # --- edit topology nodes ---
    for d in cdus:
        if d["name"] not in target:
            continue
        row, num = target[d["name"]]
        d["rack_row"], d["rack_num"], d["rack_unit"] = row, num, NEW_RU
        d["model_name"] = NEW_MODEL
        d["vendor"] = NEW_VENDOR
        d["power_draw_w"] = NEW_PW
    json.dump(topo, open(TOPO, "w", encoding="utf-8"), indent=2)

    # --- edit floor-plan asset file ---
    racks = {r["rack_id"]: r for r in floor["racks"]}
    # index server racks by hall coords -> rack_id
    rid_by_key = {}
    for r in floor["racks"]:
        rid_by_key[server_rack_key(r["datacenter"], r["room"], r["floor"],
                                   r["row"], r["rack_num"])] = r["rack_id"]

    moved = 0
    for dev in floor["devices"]:
        if dev.get("device_type") != "cdu" or dev["name"] not in target:
            continue
        dc = racks[dev["rack_id"]]["datacenter"]
        room = racks[dev["rack_id"]]["room"]
        fl = racks[dev["rack_id"]]["floor"]
        row, num = target[dev["name"]]
        tgt = rid_by_key.get(server_rack_key(dc, room, fl, row, num))
        if not tgt:
            print(f"!! no floor-plan rack for {dev['name']} -> {(dc,room,fl,row,num)}")
            continue
        dev["rack_id"] = tgt
        dev["rack_unit"] = NEW_RU
        dev["model"] = NEW_MODEL
        dev["vendor"] = NEW_VENDOR
        dev["power_draw_w"] = NEW_PW
        moved += 1

    # rebuild rack membership / derived fields from the (edited) device list
    members = defaultdict(list)
    for dev in floor["devices"]:
        members[dev["rack_id"]].append(dev)
    kept = []
    for r in floor["racks"]:
        devs = members.get(r["rack_id"], [])
        if not devs:
            continue   # drop now-empty CDU-only racks
        r["device_ids"] = [d["id"] for d in devs]
        r["device_count"] = len(devs)
        r["it_power_draw_w"] = sum(d.get("power_draw_w") or 0 for d in devs)
        kept.append(r)
    removed = len(floor["racks"]) - len(kept)
    floor["racks"] = kept
    floor["summary"]["racks"] = len(kept)
    json.dump(floor, open(FLOOR, "w", encoding="utf-8"), indent=2)

    print(f"Converted {moved} CDUs -> in-rack {NEW_MODEL} @ U{NEW_RU}")
    print(f"Removed {removed} now-empty CDU racks; floor-plan racks now {len(kept)}")
    for name, (row, num) in sorted(target.items()):
        print(f"  {name:14} -> row{row} rack{num} U{NEW_RU}")


if __name__ == "__main__":
    sys.exit(main())
