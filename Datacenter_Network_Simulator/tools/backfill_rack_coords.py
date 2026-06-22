"""
Backfill physical placement from the post-processed asset file
(dual_dc_enterprise_floorplan.json) back into the topology source of truth
(dual_dc_enterprise.json), so export_dcim_floorplan.py reproduces the asset
file exactly.

Why this drift exists: several tools (relocate_pdus_inrack, relocate_crah_perimeter,
perimeter-aisle tools, the OOB consolidation) wrote rack coordinates / facing /
aisle assignment and room geometry ONLY into the floorplan JSON. The topology
nodes never carried floor_x/floor_y/aisle, so export emitted None for them.

Authoritative drift (measured): rack-level floor_x, floor_y, rack_facing,
cold_aisle, hot_aisle, plus the per-room geometry block. Device assignment,
rack_unit, power feeds and power draw already match and are left untouched.

Backfill:
  * each node gets its rack's floor_x/floor_y/rack_facing/cold_aisle/hot_aisle
    (export reads these from the device that first creates the rack).
  * topology floorplan geometry block <- floorplan-file geometry block.
Idempotent.
"""
import json
import shutil

TOPO = "topologies/dual_dc_enterprise.json"
FP = "topologies/dual_dc_enterprise_floorplan.json"

PLACE = ["floor_x", "floor_y", "rack_facing", "cold_aisle", "hot_aisle"]


def main():
    topo = json.load(open(TOPO))
    fp = json.load(open(FP))

    # device id -> its rack record (placement source of truth)
    dev_rack = {}
    for r in fp["racks"]:
        for did in r["device_ids"]:
            dev_rack[did] = r

    touched = 0
    for n in topo["nodes"]:
        did = n["device"].get("id", n["id"])
        r = dev_rack.get(did)
        if r is None:
            continue
        dev = n["device"]
        changed = False
        for f in PLACE:
            if dev.get(f) != r.get(f):
                dev[f] = r.get(f)
                changed = True
        touched += changed

    # sync per-room geometry block (rooms, aisles, extents, pitches)
    geom_changed = topo.get("floorplan") != fp["floorplan"]
    topo["floorplan"] = fp["floorplan"]

    if not touched and not geom_changed:
        print("Already consistent (no-op).")
        return

    shutil.copy2(TOPO, TOPO + ".bak4")
    json.dump(topo, open(TOPO, "w"), indent=2)
    print(f"Backfilled placement on {touched} nodes; geometry synced={geom_changed}.")


if __name__ == "__main__":
    main()
