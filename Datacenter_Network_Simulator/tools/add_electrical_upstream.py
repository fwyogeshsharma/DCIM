#!/usr/bin/env python3
"""Build the real electrical upstream: utility feed → switchgear → ATS → UPS/MCC.

BEFORE (what the topology used to say)

    GEN1 ─┬─> UPS-A ─> RPP-A ─> racks
          ├─> UPS-B ─> RPP-B ─> racks
          ├─> RPP-MR ─> CRAHs
          └─> RPP-CP ─> chillers, pumps, towers, valves, plant controls
    GEN2 ─┬─> UPS-A
          └─> UPS-B

Two problems. GEN2 could not reach the mechanical plant at all, so a GEN1 failure
killed every chiller while GEN2 happily kept the servers running — no real design
accepts a redundant genset that cannot carry the load it is redundant for. And the
chillers hung off an RPP downstream of the UPS, implying they were UPS-backed. They
are not: bulk mechanical load rides the transfer gap on chilled-water thermal mass.

AFTER

    UTIL1 ──────────────> SWGR1 (utility main board) ──┐
                                                       ├─> ATS1 ─┬─> UPSA ─> RPP-A
    GEN1 ─┐                                            │         └─> MCC1 ─> plant (A)
    GEN2 ─┴─> SWGR2 (generator paralleling board) ─────┤
                                                       └─> ATS2 ─┬─> UPSB ─> RPP-B
                                                                 └─> MCC2 ─> plant (B)

Both gensets close onto a common paralleling bus, so neither owns a load class and
either can carry the whole site. Each ATS feeds one UPS (critical) and one MCC
(mechanical), giving a 2N split where losing one side leaves half the cooling.

The plant CONTROL panel (RPP-CP: BMS controllers, plant sensors, the plant OOB
switch) stays where it belongs — on UPS-backed critical power — and is re-fed from
UPS-A rather than straight off the genset.

A single utility service is deliberate. Uptime Institute does not count utility
toward tier classification ("an economic alternative to on-site power"); the
redundancy lives in the generator plant.

Idempotent: a datacenter that already has a utility_feed is skipped.
Run export_dcim_floorplan.py afterwards to refresh the floorplan asset DB.

Usage:
    python tools/add_electrical_upstream.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# Mechanical load types that move from the old RPPs onto the MCCs. A CDU is absent
# on purpose — it is dual-corded off the rack PDUs, so it is UPS-backed.
MECH_TYPES = ("chiller", "pump", "cooling_tower", "crah", "valve")

# New gear, as (name suffix, device_type, vendor, model, room). Model names drive
# rated_power_w through core.device_manager._MODEL_RATED_W.
GEAR = [
    ("UTIL1", "utility_feed", "Schneider Electric",       "Schneider PowerLogic ION9000",      "UPS Room"),
    ("SWGR1", "switchgear",   "Eaton",                    "Eaton Magnum DS 4000A",             "UPS Room"),
    ("SWGR2", "switchgear",   "ASCO Power Technologies",  "ASCO 7000 Paralleling Switchgear",  "Generator Room"),
    ("ATS1",  "ats",          "ASCO Power Technologies",  "ASCO 7000 Series 3000A",            "UPS Room"),
    ("ATS2",  "ats",          "ASCO Power Technologies",  "ASCO 7000 Series 3000A",            "UPS Room"),
    ("MCC1",  "mcc",          "Eaton",                    "Eaton Freedom 2100 MCC 800A",       "Mechanical Room"),
    ("MCC2",  "mcc",          "Eaton",                    "Eaton Freedom 2100 MCC 800A",       "Mechanical Room"),
]
ROOM_CODE = {"UPS Room": "UR", "Generator Room": "GR", "Mechanical Room": "MR"}


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
    byname = {n["device"]["name"]: n for n in nodes}
    used_ids = set(byid)
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}

    def dev(i):
        return byid[i]["device"]

    def new_id() -> str:
        i = uuid.uuid4().hex[:8]
        while i in used_ids:
            i = uuid.uuid4().hex[:8]
        used_ids.add(i)
        return i

    def edge(s, t, layer="power"):
        edges.append({"src": s, "dst": t, "src_iface": 0, "dst_iface": 0,
                      "broken": False, "layer": layer})

    def drop_edges(pred):
        """Remove every edge matching *pred*; return how many went."""
        before = len(edges)
        edges[:] = [e for e in edges if not pred(e)]
        return before - len(edges)

    for dc in sorted({n["device"]["datacenter"] for n in nodes if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc and pred(n["device"])]

        if in_dc(lambda d: d["device_type"] == "utility_feed"):
            print(f"{dc}: already has a utility feed — skipped")
            continue

        gens = sorted(in_dc(lambda d: d["device_type"] == "generator"),
                      key=lambda n: n["device"]["name"])
        upses = sorted(in_dc(lambda d: d["device_type"] == "ups"),
                       key=lambda n: n["device"]["name"])
        if len(gens) < 1 or len(upses) < 2:
            print(f"{dc}: needs >=1 generator and 2 UPS — skipped")
            continue

        mech_rpps = in_dc(lambda d: d["device_type"] == "rpp"
                          and d.get("room") == "Mechanical Room")
        plant_rpps = in_dc(lambda d: d["device_type"] == "rpp"
                           and d.get("room") == "Central Plant")

        anchor = upses[0]
        seed_ip = anchor["device"].get("mgmt_ip") or ""
        base = {k: anchor["device"].get(k) for k in
                ("country", "datacenter_city", "datacenter", "floor")}

        made = {}
        for suffix, dtype, vendor, model, room in GEAR:
            nid = new_id()
            mgmt = next_ip(seed_ip, used_mgmt)
            node = {
                "id": nid,
                # Canvas coordinates are owned by tools/layout_canvas.py.
                "position": {"x": 0, "y": 0},
                "device": {
                    "name": f"{suffix}-{dc}-{ROOM_CODE[room]}",
                    "device_type": dtype,
                    "vendor": vendor,
                    "ip_address": "",
                    "snmp_port": 161,
                    "gnmi_port": 57400,
                    "snmp_community": mgmt,
                    "interface_count": 1,
                    "interface_groups": [{"iface_type": "Gigabit Ethernet (1 Gbps)", "count": 1}],
                    "interfaces": [{
                        "index": 1, "name": "eth0", "speed": 1000000000,
                        "oper_status": 1,
                        "in_octets": 0, "out_octets": 0,
                        "in_errors": 0, "out_errors": 0,
                        "in_discards": 0, "out_discards": 0,
                        "mac_address": ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6]),
                        "connected_to_device": None, "connected_to_iface": None,
                    }],
                    "model_name": model,
                    "metrics_enabled": True,
                    "id": nid,
                    "mgmt_ip": mgmt,
                    "mgmt_vlan": 10,
                    "power_draw_w": 0,
                    "rated_power_w": None,   # derived from model_name at load
                    "room": room,
                    "rack_row": 1, "rack_num": 1, "rack_unit": 0,
                    "floor_x": anchor["device"].get("floor_x", 0.3),
                    "floor_y": anchor["device"].get("floor_y", 0.6),
                    **base,
                },
            }
            nodes.append(node)
            byid[nid] = node
            byname[node["device"]["name"]] = node
            made[suffix] = nid

        # ── Tear down the old direct feeds ────────────────────────────────────
        gen_ids = {n["id"] for n in gens}
        ups_ids = {n["id"] for n in upses}
        old_rpp_ids = {n["id"] for n in mech_rpps + plant_rpps}
        # Generator → UPS, and generator → the mech/plant RPPs. The genset → EV2
        # link stays: a meter on the genset output is real, and it now reads the
        # site load only while the ATS is actually closed onto emergency.
        removed = drop_edges(
            lambda e: e.get("layer") == "power"
            and ((e["src"] in gen_ids and e["dst"] in ups_ids)
                 or (e["dst"] in gen_ids and e["src"] in ups_ids)
                 or (e["src"] in gen_ids and e["dst"] in old_rpp_ids)
                 or (e["dst"] in gen_ids and e["src"] in old_rpp_ids)))

        # ── New one-line ──────────────────────────────────────────────────────
        edge(made["UTIL1"], made["SWGR1"])
        for g in gens:
            edge(g["id"], made["SWGR2"])
        for a in ("ATS1", "ATS2"):
            edge(made["SWGR1"], made[a])       # normal source
            edge(made["SWGR2"], made[a])       # emergency source
        edge(made["ATS1"], upses[0]["id"])
        edge(made["ATS2"], upses[1]["id"])
        edge(made["ATS1"], made["MCC1"])
        edge(made["ATS2"], made["MCC2"])

        # ── Move the bulk mechanical load onto the two MCCs ───────────────────
        # Alternate WITHIN each device type so both sides get a coherent half of
        # the plant: CHL1/CHL3 + CHWP1/CHWP3 + CWP1 + CT1 on the A side, the even
        # units on the B side, CRAHs split down the middle of each hall.
        mcc = (made["MCC1"], made["MCC2"])
        moved = 0
        for dtype in MECH_TYPES:
            units = sorted(in_dc(lambda d, _t=dtype: d["device_type"] == _t),
                           key=lambda n: n["device"]["name"])
            for i, u in enumerate(units):
                drop_edges(lambda e, uid=u["id"]: e.get("layer") == "power"
                           and uid in (e["src"], e["dst"])
                           and (e["src"] in old_rpp_ids or e["dst"] in old_rpp_ids))
                edge(mcc[i % 2], u["id"])
                moved += 1

        # ── The plant CONTROL panel stays on UPS-backed critical power ────────
        for r in plant_rpps:
            edge(upses[0]["id"], r["id"])

        # ── The mech-room RPP has nothing left to feed: retire it ─────────────
        # Its EV2 moves onto MCC1, where it now meters the A-side mechanical bus.
        for r in mech_rpps:
            rid = r["id"]
            meters = [e["dst"] if e["src"] == rid else e["src"] for e in edges
                      if e.get("layer") == "power" and rid in (e["src"], e["dst"])
                      and dev(e["dst"] if e["src"] == rid else e["src"])["device_type"]
                      == "energy_monitor"]
            drop_edges(lambda e, i=rid: i in (e["src"], e["dst"]))
            nodes[:] = [n for n in nodes if n["id"] != rid]
            byid.pop(rid, None)
            for m in meters:
                edge(made["MCC1"], m)

        # ── Management: every new device is a polled SNMP endpoint ────────────
        # Electrical gear is FACILITY (OT) gear and must land on the BMS access
        # switch in the plant, never on an IT out-of-band switch. Matching on
        # startswith("OOB1") used to pick OOB1-<dc>-HA-R1-03 — an IT access switch
        # in a compute hall — putting the transfer switches on the same broadcast
        # domain as server iDRACs. Resolve by ROOM instead.
        oob = next((n for n in in_dc(lambda d: d["device_type"] == "oob_switch"
                                     and d.get("room") == "Central Plant")), None)
        if oob is None:
            oob = next(iter(in_dc(lambda d: d["device_type"] == "oob_switch")), None)
        if oob is not None:
            for nid in made.values():
                edge(nid, oob["id"], layer="management")

        print(f"{dc}: +{len(made)} devices, dropped {removed} legacy gen feeds, "
              f"moved {moved} mechanical units onto MCC1/MCC2, "
              f"retired {len(mech_rpps)} mech RPP")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nWrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
