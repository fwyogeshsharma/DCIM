#!/usr/bin/env python3
"""Meter the electrical plant where the power actually flows.

Three problems, all of them about a CT clamped on the wrong conductor.

1. THE GENSET METER READS HALF THE SITE.
   EV2 clamped GEN1 only — a leftover from when the DC had one genset. Both
   gensets now close onto SWGR2, the paralleling bus, so the load divides across
   them and a meter on one machine reads about half of whatever the site is
   drawing. It is also the meter get_power_summary() classifies as the facility
   "main", and on utility it reads zero, because the gensets carry nothing.
   With a paralleling bus you meter the BUS, not one machine. The meter moves
   GEN1 -> SWGR2. It stays in the Generator Room, so its name is still right.

2. HALF THE MECHANICAL PLANT IS UNMETERED.
   EV2 clamped MCC1 only, so the "cool" sub-meter saw the A-side chillers and
   pumps and missed the B-side entirely. A second EV2 goes on MCC2.

3. THERE IS NO FACILITY MAIN METER.
   PUE wants total facility power. A new EV2 clamps SWGR1, the utility main
   board, whose subtree is both transfer switches -> every UPS and every MCC.

   SWGR1 and SWGR2 are never energized together — _active_parents() routes the
   load through exactly one source board — so both carry the "main" role and
   their sum is the live facility total whether the site is on utility or on
   generator. Nothing double counts.

Also repairs `monitored_panel`, which on the mechanical EV2s still names the
mech-room RPP that add_electrical_upstream.py retired. Nothing reads that field
at runtime (Device.from_dict drops it — it is not a dataclass field), but a
dangling node id in the topology is a trap for whoever reads it next. Every EV2
is re-stamped from the panel it genuinely clamps.

Not modelled here, deliberately: UTIL1, the switchgear, the ATS and the UPS all
carry integral metering already (utilKW, swgrKW, atsKW, UPS-MIB) and serve it
over SNMP. A Verdigris EV2 is a 42-circuit CT submeter panel — in the field you
clamp it on a panelboard, not on switchgear that shipped with a PowerLogic meter.
The EV2s added here exist because get_power_summary() reads energy_monitor nodes
and nothing else.

Idempotent: a panel that already has an EV2 is skipped. Run AFTER
add_electrical_upstream.py, then re-run the layout + export:

    python tools/fix_electrical_metering.py  topologies/dual_dc_enterprise.json
    python tools/fix_electrical_positions.py topologies/dual_dc_enterprise.json
    python tools/export_dcim_floorplan.py    topologies/dual_dc_enterprise.json \
        topologies/dual_dc_enterprise_floorplan.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

ROOM_CODE = {"UPS Room": "UR", "Generator Room": "GR", "Mechanical Room": "MR"}

# Panels that must each carry an EV2, as (device-name prefix, room). The name of a
# new meter is EV2<n>-<DC>-<room code>, with <n> the next free index in that room.
METER_TARGETS = [
    ("SWGR1", "UPS Room"),          # facility main, utility side
    ("SWGR2", "Generator Room"),    # facility main, generator side (the paralleling bus)
    ("MCC1",  "Mechanical Room"),   # mechanical A-half
    ("MCC2",  "Mechanical Room"),   # mechanical B-half
]


def next_ip(seed: str, used: set) -> str:
    if not seed:
        return ""
    a, b, c, d = (int(x) for x in seed.split("."))
    for _ in range(65535):
        d += 1
        if d > 254:
            d = 1
            c += 1
        ip = f"{a}.{b}.{c}.{d}"
        if ip not in used:
            used.add(ip)
            return ip
    return ""


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n for n in nodes}
    used_ids = set(byid)
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}

    def edge(s, t, layer):
        edges.append({"src": s, "dst": t, "src_iface": 0, "dst_iface": 0,
                      "broken": False, "layer": layer})

    def power_neighbours(nid):
        return [(e["src"] if e["dst"] == nid else e["dst"]) for e in edges
                if e.get("layer") == "power" and nid in (e["src"], e["dst"])]

    def meter_on(panel_id):
        """The EV2 clamped on this panel, if any."""
        for nb in power_neighbours(panel_id):
            if byid[nb]["device"]["device_type"] == "energy_monitor":
                return byid[nb]
        return None

    moved = added = restamped = 0

    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc
                    and pred(n["device"])]

        by_name = {n["device"]["name"]: n for n in in_dc(lambda d: True)}
        ev2s = in_dc(lambda d: d["device_type"] == "energy_monitor")
        if not ev2s:
            print(f"{dc}: no EV2 to clone from — skipped")
            continue
        tmpl = ev2s[0]
        seed_ip = tmpl["device"].get("mgmt_ip") or ""

        # ── 1. Re-clamp the genset meter onto the paralleling bus ─────────────
        gen1 = by_name.get(f"GEN1-{dc}-GR")
        swgr2 = by_name.get(f"SWGR2-{dc}-GR")
        if gen1 and swgr2:
            m = meter_on(gen1["id"])
            if m is not None:
                edges[:] = [e for e in edges
                            if not (e.get("layer") == "power"
                                    and {e["src"], e["dst"]} == {gen1["id"], m["id"]})]
                edge(swgr2["id"], m["id"], "power")
                moved += 1
                print(f"{dc}: {m['device']['name']} re-clamped "
                      f"GEN1-{dc}-GR -> SWGR2-{dc}-GR (paralleling bus)")

        # ── 2+3. Give every target panel a meter ──────────────────────────────
        for prefix, room in METER_TARGETS:
            panel = by_name.get(f"{prefix}-{dc}-{ROOM_CODE[room]}")
            if panel is None:
                continue
            if meter_on(panel["id"]) is not None:
                continue                       # already metered — idempotent

            rc = ROOM_CODE[room]
            taken = {n["device"]["name"] for n in ev2s}
            idx = 1
            while f"EV2{idx}-{dc}-{rc}" in taken:
                idx += 1
            name = f"EV2{idx}-{dc}-{rc}"

            nid = uuid.uuid4().hex[:8]
            while nid in used_ids:
                nid = uuid.uuid4().hex[:8]
            used_ids.add(nid)

            node = copy.deepcopy(tmpl)
            d = node["device"]
            node["id"] = nid
            d["id"] = nid
            d["name"] = name
            d["room"] = room
            d["floor"] = panel["device"].get("floor", d.get("floor"))
            d["mgmt_ip"] = next_ip(seed_ip, used_mgmt)
            d["snmp_community"] = d["mgmt_ip"]
            d["ip_address"] = ""
            for iface in d.get("interfaces", []):
                iface["mac_address"] = ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6])
                iface["connected_to_device"] = None
                iface["connected_to_iface"] = None
            # Canvas coordinates are owned by tools/layout_canvas.py.
            node["position"] = {"x": 0, "y": 0}

            nodes.append(node)
            byid[nid] = node
            by_name[name] = node
            ev2s.append(node)
            added += 1

            edge(panel["id"], nid, "power")           # the CT clamp
            # EV2 meters are BACnet facility devices: BMS access switch (the one in
            # the plant), not an IT OOB. See add_electrical_upstream.py for why
            # startswith("OOB1") was the wrong test.
            oob = next((n for n in in_dc(
                lambda x: x["device_type"] == "oob_switch"
                and x.get("room") == "Central Plant")), None)
            if oob is not None:
                edge(nid, oob["id"], "management")
            print(f"{dc}: +{name} clamps {panel['device']['name']}")

    # ── 4. Re-stamp monitored_panel from the panel each EV2 truly clamps ──────
    for n in nodes:
        d = n["device"]
        if d["device_type"] != "energy_monitor":
            continue
        panel = next((nb for nb in power_neighbours(n["id"])
                      if byid[nb]["device"]["device_type"] != "energy_monitor"), None)
        if panel and d.get("monitored_panel") != panel:
            d["monitored_panel"] = panel
            restamped += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nMoved {moved} meter(s), added {added}, re-stamped {restamped} "
          f"monitored_panel ref(s). Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
