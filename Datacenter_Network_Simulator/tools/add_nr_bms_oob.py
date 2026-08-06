"""Give each Network Room its own BMS access switch (OOBM).

The Network Room's facility gear (Verdigris EV2 branch meters today, more meters /
environmental sensors / busway monitors as the room grows) is OT and belongs on
the BMS management plane — but the room had no BMS access switch, so its two EV2s
home-ran straight to the BMS aggregation (BMSC1). That is fine for two meters; it
does not scale as the room's facility-device count grows.

This tool adds one BMS access switch per DC, mirroring the hall/plant OOBM pattern:

    OOBM1-<dc>-NR-R1-03   (Cisco Catalyst 1000-48T, access-class, Network Room)
      ├─ mgmt uplink → BMSC1-<dc>-CP     (dual-homed to the BMS core pair, exactly
      ├─ mgmt uplink → BMSC2-<dc>-MR      like every hall / plant OOBM)
      ├─ power A ← PDUA-<dc>-NR-R1-03
      ├─ power B ← PDUB-<dc>-NR-R1-03
      └─ facility endpoints: the Network Room's EV2 meters (rehomed off BMSC1)

The fleet engine is already room-keyed (_oobm_port_for → _hall_oobms matches OOBM
by room), so once this switch exists, fleet-added Network-Room facility gear homes
to it automatically and stacks a second OOBM when the plane fills.

Idempotent: a DC that already has an OOBM in its Network Room is skipped.
Re-run tools/layout_canvas.py and re-export the floorplan afterwards.

    python tools/add_nr_bms_oob.py [topologies/dual_dc_enterprise.json]
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

TOPO = "topologies/dual_dc_enterprise.json"

# OT/facility device classes that must sit on the BMS plane, not the IT OOB.
OT_TYPES = {
    "chiller", "pump", "cooling_tower", "valve", "crah", "cdu",
    "generator", "ups", "ats", "mcc", "mpp", "switchgear", "utility_feed",
    "energy_monitor", "sensor",
}
NR_RACK = (1, 3)          # Network Room network/mgmt rack (row 1, rack 3) — R1-03
NR_UNIT = 40              # U-slot for the stacked BMS access switch


def next_ip(seed: str, used: set) -> str:
    if not seed:
        return ""
    a, b, c, d = (int(x) for x in seed.split("."))
    for _ in range(65535):
        d += 1
        if d > 254:
            d, c = 1, c + 1
        ip = f"{a}.{b}.{c}.{d}"
        if ip not in used:
            used.add(ip)
            return ip
    return ""


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    used_ids = {n["id"] for n in nodes}
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}

    def new_id() -> str:
        i = uuid.uuid4().hex[:8]
        while i in used_ids:
            i = uuid.uuid4().hex[:8]
        used_ids.add(i)
        return i

    def edge(s, t, layer="management"):
        edges.append({"src": s, "dst": t, "src_iface": 0, "dst_iface": 0,
                      "broken": False, "layer": layer})

    def drop(pred):
        before = len(edges)
        edges[:] = [e for e in edges if not pred(e)]
        return before - len(edges)

    byid = {n["id"]: n for n in nodes}

    def dtype(i):
        n = byid.get(i)
        return n["device"]["device_type"] if n else None

    def name_of(i):
        n = byid.get(i)
        return n["device"]["name"] if n else None

    total_added = 0
    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc
                    and pred(n["device"])]

        # Idempotent: skip if the Network Room already has a BMS OOB.
        if in_dc(lambda d: d.get("device_type") == "oob_switch"
                 and d.get("room") == "Network Room"
                 and (d.get("name") or "").split("-", 1)[0].upper().startswith("OOBM")):
            print(f"{dc}: Network Room already has a BMS OOB — skipped")
            continue

        bmsc1 = next((n for n in in_dc(lambda d: (d.get("name") or "").startswith("BMSC1"))), None)
        bmsc2 = next((n for n in in_dc(lambda d: (d.get("name") or "").startswith("BMSC2"))), None)
        if bmsc1 is None or bmsc2 is None:
            print(f"{dc}: no BMS core (BMSC1/BMSC2) — run separate_bms_network.py first; skipped")
            continue

        # Clone template: prefer a hall OOBM (rack-room access-class switch).
        tmpl = next((n for n in in_dc(
            lambda d: d.get("device_type") == "oob_switch"
            and (d.get("name") or "").split("-", 1)[0].upper().startswith("OOBM")
            and (d.get("room") or "").startswith("Server Hall"))), None)
        if tmpl is None:
            tmpl = next((n for n in in_dc(
                lambda d: d.get("device_type") == "oob_switch"
                and (d.get("name") or "").split("-", 1)[0].upper().startswith("OOBM"))), None)
        if tmpl is None:
            print(f"{dc}: no OOBM template — skipped")
            continue

        # Power source: the R1-03 network rack's A/B PDU pair.
        pdua = next((n for n in in_dc(lambda d: d.get("name") == f"PDUA-{dc}-NR-R1-03")), None)
        pdub = next((n for n in in_dc(lambda d: d.get("name") == f"PDUB-{dc}-NR-R1-03")), None)
        anchor = pdua or bmsc1
        ad = anchor["device"]

        # Build the switch by cloning the template OOBM.
        nid = new_id()
        node = copy.deepcopy(tmpl)
        d = node["device"]
        node["id"] = d["id"] = nid
        d["name"] = f"OOBM1-{dc}-NR-R1-03"
        d["room"] = "Network Room"
        d["floor"] = ad.get("floor", 1)
        d["rack_row"], d["rack_num"], d["rack_unit"] = NR_RACK[0], NR_RACK[1], NR_UNIT
        d["floor_x"], d["floor_y"] = ad.get("floor_x", 1.5), ad.get("floor_y", 1.8)
        d["mgmt_ip"] = next_ip(tmpl["device"].get("mgmt_ip") or "192.168.1.1", used_mgmt)
        d["snmp_community"] = d["mgmt_ip"]
        d["ip_address"] = ""
        node["position"] = {"x": 0, "y": 0}   # placed by tools/layout_canvas.py
        nodes.append(node)
        byid[nid] = node

        # Dual-homed BMS uplinks + dual-cord power.
        edge(nid, bmsc1["id"], "management")
        edge(nid, bmsc2["id"], "management")
        if pdua:
            edge(pdua["id"], nid, "power")
        if pdub:
            edge(pdub["id"], nid, "power")

        # Re-home Network Room facility gear off the aggregation onto the new switch.
        rehomed = 0
        for n in in_dc(lambda d: d.get("device_type") in OT_TYPES
                       and d.get("room") == "Network Room"):
            fid = n["id"]
            removed = drop(lambda e, i=fid: e.get("layer") == "management"
                           and i in (e["src"], e["dst"])
                           and dtype(e["dst"] if e["src"] == i else e["src"]) == "oob_switch")
            edge(fid, nid, "management")
            if removed:
                rehomed += 1

        total_added += 1
        print(f"{dc}: +{d['name']} (mgmt {d['mgmt_ip']}) -> BMSC1/BMSC2, "
              f"power {('PDUA/PDUB-'+dc+'-NR-R1-03') if pdua and pdub else 'MISSING'}; "
              f"rehomed {rehomed} facility device(s)")

    if total_added:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
        print(f"\nWrote {path}. Next: python tools/layout_canvas.py, re-export the "
              f"floorplan, then restart.")
    else:
        print("\nNo changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else TOPO))
