"""
Make dual_dc_enterprise.json (topology source of truth) consistent with the
OOB consolidation so export_dcim_floorplan.py reproduces it.

The consolidation tool removed the 10 PDU nodes and repointed power *edges*, but
left each moved OOB switch's device-level placement fields unchanged:
  - device.rack_num  still 10..14  (export would recreate RACK10-14)
  - device.power_source_a/b still reference the deleted PDU ids
    (export's feed_name() would emit raw ids instead of PDU names)

This patch points the 5 moved switches at RACK9 + the RACK9 PDUs.
Idempotent: re-running is a no-op.
"""
import json
import shutil

TOPO = "topologies/dual_dc_enterprise.json"

# moved OOB switch -> target rack unit in the mgmt rack (RACK9)
MOVED = {
    "94f1c5b0": 40,  # OOB-SW-DC1-01
    "ffe07bcf": 38,  # OOB-SW-DC1-02
    "46aac8d9": 36,  # OOB-SW-DC1-03
    "54c037ab": 34,  # OOB-SW-DC1-04
    "e69c9a56": 32,  # OOB-SW-DC1-05
}
R9_PDU_A = "1931e9ee"   # PDU-DC1-SHA-R1-9-A
R9_PDU_B = "894a05e4"   # PDU-DC1-SHA-R1-9-B


def main():
    topo = json.load(open(TOPO))
    nodes = {n["id"]: n for n in topo["nodes"]}
    changed = 0
    for sid, ru in MOVED.items():
        d = nodes[sid]["device"]
        if d.get("rack_num") == 9 and d.get("power_source_a") == R9_PDU_A:
            continue
        d["rack_num"] = 9
        d["rack_unit"] = ru
        d["power_source_a"] = R9_PDU_A
        d["power_source_b"] = R9_PDU_B
        changed += 1
    if not changed:
        print("Already consistent (no-op).")
        return
    shutil.copy2(TOPO, TOPO + ".bak3")
    json.dump(topo, open(TOPO, "w"), indent=2)
    print(f"Patched {changed} OOB switch nodes -> RACK9 + R9 PDUs.")


if __name__ == "__main__":
    main()
