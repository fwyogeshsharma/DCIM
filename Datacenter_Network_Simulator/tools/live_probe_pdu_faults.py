#!/usr/bin/env python3
"""Every PDU fault, and what it actually cascades to.

    python tools/live_probe_pdu_faults.py [hold_s] [pdu_name]

For each condition in turn: baseline, inject, hold, observe, release, confirm
recovery. Three blast radii are reported separately, because they are the whole
question:

  SELF   the PDU's own points — did the fault land on the metric it claims?
  POWER  the loads corded to it — a breaker trip is the only PDU fault with a
         power consequence; every other one must leave the servers alone
  SITE   IT kW / cooling kW / PUE, and which traps fired

The PDU is chosen for having real downstream load. A strip feeding nothing proves
nothing about cascade, and most PDUs in the plant rooms feed facility gear rather
than IT.

Deliberately does NOT call /rules: that endpoint copies the whole fleet's rule
state under the tick thread's lock and starves the simulator, which is how an
earlier campaign measured a plant it was itself stalling. /traps is cheap.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._live_api import DC, connect, device_ids  # noqa: E402

HOLD_S = int(sys.argv[1]) if len(sys.argv) > 1 else 60
MODE = sys.argv[2] if len(sys.argv) > 2 else "all"      # "all" | "pair"
PDU_NAME = sys.argv[3] if len(sys.argv) > 3 else None
SETTLE_S = 45
TOPOLOGY = "topologies/dual_dc_enterprise.json"

# Band each metric random-walks through on a HEALTHY plant. Anything inside it is
# drift, not cascade. Without these the report flagged all thirteen faults as
# "did not fully clear" because pdu_humidity had wandered 0.3 %RH — real signal
# buried under noise is the same as no signal.
#
# Widened from MEASURED drift on this plant: voltage swings ~8 V between samples
# and phase imbalance ~10 points, both of which were being reported as residual
# fault after an unrelated test.
#
# pdu_outlet_current was the awkward one: drift ~1.8 A against an injected 34.0 that
# sat only ~2.1 A above baseline, leaving almost no band that separated them. Fixed
# on the PRODUCT side rather than here — the pin is now 38 A on a 32 A rack breaker
# (~119 %), which is what an overcurrent actually looks like and is comfortably
# clear of the walk. A fault you cannot tell apart from noise is a poor fault.
#
# srv_cpu_mean is FLEET WORKLOAD, not a PDU signal at all. It wanders ~11 points
# between samples and was the last thing masquerading as residual fault.
NOISE = {"pdu_load": 4.0, "pdu_voltage": 10.0, "pdu_outlet_current": 3.0,
         "pdu_power_factor": 0.03, "pdu_phase_imbalance": 12.0,
         "pdu_temperature": 1.5, "pdu_humidity": 5.0,
         "srv_cpu_mean": 15.0, "it_kw": 5.0, "cool_kw": 4.0, "pue": 0.03}

SELF_POINTS = ("pdu_load", "pdu_voltage", "pdu_outlet_current", "pdu_power_factor",
               "pdu_phase_imbalance", "pdu_temperature", "pdu_humidity",
               "pdu_breaker_status", "pdu_outlet_status", "pdu_outlet_failure",
               "pdu_smoke", "pdu_ground_fault")


def power_neighbours(pdu_name):
    """(servers fed by this PDU, its upstream feeders) from the power edges."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, TOPOLOGY), encoding="utf-8") as fh:
        topo = json.load(fh)
    nodes = {n["device"].get("id", n["id"]): n["device"] for n in topo["nodes"]}
    by_name = {d.get("name"): i for i, d in nodes.items()}
    pid = by_name.get(pdu_name)
    downstream, upstream = set(), set()
    for e in topo["edges"]:
        if e.get("layer") != "power":
            continue
        src, dst = e["src"], e["dst"]
        if src == pid and dst in nodes:
            downstream.add(nodes[dst].get("name"))
        elif dst == pid and src in nodes:
            upstream.add(nodes[src].get("name"))
    srv = {n for n in downstream
           if (nodes[by_name[n]].get("device_type") == "server")}
    return srv, upstream


def pick_pdu(devs):
    """A PDU with the most IT load corded to it."""
    best, best_n = None, -1
    for d in devs:
        if d.get("device_type") not in ("pdu", "floor_pdu") or d.get("datacenter") != DC:
            continue
        srv, _up = power_neighbours(d["name"])
        if len(srv) > best_n:
            best, best_n = d, len(srv)
    return best, best_n


