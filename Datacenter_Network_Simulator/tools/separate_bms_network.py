#!/usr/bin/env python3
"""Give the facility (OT) gear its own management network, firewalled from the IT
out-of-band network.

BEFORE

    OOBC1 / OOBC2  (IT OOB core pair)
      <- OOB1..5 per hall          server iDRACs, leaf/spine consoles, rack PDUs
      <- OOBM1 per hall            CRAHs, CDUs, environmental sensors, MPPs
      <- OOB1-DC1-CP               chillers, pumps, towers, valves, gensets, UPS
      <- OOB1-DC1-HA-R1-03         UTIL1, SWGR1/2, ATS1/2, MCC1/2   (!!)

add_bms_oob.py segmented HVAC onto dedicated OOBM switches, but every one of them
terminated on the same aggregation as the IT OOB, so the separation was VLAN-deep
at best: a compromised iDRAC and a chiller controller sat two hops apart.

Worse, the electrical upstream landed on an IT ACCESS switch in a compute hall.
add_electrical_upstream.py and fix_electrical_metering.py both picked their
management switch with name.startswith("OOB1"), which matches OOB1-<dc>-HA-R1-03
before it matches OOB1-<dc>-CP. So the transfer switches, the switchgear and the
utility feed shared a broadcast domain with server iDRACs.

AFTER

    facility gear -> OOBM access (per hall / plant)
                       -> BMSC1-<dc>-CP  +  BMSC2-<dc>-MR      (BMS aggregation)
                            -> FWM1-<dc>-NR  +  FWM2-<dc>-NR   (management firewalls)
                                 -> OOBC1 + OOBC2               (IT OOB core)

    IT gear       -> OOB access -> OOBC1 + OOBC2

One enforcement point between the two planes. The BMS aggregation is a pair in two
different rooms (Central Plant and Mechanical Room) so neither room's loss takes
it, both dual-corded off the plant control panels — which are themselves a real A/B
pair on UPSA/UPSB since fix_plant_control_ab.py.

Also repairs two management gaps found on the way:
  * CWP3 / CT3 — the third cooling train's condenser pump and tower cell — were
    cloned by fix_cooling_trains.py without their management uplinks, so they were
    unreachable over SNMP/BACnet.
  * The Network Room's EV2 meters (EV21-<dc>-NR-R2-01/02) never had one either.
    They are BACnet meters, so they home to the BMS aggregation directly — the
    Network Room has no BMS access switch and does not warrant one for two meters.

Idempotent: a DC that already has a BMSC1 is skipped.
Re-export the floorplan afterwards.

Usage:
    python tools/separate_bms_network.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

# Facility / OT device classes. These must never terminate on the IT OOB.
# Rack PDUs are deliberately absent: they are IT power distribution, managed by the
# IT OOB alongside the servers they feed.
OT_TYPES = {
    "chiller", "pump", "cooling_tower", "valve", "crah", "cdu",
    "generator", "ups", "ats", "mcc", "mpp", "switchgear", "utility_feed",
    "energy_monitor", "sensor",
}
FACILITY_ROOMS = {"Central Plant", "Roof", "Generator Room",
                  "UPS Room", "Mechanical Room"}
HALL_CODE = {"Server Hall A": "HA", "Server Hall B": "HB"}

AGG_PORTS = 24
FW_MODEL = "PA-5220"


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


def set_ports(dev: dict, count: int) -> None:
    tmpl = (dev.get("interfaces") or [{}])[0]
    ifaces = []
    for i in range(1, count + 1):
        f = copy.deepcopy(tmpl)
        f.update(index=i, name=f"GigabitEthernet0/{i - 1}", oper_status=1,
                 in_octets=0, out_octets=0, in_errors=0, out_errors=0,
                 in_discards=0, out_discards=0,
                 mac_address=":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6]),
                 connected_to_device=None, connected_to_iface=None)
        ifaces.append(f)
    dev["interfaces"] = ifaces
    dev["interface_count"] = count
    dev["interface_groups"] = [{"iface_type": "Gigabit Ethernet (1 Gbps)", "count": count}]


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n for n in nodes}
    used_ids = set(byid)
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}

    def new_id():
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

    def clone(tmpl, name, room, floor, row, num, unit, fx, fy, ports=None):
        nid = new_id()
        node = copy.deepcopy(tmpl)
        d = node["device"]
        node["id"] = nid
        d["id"] = nid
        d["name"] = name
        d["room"] = room
        d["floor"] = floor
        d["rack_row"], d["rack_num"], d["rack_unit"] = row, num, unit
        d["floor_x"], d["floor_y"] = fx, fy
        d["mgmt_ip"] = next_ip(tmpl["device"].get("mgmt_ip") or "", used_mgmt)
        d["snmp_community"] = d["mgmt_ip"]
        d["ip_address"] = ""
        node["position"] = {"x": 0, "y": 0}   # placed by tools/layout_canvas.py
        if ports:
            set_ports(d, ports)
        nodes.append(node)
        byid[nid] = node
        return node

    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc
                    and pred(n["device"])]

        if in_dc(lambda d: (d.get("name") or "").startswith("BMSC")):
            print(f"{dc}: BMS aggregation already present — skipped")
            continue

        plant_oob = next((n for n in in_dc(
            lambda d: d["device_type"] == "oob_switch"
            and d.get("room") == "Central Plant")), None)
        cores = sorted(in_dc(lambda d: (d.get("name") or "").startswith("OOBC")),
                       key=lambda n: n["device"]["name"])
        if plant_oob is None or len(cores) < 1:
            print(f"{dc}: needs a plant OOB and an OOB core — skipped")
            continue

        by_name = {n["device"]["name"]: n for n in in_dc(lambda d: True)}

        # ── The plant OOB is a BMS access switch; name it like one ───────────
        old_plant = plant_oob["device"]["name"]
        plant_oob["device"]["name"] = f"OOBM1-{dc}-CP"
        by_name[plant_oob["device"]["name"]] = plant_oob

        # ── BMS aggregation: one per room, both on the plant control A/B pair ─
        pdu_a = by_name.get(f"PDUA-{dc}-CP")
        pdu_b = by_name.get(f"PDUB-{dc}-CP")
        mcc1 = by_name.get(f"MCC1-{dc}-MR")
        if pdu_a is None or pdu_b is None or mcc1 is None:
            print(f"{dc}: needs the plant control PDU pair and an MCC — skipped")
            continue

        pd = plant_oob["device"]
        bmsc1 = clone(plant_oob, f"BMSC1-{dc}-CP", "Central Plant", pd.get("floor"),
                      pd["rack_row"], pd["rack_num"], 40,
                      pd.get("floor_x"), pd.get("floor_y"), ports=AGG_PORTS)
        md = mcc1["device"]
        bmsc2 = clone(plant_oob, f"BMSC2-{dc}-MR", "Mechanical Room", md.get("floor"),
                      2, 2, 40, 0.9, 3.0, ports=AGG_PORTS)
        for b in (bmsc1, bmsc2):
            # The cords ARE the record — see Device in core/device_manager.py.
            edge(pdu_a["id"], b["id"], "power")
            edge(pdu_b["id"], b["id"], "power")
        edge(bmsc1["id"], bmsc2["id"])           # aggregation peer link

        # ── Management firewalls: the OT/IT boundary, one per Network Room rack ─
        fw_tmpl = next((n for n in in_dc(
            lambda d: d["device_type"] == "firewall")), None)
        if fw_tmpl is None:
            print(f"{dc}: no firewall to clone — skipped")
            continue
        fws = []
        for i, rack in enumerate((2, 3), start=1):
            fa = by_name.get(f"PDUA-{dc}-NR-R1-{rack:02d}")
            fb = by_name.get(f"PDUB-{dc}-NR-R1-{rack:02d}")
            if fa is None or fb is None:
                continue
            fd = fw_tmpl["device"]
            fw = clone(fw_tmpl, f"FWM{i}-{dc}-NR-R1-{rack:02d}", "Network Room",
                       fd.get("floor"), 1, rack, 38,
                       fa["device"].get("floor_x"), fa["device"].get("floor_y"))
            fw["device"]["model_name"] = FW_MODEL
            edge(fa["id"], fw["id"], "power")
            edge(fb["id"], fw["id"], "power")
            fws.append(fw)

        # ── Re-home every OT device onto the BMS access switch for its room ───
        hall_bms = {}
        for n in in_dc(lambda d: d["device_type"] == "oob_switch"
                       and (d.get("name") or "").startswith("OOBM")):
            hall_bms[n["device"].get("room")] = n

        def bms_access_for(d):
            room = d.get("room") or ""
            if room in HALL_CODE:
                return hall_bms.get(room)
            if room in FACILITY_ROOMS:
                return plant_oob
            # A room with its own BMS access switch (e.g. the Network Room once
            # add_nr_bms_oob.py has run) homes to it; otherwise straight to the
            # aggregation (fine for a room with only a meter or two).
            return hall_bms.get(room) or bmsc1

        rehomed = repaired = 0
        for n in in_dc(lambda d: d["device_type"] in OT_TYPES):
            d = n["device"]
            target = bms_access_for(d)
            if target is None or target["id"] == n["id"]:
                continue
            # Drop links to any management switch that is not the BMS access switch.
            removed = drop(lambda e, i=n["id"], t=target["id"]:
                           e.get("layer") == "management" and i in (e["src"], e["dst"])
                           and byid[e["src"] if e["dst"] == i else e["dst"]]
                           ["device"]["device_type"] == "oob_switch"
                           and (e["src"] if e["dst"] == i else e["dst"]) != t)
            has = any(e.get("layer") == "management"
                      and {e["src"], e["dst"]} == {n["id"], target["id"]}
                      for e in edges)
            if not has:
                edge(n["id"], target["id"])
                if removed:
                    rehomed += 1
                else:
                    repaired += 1

        # ── BMS access switches uplink to the aggregation, not to the IT core ──
        access = [n for n in in_dc(
            lambda d: d["device_type"] == "oob_switch"
            and (d.get("name") or "").startswith("OOBM"))]
        core_ids = {c["id"] for c in cores}
        cut = 0
        for a in access:
            cut += drop(lambda e, i=a["id"]: e.get("layer") == "management"
                        and ((e["src"] == i and e["dst"] in core_ids)
                             or (e["dst"] == i and e["src"] in core_ids)))
            for b in (bmsc1, bmsc2):
                edge(a["id"], b["id"])

        # ── Aggregation -> firewalls -> IT OOB core ──────────────────────────
        for b in (bmsc1, bmsc2):
            for fw in fws:
                edge(b["id"], fw["id"])
        for fw in fws:
            for c in cores:
                edge(fw["id"], c["id"])

        print(f"{dc}: {old_plant} -> {plant_oob['device']['name']} | "
              f"+BMSC1(Central Plant) +BMSC2(Mechanical Room) "
              f"+{len(fws)} management firewalls | "
              f"re-homed {rehomed} OT devices off the IT OOB, "
              f"gave {repaired} unmanaged OT devices an uplink, "
              f"cut {cut} BMS->IT-core uplinks across {len(access)} BMS access switches")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nWrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
