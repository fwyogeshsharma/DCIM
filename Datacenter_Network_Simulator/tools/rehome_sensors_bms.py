#!/usr/bin/env python3
"""Re-home server-hall environmental sensors onto the hall's BMS OOB switch.

Environmental sensors (rack/aisle temp + humidity) feed the cooling-control loop,
so they belong on the BMS/facility network with the CRAHs — not the IT OOB. This
re-points every server-hall SENSOR whose management link lands on an IT OOB onto
that hall's BMS switch (OOBM…, created by add_bms_oob.py). Plant sensors already
sit on the Central Plant facility OOB and are left alone.

Idempotent. Run export_dcim_floorplan.py + fix_edge_directions.py afterwards.

Usage:
    python tools/rehome_sensors_bms.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    N = {n["id"]: n["device"] for n in topo["nodes"]}
    E = topo["edges"]
    NM = lambda i: N[i].get("name") or ""

    # BMS switch per (dc, room)
    bms = {(N[i]["datacenter"], N[i].get("room")): i
           for i in N if NM(i).startswith("OOBM")}

    def is_it_oob(i):
        return N[i]["device_type"] == "oob_switch" and NM(i).startswith("OOB") \
            and not NM(i).startswith("OOBM") and not NM(i).startswith("OOBC")

    def is_hall_sensor(i):
        return N[i]["device_type"] == "sensor" \
            and (N[i].get("room") or "").startswith("Server Hall")

    moved = 0
    for e in E:
        if e.get("layer") != "management":
            continue
        s, d = e["src"], e["dst"]
        # sensor <-> IT OOB, either orientation
        if is_hall_sensor(s) and is_it_oob(d):
            sensor, oob_end = s, "dst"
        elif is_hall_sensor(d) and is_it_oob(s):
            sensor, oob_end = d, "src"
        else:
            continue
        tgt = bms.get((N[sensor]["datacenter"], N[sensor].get("room")))
        if not tgt:
            continue
        e[oob_end] = tgt
        moved += 1

    # drop any duplicate/self-loop the re-point may have created
    seen, clean = set(), []
    for e in E:
        if e["src"] == e["dst"]:
            continue
        k = (e["src"], e["dst"], e.get("layer"))
        if k in seen:
            continue
        seen.add(k); clean.append(e)
    topo["edges"] = clean

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Re-homed {moved} server-hall sensors onto their BMS switch. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
