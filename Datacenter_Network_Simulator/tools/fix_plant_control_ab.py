#!/usr/bin/env python3
"""Split the plant control panel into a real A/B pair, so the dual-corded BMS gear
in the Central Plant actually has two independent feeds.

WHAT WAS WRONG
--------------
    UPSA -> RPP1-DC1-CP ─┬─> PDUA-DC1-CP ─┐
                         ├─> PDUB-DC1-CP ─┴─> OOB1-DC1-CP   (dual-corded)
                         └─> 6 plant sensors

OOB1 draws from two rack PDUs and looks redundant. It is not: both PDUs hang off
ONE panelboard, fed from ONE UPS, behind ONE transfer switch. A single breaker
trip on RPP1 — or losing UPS-A — takes both cords at once. Every hall rack in this
topology is wired correctly by comparison:

    PDUA-DC1-HA-R2-01  <- RPPA-DC1-HA-R1-04  <- UPSA
    PDUB-DC1-HA-R2-01  <- RPPB-DC1-HA-R1-09  <- UPSB

AFTER
-----
    UPSA -> RPPA-DC1-CP ─┬─> PDUA-DC1-CP ──> OOB1-DC1-CP
                         ├─> EV21-DC1-CP
                         └─> CHWS, CHWR, FLOW      (chilled-water DDC controller)

    UPSB -> RPPB-DC1-CP ─┬─> PDUB-DC1-CP ──> OOB1-DC1-CP
                         ├─> EV22-DC1-CP
                         └─> CWS, CWR, CTB         (condenser-water DDC controller)

The existing RPP1 is RENAMED to RPPA (keeping its node id, so every edge and every
`monitored_panel` reference that already points at it stays valid) and a matching
RPPB is added on the B-side UPS. The leading name segment carries the A/B role that
the runtime parses (fleet_lifecycle._rpp_side), so RPPA/RPPB is the required form.

Plant sensors split by LOOP rather than piling onto one panel. A large central
plant runs two DDC controllers — one sequencing the chilled-water side, one the
condenser/tower side — and each is powered from the panel on its own side. That
way losing a UPS costs the site half its plant sensing, not all of it.

Note what this does NOT fix: the sensors are modelled as loads on a three-phase
panelboard. Real BMS field devices are 24 VAC/VDC off the DDC controller they wire
back to. They draw ~10 W each, so it costs nothing electrically — the panel edge
just isn't how power physically reaches them.

Idempotent: a DC that already has an RPPB in the Central Plant is skipped.
Run after add_electrical_upstream.py; re-export the floorplan afterwards.

Usage:
    python tools/fix_plant_control_ab.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

# Condenser-water loop sensors move to the B-side panel with their DDC controller.
# The chilled-water loop (CHWS / CHWR / FLOW) stays on A.
B_SIDE_SENSOR_PREFIXES = ("CWS", "CWR", "CTB")


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

    def new_id() -> str:
        i = uuid.uuid4().hex[:8]
        while i in used_ids:
            i = uuid.uuid4().hex[:8]
        used_ids.add(i)
        return i

    def edge(s, t, layer="power"):
        edges.append({"src": s, "dst": t, "src_iface": 0, "dst_iface": 0,
                      "broken": False, "layer": layer})

    def drop(pred):
        before = len(edges)
        edges[:] = [e for e in edges if not pred(e)]
        return before - len(edges)

    def clone(tmpl, name, rack_num, mgmt_seed):
        nid = new_id()
        node = copy.deepcopy(tmpl)
        d = node["device"]
        node["id"] = nid
        d["id"] = nid
        d["name"] = name
        node["position"] = {"x": 0, "y": 0}   # placed by tools/layout_canvas.py
        d["rack_num"] = rack_num
        d["floor_x"] = round(0.3 + (rack_num - 1) * 0.6, 3)
        if d.get("mgmt_ip"):
            d["mgmt_ip"] = next_ip(mgmt_seed, used_mgmt)
            if d.get("snmp_community"):
                d["snmp_community"] = d["mgmt_ip"]
        d["ip_address"] = ""
        for iface in d.get("interfaces", []):
            iface["mac_address"] = ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6])
            iface["connected_to_device"] = None
            iface["connected_to_iface"] = None
        nodes.append(node)
        byid[nid] = node
        return node

    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        def in_dc(pred):
            return [n for n in nodes if n["device"].get("datacenter") == dc
                    and pred(n["device"])]

        cp_rpps = in_dc(lambda d: d["device_type"] == "rpp"
                        and d.get("room") == "Central Plant")
        if len(cp_rpps) >= 2:
            print(f"{dc}: plant control panel already an A/B pair — skipped")
            continue
        if not cp_rpps:
            print(f"{dc}: no Central Plant RPP — skipped")
            continue

        rppa = cp_rpps[0]
        upses = sorted(in_dc(lambda d: d["device_type"] == "ups"),
                       key=lambda n: n["device"]["name"])
        if len(upses) < 2:
            print(f"{dc}: needs two UPS — skipped")
            continue
        ups_a, ups_b = upses[0], upses[1]

        by_name = {n["device"]["name"]: n for n in in_dc(lambda d: True)}
        pdu_b = by_name.get(f"PDUB-{dc}-CP")
        oob = by_name.get(f"OOB1-{dc}-CP")
        if pdu_b is None:
            print(f"{dc}: no PDUB in the Central Plant — skipped")
            continue

        # ── Rename the existing panel to the A side (id, and every edge, survive) ──
        old_name = rppa["device"]["name"]
        rppa["device"]["name"] = f"RPPA-{dc}-CP"
        rppa["device"]["rack_num"] = 1
        rppa["device"]["floor_x"] = 0.3

        # ── Build the B-side panel next to it ────────────────────────────────
        rppb = clone(rppa, f"RPPB-{dc}-CP", 2, rppa["device"].get("mgmt_ip", ""))

        # ── Re-feed: each panel from its own UPS ─────────────────────────────
        dropped = drop(lambda e: e.get("layer") == "power"
                       and e["dst"] == rppa["id"]
                       and byid[e["src"]]["device"]["device_type"] == "ups")
        edge(ups_a["id"], rppa["id"])
        edge(ups_b["id"], rppb["id"])

        # ── The B-side rack PDU moves onto the B-side panel ───────────────────
        drop(lambda e: e.get("layer") == "power"
             and {e["src"], e["dst"]} == {rppa["id"], pdu_b["id"]})
        edge(rppb["id"], pdu_b["id"])

        # ── Plant sensors split by loop, each with its own DDC controller ─────
        moved = []
        for n in in_dc(lambda d: d["device_type"] == "sensor"
                       and d.get("room") == "Central Plant"):
            if not (n["device"]["name"] or "").startswith(B_SIDE_SENSOR_PREFIXES):
                continue
            drop(lambda e, i=n["id"]: e.get("layer") == "power"
                 and {e["src"], e["dst"]} == {rppa["id"], i})
            edge(rppb["id"], n["id"])
            moved.append(n["device"]["name"])

        # ── The B panel gets its own meter, like the A panel has ─────────────
        ev2_a = next((byid[e["dst"] if e["src"] == rppa["id"] else e["src"]]
                      for e in edges if e.get("layer") == "power"
                      and rppa["id"] in (e["src"], e["dst"])
                      and byid[e["dst"] if e["src"] == rppa["id"] else e["src"]]
                      ["device"]["device_type"] == "energy_monitor"), None)
        ev2_b = None
        if ev2_a is not None:
            taken = {n["device"]["name"] for n in in_dc(
                lambda d: d["device_type"] == "energy_monitor")}
            idx = 1
            while f"EV2{idx}-{dc}-CP" in taken:
                idx += 1
            ev2_b = clone(ev2_a, f"EV2{idx}-{dc}-CP", 2,
                          ev2_a["device"].get("mgmt_ip", ""))
            edge(rppb["id"], ev2_b["id"])
            if oob is not None:
                edge(ev2_b["id"], oob["id"], "management")
            ev2_b["device"]["monitored_panel"] = rppb["id"]

        print(f"{dc}: {old_name} -> {rppa['device']['name']} (on {ups_a['device']['name']}), "
              f"+RPPB on {ups_b['device']['name']}, dropped {dropped} single feed | "
              f"PDUB re-fed | sensors to B: {moved} | "
              f"+{ev2_b['device']['name'] if ev2_b else 'no meter'}")

    # Re-stamp monitored_panel for every meter from the panel it actually clamps.
    restamped = 0
    for n in nodes:
        d = n["device"]
        if d["device_type"] != "energy_monitor":
            continue
        panel = next((e["src"] if e["dst"] == n["id"] else e["dst"]) for e in edges
                     if e.get("layer") == "power" and n["id"] in (e["src"], e["dst"])) \
            if any(e.get("layer") == "power" and n["id"] in (e["src"], e["dst"])
                   for e in edges) else None
        if panel and d.get("monitored_panel") != panel:
            d["monitored_panel"] = panel
            restamped += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nRe-stamped {restamped} monitored_panel ref(s). Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
