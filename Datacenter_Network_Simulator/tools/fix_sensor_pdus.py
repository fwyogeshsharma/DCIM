"""Model Raritan DPX2 rack sensors realistically.

A Raritan DPX2 environmental probe has NO power cord and no network port of its
own — it plugs into a single Raritan PX PDU's sensor port (RJ-45), which powers
it and reads it over SNMP. The curated topology modelled every DPX2 as a dual-
corded IT load (an APC feed + a Raritan feed), which is wrong on two counts: a
DPX2 is not dual-fed, and it cannot connect to an APC PDU.

This tool, for every DPX2 sensor:
  * connects it to the ONE Raritan PX PDU in its rack (drops the APC feed and the
    dual-cord), and
  * if the sensor's rack holds no servers (e.g. a rack left empty when the
    network core moved out), re-homes it into a populated compute rack in the
    same hall — probes belong in an occupied IT rack, monitoring its inlet — and
    connects it to that rack's Raritan PX.

Idempotent. Plant/CHW sensors (not DPX2) are left untouched.

    python tools/fix_sensor_pdus.py
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

TOPO = "topologies/dual_dc_enterprise.json"


def main() -> None:
    with open(TOPO, "r", encoding="utf-8") as f:
        doc = json.load(f)
    nodes, edges = doc["nodes"], doc["edges"]
    by_id = {n["device"]["id"]: n["device"] for n in nodes if n.get("device")}

    def rack_key(d):
        return (d.get("datacenter"), d.get("floor"), d.get("room"),
                d.get("rack_row"), d.get("rack_num"))

    racks = defaultdict(list)
    for d in by_id.values():
        racks[rack_key(d)].append(d)

    def raritan_pdu(key):
        return next((d for d in racks.get(key, [])
                     if d.get("device_type") == "pdu" and "Raritan" in (d.get("vendor") or "")), None)

    def has_server(key):
        return any(d.get("device_type") == "server" for d in racks.get(key, []))

    # Direction of a PDU->load power edge, to mint new ones consistently.
    pdu_src = True
    for e in edges:
        if e.get("layer") == "power":
            a, b = by_id.get(e["src"], {}), by_id.get(e["dst"], {})
            if a.get("device_type") == "pdu" and b.get("device_type") in ("server", "sensor"):
                pdu_src = True
                break
            if b.get("device_type") == "pdu" and a.get("device_type") in ("server", "sensor"):
                pdu_src = False
                break

    def drop_power_edges(dev_id):
        for e in [e for e in edges if e.get("layer") == "power" and dev_id in (e["src"], e["dst"])]:
            edges.remove(e)

    def add_power_edge(pdu_id, dev_id):
        src, dst = (pdu_id, dev_id) if pdu_src else (dev_id, pdu_id)
        for e in edges:
            if e.get("layer") == "power" and {e["src"], e["dst"]} == {pdu_id, dev_id}:
                return
        edges.append({"src": src, "dst": dst, "src_iface": 0, "dst_iface": 0, "layer": "power"})

    # Populated compute racks per hall, for re-homing (round-robin).
    populated = defaultdict(list)
    for key in sorted(racks, key=lambda k: tuple(str(x) for x in k)):
        dc, _fl, room, rr, rn = key
        if rr is None:
            continue
        if has_server(key) and raritan_pdu(key):
            populated[(dc, room)].append(key)
    rr_idx = defaultdict(int)

    sensors = [d for d in by_id.values()
               if d.get("device_type") == "sensor" and "DPX2" in (d.get("model_name") or "")]
    rehomed = refed = 0
    for s in sensors:
        cur = rack_key(s)
        target = cur
        if not has_server(cur):
            hall = (s.get("datacenter"), s.get("room"))
            pool = populated.get(hall)
            if pool:
                target = pool[rr_idx[hall] % len(pool)]
                rr_idx[hall] += 1
                rehomed += 1

        # Co-locate the probe with its rack (0U) — sync placement to a server in
        # the target rack. Fixes both re-homed probes and any whose stale coords
        # put them in the wrong row (e.g. CDU-leak sensors sitting in the front
        # row while their rack_row already points at the compute row).
        anchor = next((d for d in racks[target] if d.get("device_type") == "server"), None)
        if anchor:
            s["floor"] = anchor.get("floor")
            s["rack_row"], s["rack_num"] = anchor.get("rack_row"), anchor.get("rack_num")
            s["floor_x"], s["floor_y"] = anchor.get("floor_x"), anchor.get("floor_y")
            s["rack_facing"] = anchor.get("rack_facing")
            s["cold_aisle"] = anchor.get("cold_aisle")
            s["hot_aisle"] = anchor.get("hot_aisle")

        pdu = raritan_pdu(target)
        if pdu is None:
            continue
        drop_power_edges(s["id"])
        add_power_edge(pdu["id"], s["id"])
        # No power_source_a/b. A DPX2 has no PSU — it plugs into the PDU's SENSOR port
        # (RJ-12) and is powered over it, which is why PSU_COUNT_BY_TYPE gives it none
        # and TopologyEngine._power_ends leaves this edge null-terminated. Recording an
        # A-side "feed" for it claimed a cord that does not exist. The edge above says
        # which PDU it hangs off; power_feeds correctly reports no cord.
        refed += 1

    with open(TOPO, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"Done: {len(sensors)} DPX2 sensor(s) -> single Raritan PX feed; "
          f"{rehomed} re-homed into a populated rack.")


if __name__ == "__main__":
    main()