def observe(get, pdu_name, srv_names, up_names):
    devs = get("/devices")
    devs = devs["devices"] if isinstance(devs, dict) else devs
    by_name = {d["name"]: d for d in devs}
    ps = get("/bacnet/power-summary")
    pdu = by_name.get(pdu_name, {})
    srv = [by_name[n] for n in srv_names if n in by_name]
    up = [by_name[n] for n in up_names if n in by_name]
    return {
        "self": {k: pdu.get(k) for k in SELF_POINTS},
        "self_power_state": pdu.get("power_state"),
        "srv_total": len(srv),
        # DARK = lost its feed. NOT power_state: that is the Redfish chassis state,
        # an operator or thermal action, and a box whose PDU died is still
        # administratively "On" while drawing nothing. The store models the blackout
        # separately (ext["pwr_dead"]) — it zeroes cpu/memory and breaks the links —
        # so zero CPU is the observable that actually tracks lost power.
        "srv_dark": sum(1 for d in srv if not (d.get("cpu_usage") or 0)),
        "srv_admin_off": sum(1 for d in srv
                             if str(d.get("power_state", "On")) == "Off"),
        "srv_cpu_mean": (round(sum(d.get("cpu_usage", 0) or 0 for d in srv) / len(srv), 1)
                         if srv else None),
        "up_load": {d["name"]: d.get("pdu_load", d.get("ups_output_load"))
                    for d in up},
        "it_kw": round(ps["it_watts"] / 1000, 1),
        "cool_kw": round(ps["cooling_watts"] / 1000, 1),
        "pue": ps["pue"],
    }


_TRAP_KEYS = ("rule", "rule_name", "name", "trap", "trap_type", "type", "oid")


def traps(get):
    """Trap history counted by rule name. /traps, never /rules — the latter copies
    the whole fleet's rule state under the tick thread's lock."""
    try:
        t = get("/traps")
        rows = t.get("traps", t) if isinstance(t, dict) else t
        out = {}
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            k = next((str(r[j]) for j in _TRAP_KEYS if r.get(j)), None)
            if k is None:                     # unknown shape — surface it, don't hide it
                k = "unparsed:" + ",".join(sorted(r)[:4])
            out[k] = out.get(k, 0) + 1
        return out
    except Exception as exc:
        return {f"traps-unavailable: {exc}": 1}


