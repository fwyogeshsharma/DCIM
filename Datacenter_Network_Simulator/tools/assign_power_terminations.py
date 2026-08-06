#!/usr/bin/env python3
"""Give every PDU->IT power cord a real outlet and PSU, and build the inventories.

Power edges carry no termination (see clear_nonethernet_ifaces.py — they used to
carry a fake iface 0). A cord physically runs from a numbered PDU OUTLET to a
numbered PSU inlet, and that is what outlet-metered PDUs report over SNMP
(APC rPDU2OutletMeteredStatusTable, Raritan PDU2-MIB outletSensorMeasurements,
ServerTech Sentry3/4-MIB outletTable). This writes:

  device.outlets   on each rack PDU, from its SKU (PDU_OUTLET_CATALOG)
  device.psus      on each IT load (1+1), inlet sized from its draw
  edge.outlet      1-based outlet index on the PDU
  edge.psu         1-based PSU index on the load
  edge.supply_node / edge.load_node   which end feeds which

WHAT GETS A CORD, AND WHAT DOES NOT:

  pdu -> server/switch/router/firewall/lb/oob_switch/cdu   YES — a real cord.
  pdu -> sensor    NO. A Raritan DPX2 has no PSU: it plugs into the PDU's SENSOR
                   port (RJ-12/RJ-45) and is powered over it. That is why those
                   19 edges are single-fed with no A-side twin — not a redundancy
                   gap, just a different kind of cable. Modelling it as an outlet
                   would consume a receptacle that is still physically free.
  rpp -> pdu       NO. The PDU has outlets but here it is the LOAD; its own feed
                   is a breaker position on the RPP panel. Out of scope.
  mcc -> pump, ups -> rpp, switchgear -> ats, ...          NO — same reason.

FEED SIDE: PSU1 takes the A feed and PSU2 the B feed, from the PDU's name prefix
(PDUA/PDUB). That choice is expressed by WHICH CORD lands on which PSU — the side
itself is not written onto the PSU, since it is a fact about the cord and is read
back from the edge via TopologyEngine.power_feeds / device_manager.feed_side.

Outlet order is deterministic (cords sorted by load name), so a re-run reproduces
the same assignment rather than reshuffling every cord.

Idempotent. Refuses to write if any PDU would overflow its outlet count.

Usage:
    python tools/assign_power_terminations.py topologies/dual_dc_enterprise.json
    python tools/assign_power_terminations.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.device_manager import (  # noqa: E402
    PDU_OUTLET_CATALOG, PSU_COUNT_BY_TYPE, C13_CONTINUOUS_W, _PHASE_PAIRS,
)


def build_outlets(model: str) -> list:
    spec = PDU_OUTLET_CATALOG.get(model)
    if not spec:
        return []
    n_c13, n_c19, phases, _a, _v = spec
    n_banks = 6 if phases == 3 else 2
    total = n_c13 + n_c19
    per_bank = max(1, -(-total // n_banks))
    out = []
    for i in range(total):
        bank = min(n_banks, i // per_bank + 1)
        out.append({
            "index": i + 1,
            "type": "C13" if i < n_c13 else "C19",
            "bank": bank,
            "phase": _PHASE_PAIRS[(bank - 1) % 3] if phases == 3 else "L1",
            "rated_a": 10.0 if i < n_c13 else 16.0,
        })
    return out


def build_psus(dtype: str, watts: int) -> list:
    n = PSU_COUNT_BY_TYPE.get(dtype, 0)
    if not n:
        return []
    inlet = "C20" if (watts or 0) > C13_CONTINUOUS_W else "C14"
    return [{"index": i + 1, "name": f"PSU{i + 1}", "inlet": inlet,
             "capacity_w": 1100 if inlet == "C14" else 2400}
            for i in range(n)]


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n["device"] for n in nodes}

    # 1. inventories
    inv = Counter()
    for n in nodes:
        d = n["device"]
        if d["device_type"] == "pdu":
            d["outlets"] = build_outlets(d.get("model_name", ""))
            if not d["outlets"]:
                print(f"  WARNING: no outlet spec for SKU {d.get('model_name')!r} "
                      f"({d['name']}) — left with none")
            inv[f"pdu outlets: {d.get('model_name')}"] = len(d["outlets"])
        else:
            d["outlets"] = []
        d["psus"] = build_psus(d["device_type"], d.get("power_draw_w", 0))
        if d["psus"]:
            inv[f"psus: {d['device_type']} ({d['psus'][0]['inlet']})"] += len(d["psus"])

    # 2. group cords per PDU, deterministically
    cords = defaultdict(list)          # pdu id -> [edge]
    for e in edges:
        if e.get("layer") != "power":
            continue
        e.pop("outlet", None); e.pop("psu", None)
        e.pop("supply_node", None); e.pop("load_node", None)
        s, d = byid[e["src"]], byid[e["dst"]]
        # supply must have outlets AND load must have PSUs — see module docstring.
        if s["device_type"] == "pdu" and s.get("outlets") and d.get("psus"):
            cords[e["src"]].append(e)
    for pdu_id in cords:
        cords[pdu_id].sort(key=lambda e: byid[e["dst"]]["name"])

    # 3. assign
    assigned = Counter()
    overflow = []
    for pdu_id, es in cords.items():
        pdu = byid[pdu_id]
        free = {"C13": [o["index"] for o in pdu["outlets"] if o["type"] == "C13"],
                "C19": [o["index"] for o in pdu["outlets"] if o["type"] == "C19"]}
        used_psu = defaultdict(set)
        for e in es:
            load = byid[e["dst"]]
            want = "C19" if load["psus"][0]["inlet"] == "C20" else "C13"
            if not free[want]:
                overflow.append(f"{pdu['name']} ({pdu['model_name']}): out of {want} "
                                f"outlets, cannot cord {load['name']}")
                continue
            # PSU1 = A feed, PSU2 = B feed.
            side = "A" if pdu["name"].startswith("PDUA") else "B" if pdu["name"].startswith("PDUB") else ""
            want_psu = 1 if side == "A" else 2 if side == "B" else None
            if want_psu is None or want_psu in used_psu[e["dst"]] or want_psu > len(load["psus"]):
                want_psu = next((x["index"] for x in load["psus"]
                                 if x["index"] not in used_psu[e["dst"]]), None)
            if want_psu is None:
                overflow.append(f"{load['name']}: every PSU already corded")
                continue
            used_psu[e["dst"]].add(want_psu)
            e["outlet"] = free[want].pop(0)
            e["psu"] = want_psu
            e["supply_node"] = pdu_id
            e["load_node"] = e["dst"]
            assigned[f"{pdu['model_name']} -> {load['device_type']} ({want})"] += 1

    if overflow:
        print(f"REFUSING TO WRITE — {len(overflow)} cord(s) do not fit:")
        for o in overflow[:20]:
            print(f"  {o}")
        return 1

    if not dry_run:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would assign" if dry_run else "Assigned"
    print(f"{verb} {sum(assigned.values())} cord(s) to outlets. "
          f"{'(dry run)' if dry_run else f'Wrote {p}'}\n")
    for k, c in sorted(assigned.items()):
        print(f"  {c:5d}  {k}")
    print()
    for k, c in sorted(inv.items()):
        print(f"  {c:5d}  {k}")

    # utilisation: the headroom question an operator actually asks
    print("\n  PDU outlet utilisation:")
    util = defaultdict(list)
    for pdu_id, es in cords.items():
        pdu = byid[pdu_id]
        n13 = sum(1 for o in pdu["outlets"] if o["type"] == "C13")
        used13 = sum(1 for e in es if e.get("outlet") and
                     pdu["outlets"][e["outlet"] - 1]["type"] == "C13")
        util[pdu["model_name"]].append((used13, n13))
    for model, v in sorted(util.items()):
        worst = max(v, key=lambda t: t[0] / t[1])
        print(f"  {model:22s} C13 worst {worst[0]}/{worst[1]} "
              f"({100 * worst[0] / worst[1]:.0f}%) across {len(v)} PDUs")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
