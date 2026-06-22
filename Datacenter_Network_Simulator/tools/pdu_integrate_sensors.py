#!/usr/bin/env python3
"""Option B: per-rack environmental sensing rides the intelligent PDU.

Real datacenters read per-rack inlet temp / humidity from the rack PDU's onboard
probe ports (every rack here has A+B metered PDUs). Standalone ambient probes are
redundant. The simulator already generates pdu_temperature / pdu_humidity per PDU
and exposes them via DeviceInfo, so removing the redundant sensors loses nothing.

Cut:
  * REMOVE every standalone ambient probe: Vertiv Geist GTHD, APC NetBotz 250/355
    (-> per-rack reading now comes from the PDU probe).
  * THIN Raritan DPX2-T3H1 to ONE 3-height reference per server-hall row
    (ASHRAE 3-height profile; the PDU gives only a single point).
KEEP:
  * Raritan DPX2-CC2 leak sensors (sub-floor + CDU loop).
  * Plant sensors (CHW/CW temp, flow, basin -- BMS instrumentation).

Edits topology + floor-plan together; drops emptied racks. Sensors are leaf
nodes (no topology edges), so node removal needs no edge pruning.
"""
from __future__ import annotations
import json, sys
from collections import defaultdict

TOPO = "topologies/dual_dc_enterprise.json"
FLOOR = "topologies/dual_dc_enterprise_floorplan.json"

DROP_MODELS = {"Vertiv Geist GTHD", "APC NetBotz 355", "APC NetBotz 250"}
REF_MODEL = "Raritan DPX2-T3H1"


def keep_sensor(dev, kept_ref_rooms):
    """Decide whether a sensor device survives the PDU-integration cut."""
    model = dev.get("model") or dev.get("model_name") or ""
    if model in DROP_MODELS:
        return False
    if model == REF_MODEL:
        room = (dev["_dc"], dev["_room"])          # one 3-height reference per hall
        if room in kept_ref_rooms:
            return False
        kept_ref_rooms.add(room)
        return True
    return True   # CC2 leak + plant sensors stay


def main():
    topo = json.load(open(TOPO, encoding="utf-8"))
    floor = json.load(open(FLOOR, encoding="utf-8"))

    rackById = {r["rack_id"]: r for r in floor["racks"]}

    # decide kept/removed using floor-plan device list (has rack -> room)
    kept_ref_rooms = set()
    remove_ids = set()
    # process T3H1 deterministically by name so "first per hall" is stable
    devs = sorted(floor["devices"], key=lambda d: d["name"])
    for dev in devs:
        if dev["device_type"] != "sensor":
            continue
        r = rackById[dev["rack_id"]]
        dev["_dc"], dev["_room"] = r["datacenter"], r["room"]
        if not keep_sensor(dev, kept_ref_rooms):
            remove_ids.add(dev["id"])
    for dev in floor["devices"]:
        dev.pop("_dc", None); dev.pop("_room", None)

    # --- floor-plan: drop removed sensor devices, rebuild racks ---
    floor["devices"] = [d for d in floor["devices"] if d["id"] not in remove_ids]
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
    removed_racks = len(floor["racks"]) - len(kept)
    floor["racks"] = kept
    floor["summary"]["racks"] = len(kept)
    floor["summary"]["devices"] = len(floor["devices"])
    json.dump(floor, open(FLOOR, "w", encoding="utf-8"), indent=2)

    # --- topology: drop the same sensor nodes (no edges touch them) ---
    before = len(topo["nodes"])
    topo["nodes"] = [n for n in topo["nodes"] if n["device"].get("id") not in remove_ids]
    json.dump(topo, open(TOPO, "w", encoding="utf-8"), indent=2)

    print(f"Removed {len(remove_ids)} standalone sensors "
          f"(topology nodes {before} -> {len(topo['nodes'])})")
    print(f"Sensors remaining: {sum(1 for d in floor['devices'] if d['device_type']=='sensor')} "
          f"(CC2 leak + plant + {len(kept_ref_rooms)} T3H1 references)")
    print(f"Removed {removed_racks} emptied racks; floor-plan racks now {len(kept)}")


if __name__ == "__main__":
    sys.exit(main())