def _moved(key, a, b):
    """True only if the change is bigger than this metric's healthy walk band.
    Status points carry no band — any flip is real."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(b - a) > NOISE.get(key, 0.0)
    return a != b


def diff(a, b):
    """What moved BEYOND the noise floor, so the cascade is legible."""
    out = {}
    for k in a["self"]:
        if _moved(k, a["self"][k], b["self"][k]):
            out[k] = f"{a['self'][k]} -> {b['self'][k]}"
    for k in ("self_power_state", "srv_dark", "srv_admin_off", "srv_cpu_mean", "it_kw", "cool_kw", "pue"):
        if _moved(k, a.get(k), b.get(k)):
            out[k] = f"{a.get(k)} -> {b.get(k)}"
    for n, v in (b.get("up_load") or {}).items():
        if _moved("pdu_load", (a.get("up_load") or {}).get(n), v):
            out[f"up:{n}"] = f"{(a.get('up_load') or {}).get(n)} -> {v}"
    return out


def run_pair(get, post, ids, devs, pdu, srv_names, up_names):
    """Trip BOTH strips of the rack — the only way a 2N load actually goes down.

    A single trip proves the redundancy works and nothing else; every server on
    this rack is dual-corded, so one dead strip is invisible on the IT floor. That
    is correct and worth showing, but it is not the power cascade. Losing both
    cords is, and it is the scenario an operator most needs to rehearse: the
    A-side is already out for maintenance when the B-side breaker lets go.
    """
    peer_name = pdu["name"].replace("PDUA", "PDUB", 1) if "PDUA" in pdu["name"] \
        else pdu["name"].replace("PDUB", "PDUA", 1)
    if peer_name not in ids:
        sys.exit(f"no partner strip found for {pdu['name']} (looked for {peer_name})")
    pair = [pdu["name"], peer_name]
    print(f"tripping BOTH strips: {pair}\n", flush=True)

    pre = observe(get, pdu["name"], srv_names, up_names)
    tr0 = traps(get)
    print(f"   baseline: servers={pre['srv_total']} dark={pre['srv_dark']} "
          f"IT={pre['it_kw']}kW", flush=True)
    try:
        for n in pair:
            post(f"/devices/{ids[n]}/fault",
                 {"fault": "pdu_breaker_trip", "action": "start"})
        for i in range(max(1, HOLD_S // 30)):
            time.sleep(30)
            o = observe(get, pdu["name"], srv_names, up_names)
            print(f"   t+{(i + 1) * 30}s servers dark={o['srv_dark']}/{o['srv_total']} "
                  f"IT={o['it_kw']}kW cool={o['cool_kw']}kW PUE={o['pue']} "
                  f"breaker={o['self'].get('pdu_breaker_status')}", flush=True)
        during = observe(get, pdu["name"], srv_names, up_names)
    finally:
        for n in pair:
            post(f"/devices/{ids[n]}/fault",
                 {"fault": "pdu_breaker_trip", "action": "clear"})
        print("\n   both breakers reset", flush=True)
    time.sleep(SETTLE_S)
    after = observe(get, pdu["name"], srv_names, up_names)
    new_traps = {k: v - tr0.get(k, 0) for k, v in traps(get).items()
                 if v > tr0.get(k, 0)}

    print(f"\n   moved: {diff(pre, during) or 'NOTHING'}")
    print(f"   traps: {new_traps or 'none'}")
    print(f"   after reset, still differing: {diff(pre, after) or 'clean'}")
    print("\nPASS = every dual-corded server on the rack loses BOTH cords and drops,")
    print("       both breaker points read tripped, and all of it comes back on reset.")


def main() -> None:
    get, post = connect()
    ids = device_ids(get)
    devs = get("/devices")
    devs = devs["devices"] if isinstance(devs, dict) else devs

    if PDU_NAME:
        pdu = next(d for d in devs if d["name"] == PDU_NAME)
        n_srv = -1
    else:
        pdu, n_srv = pick_pdu(devs)
    srv_names, up_names = power_neighbours(pdu["name"])
    print(f"PDU under test: {pdu['name']}  downstream servers={len(srv_names)}  "
          f"upstream={sorted(up_names)}", flush=True)
    if not srv_names:
        print("  NOTE this strip feeds no servers — POWER cascade cannot show.",
              flush=True)

    if MODE == "pair":
        return run_pair(get, post, ids, devs, pdu, srv_names, up_names)

    faults = get(f"/devices/{pdu['id']}/faults").get("available", [])
    print(f"conditions offered: {len(faults)}\n", flush=True)

    results = []
    for f in faults:
        fid, label = f["fault"], f["label"]
        pre = observe(get, pdu["name"], srv_names, up_names)
        tr0 = traps(get)
        post(f"/devices/{pdu['id']}/fault", {"fault": fid, "action": "start"})
        time.sleep(HOLD_S)
        during = observe(get, pdu["name"], srv_names, up_names)
        tr1 = traps(get)
        post(f"/devices/{pdu['id']}/fault", {"fault": fid, "action": "clear"})
        time.sleep(SETTLE_S)
        after = observe(get, pdu["name"], srv_names, up_names)

        moved = diff(pre, during)
        new_traps = {k: v - tr0.get(k, 0) for k, v in tr1.items() if v > tr0.get(k, 0)}
        recovered = diff(pre, after)
        results.append({"fault": fid, "label": label, "moved": moved,
                        "traps": new_traps, "residual": recovered})
        print(f"### {label} ({fid})", flush=True)
        print(f"   moved: {moved or 'NOTHING'}", flush=True)
        print(f"   traps: {new_traps or 'none'}", flush=True)
        print(f"   after clear, still differing: {recovered or 'clean'}\n", flush=True)

    print("=" * 70)
    dead = [r["label"] for r in results if not r["moved"]]
    stuck = [r["label"] for r in results if r["residual"]]
    powered = [r["label"] for r in results
               if any(k in r["moved"] for k in ("srv_dark", "self_power_state"))]
    print("no observable effect :", dead or "none")
    print("did not fully clear  :", stuck or "none")
    print("moved IT power       :", powered or "none")


if __name__ == "__main__":
    main()
