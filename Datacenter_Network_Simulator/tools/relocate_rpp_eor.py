#!/usr/bin/env python3
"""Relocate IT RPPs + their Verdigris energy monitors to end-of-row positions.

RPPs (Remote Power Panels, APC Galaxy RPP) are floor/wall power panels fed from
the UPS that breaker out branch circuits to the rack PDUs -- they belong at the
END OF THE ROW they feed, not inside a server rack. The Verdigris EV2 energy
monitor clamps CTs onto the RPP's branch breakers, so it co-locates WITH its RPP.

Per server hall: the A/B RPP pair (+ one EV2 each) move to two new cells at the
end of the server row (just past the last server rack). Facility RPPs/EV2 in the
Mechanical / Central Plant / Generator rooms are already correctly placed and are
left alone. rack_unit stays 0 (floor/wall-standing).
"""
import json, sys
from collections import defaultdict

TOPO = "topologies/dual_dc_enterprise.json"
FLOOR = "topologies/dual_dc_enterprise_floorplan.json"

floor = json.load(open(FLOOR, encoding="utf-8"))
topo = json.load(open(TOPO, encoding="utf-8"))
rooms = floor["floorplan"]["rooms"]
rackById = {r["rack_id"]: r for r in floor["racks"]}
devById = {d["id"]: d for d in floor["devices"]}
topoDev = {n["device"]["id"]: n["device"] for n in topo["nodes"]}
PITCH = floor["floorplan"].get("rack_pitch", 0.6)
ROWP = floor["floorplan"].get("row_pitch", 2.4)

def is_hall(room): return "Server Hall" in room

# per server hall: RPPs, EV2s, server row, last server rack_num, existing nums in row
rpps = defaultdict(list); ev2s = defaultdict(list)
for d in floor["devices"]:
    r = rackById[d["rack_id"]]
    if not is_hall(r["room"]):
        continue
    key = (r["datacenter"], r["room"], r["floor"])
    if d["device_type"] == "rpp":
        rpps[key].append(d)
    elif d["device_type"] == "energy_monitor":
        ev2s[key].append(d)

server_row = {}; last_num = {}; nums_in_row = defaultdict(set)
for r in floor["racks"]:
    key = (r["datacenter"], r["room"], r["floor"])
    if any(devById[i]["device_type"] == "server" for i in r["device_ids"]):
        server_row[key] = r["row"]
        last_num[key] = max(last_num.get(key, 0), r["rack_num"])
for r in floor["racks"]:
    nums_in_row[(r["datacenter"], r["room"], r["floor"], r["row"])].add(r["rack_num"])

new_racks = []
for key in sorted(rpps):
    dc, room, fl = key
    srow = server_row.get(key)
    if srow is None:
        continue
    slug = room.replace(" ", "")
    fy = round((srow - 1) * ROWP + PITCH, 2)
    used = nums_in_row[(dc, room, fl, srow)]
    rpp_list = sorted(rpps[key], key=lambda d: d["name"])      # A before B
    ev_list = sorted(ev2s.get(key, []), key=lambda d: d["name"])
    nxt = last_num[key] + 1
    for idx, rpp in enumerate(rpp_list):
        while nxt in used:
            nxt += 1
        num = nxt; used.add(num); nxt += 1
        cell = [rpp] + ([ev_list[idx]] if idx < len(ev_list) else [])
        rid = f"{dc}:{slug}:F{fl}:R{srow}:RACK{num}"
        src = rackById[rpp["rack_id"]]
        for d in cell:
            d["rack_id"] = rid
            nd = topoDev.get(d["id"])
            if nd:
                nd["rack_row"], nd["rack_num"], nd["rack_unit"] = srow, num, 0
        new_racks.append({
            "rack_id": rid, "datacenter": dc,
            "datacenter_city": src.get("datacenter_city"),
            "room": room, "floor": fl, "row": srow, "rack_num": num,
            "floor_x": round((num - 1) * PITCH + PITCH / 2, 2), "floor_y": fy,
            "rack_facing": None, "cold_aisle": None, "hot_aisle": None,
            "device_ids": [d["id"] for d in cell], "device_count": len(cell),
            "it_power_draw_w": sum(d.get("power_draw_w") or 0 for d in cell),
        })

# rebuild old racks (drop emptied), append new end-of-row cells
members = defaultdict(list)
for d in floor["devices"]:
    members[d["rack_id"]].append(d)
kept = []
for r in floor["racks"]:
    ds = members.get(r["rack_id"], [])
    if not ds:
        continue
    r["device_ids"] = [x["id"] for x in ds]
    r["device_count"] = len(ds)
    r["it_power_draw_w"] = sum(x.get("power_draw_w") or 0 for x in ds)
    kept.append(r)
removed = len(floor["racks"]) - len(kept)
floor["racks"] = kept + new_racks
floor["summary"]["racks"] = len(floor["racks"])

json.dump(floor, open(FLOOR, "w", encoding="utf-8"), indent=2)
json.dump(topo, open(TOPO, "w", encoding="utf-8"), indent=2)
print(f"Relocated RPPs+monitors to end-of-row: {len(new_racks)} cells")
print(f"Removed {removed} emptied racks; floor-plan racks now {len(floor['racks'])}")
for r in new_racks:
    names = [devById[i]["name"] for i in r["device_ids"]]
    print(f"  {r['rack_id']:34} ({r['floor_x']},{r['floor_y']}) {names}")
