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

WHERE ON THE STRIP: spread, heaviest first, mirrored. A strip's outlets are
wired in banks, each behind its own breaker, and on a 3-phase strip each bank
hangs on one leg. Filling outlets in order put a rack's load on the first two
banks - 17 A on a 16 A bank - with the rest empty, and its legs 15/18/5 A. An
installer does not cable that way: the heaviest loads go in first, each into
the outlet whose LEG carries least so far, then whose BANK does, so the phases
balance at the panel and no bank breaker sits near its rating. And a load's
two cords take the SAME outlet number on its A and B strips ("outlet 7 on both
sides"), which is how racks are documented and what a failover check assumes.

Outlet order is deterministic (loads sorted by draw, then name), so a re-run
reproduces the same assignment rather than reshuffling every cord.

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
    PDU_OUTLET_CATALOG, PSU_COUNT_BY_TYPE, C13_CONTINUOUS_W, outlet_layout,
)


def build_outlets(model: str) -> list:
    spec = PDU_OUTLET_CATALOG.get(model)
    if not spec:
        return []
    n_c13, n_c19, phases, _a, _v = spec
    return outlet_layout(n_c13, n_c19, phases)


def build_psus(dtype: str, watts: int) -> list:
    n = PSU_COUNT_BY_TYPE.get(dtype, 0)
    if not n:
        return []
    inlet = "C20" if (watts or 0) > C13_CONTINUOUS_W else "C14"
    return [{"index": i + 1, "name": f"PSU{i + 1}", "inlet": inlet,
             "capacity_w": 1100 if inlet == "C14" else 2400}
            for i in range(n)]


def _pair_key(name: str) -> str:
    """PDUA-DC1-HA-R2-01 and PDUB-DC1-HA-R2-01 are one pair."""
    return name[4:] if name[:4] in ("PDUA", "PDUB") else name


def spread_outlets(cords: dict, byid: dict) -> dict:
    """(pdu_id, load_id) -> outlet index, for every cord.

    Per pair of strips (or a lone strip): loads heaviest first; each takes the
    outlet of its connector type that is free on EVERY strip it is corded to,
    choosing the one whose leg then bank carries the least draw so far, lowest
    number on a tie. A load whose strips share no free outlet number (a pair of
    unlike SKUs) is placed on each strip alone by the same rule.
    """
    groups: dict = defaultdict(list)
    for pdu_id in cords:
        groups[_pair_key(byid[pdu_id]["name"])].append(pdu_id)
    out: dict = {}
    for strips in groups.values():
        strips.sort(key=lambda s: byid[s]["name"])
        layout = {s: {o["index"]: o for o in byid[s]["outlets"]} for s in strips}
        phases = {s: (3 if any(o.get("phase", "L1") != "L1" for o in byid[s]["outlets"]) else 1)
                  for s in strips}
        used = {s: set() for s in strips}
        leg_w = {s: defaultdict(float) for s in strips}
        bank_w = {s: defaultdict(float) for s in strips}
        on = defaultdict(list)                     # load -> strips it is corded to
        for s in strips:
            for e in cords[s]:
                on[e["dst"]].append(s)

        def leg(s, o):
            b = layout[s][o]["bank"]
            return (b - 1) % 3 if phases[s] == 3 else 0

        def cost(ss, o):
            return (sum(leg_w[s][leg(s, o)] for s in ss),
                    sum(bank_w[s][layout[s][o]["bank"]] for s in ss), o)

        def place(load_id, ss):
            load = byid[load_id]
            want = "C19" if load["psus"][0]["inlet"] == "C20" else "C13"
            free = [o for o in sorted(layout[ss[0]])
                    if all(o in layout[s] and layout[s][o]["type"] == want
                           and o not in used[s] for s in ss)]
            if not free:
                return False
            o = min(free, key=lambda x: cost(ss, x))
            w = float(load.get("power_draw_w") or 0) / max(1, len(on[load_id]))
            for s in ss:
                used[s].add(o)
                leg_w[s][leg(s, o)] += w
                bank_w[s][layout[s][o]["bank"]] += w
                out[(s, load_id)] = o
            return True

        order = sorted(on, key=lambda lid: (-float(byid[lid].get("power_draw_w") or 0),
                                            byid[lid]["name"]))
        for lid in order:
            ss = on[lid]
            if not place(lid, ss) and len(ss) > 1:
                for s in ss:
                    place(lid, [s])
    return out


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

    # 3. where each load plugs in: spread over legs then banks, the same outlet
    #    number on both strips of a pair (see module docstring).
    outlet_for = spread_outlets(cords, byid)

    # 4. assign
    assigned = Counter()
    overflow = []
    for pdu_id, es in cords.items():
        pdu = byid[pdu_id]
        free = {"C13": [o["index"] for o in pdu["outlets"] if o["type"] == "C13"],
                "C19": [o["index"] for o in pdu["outlets"] if o["type"] == "C19"]}
        for e in es:
            o = outlet_for.get((pdu_id, e["dst"]))
            if o is not None:
                for k in free:
                    if o in free[k]:
                        free[k].remove(o)
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
            o = outlet_for.get((pdu_id, e["dst"]))
            e["outlet"] = o if o is not None else free[want].pop(0)
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
