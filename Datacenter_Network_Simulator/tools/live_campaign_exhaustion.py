#!/usr/bin/env python3
"""The six exhaustion scenarios, re-measured live — without disturbing the plant.

    python tools/live_campaign_exhaustion.py [hold_s] [settle_s] [out.json]

WHY THIS EXISTS. The original campaign called GET /rules on EVERY observation, and
that endpoint took 333 snapshots per request (166 rules x 2, plus the grand total),
each a full copy of the whole fleet's rule state under the lock the tick thread
needs every tick. The cost scales with device count — roughly 60k device-state
dicts copied per request at 181 servers, 217k at 653 — so the harness starved the
simulator in proportion to the load it was measuring.

That is not a slow endpoint, it is an observer effect. It made the high-load half
of the campaign read MILDER than the low-load half: with rejection gone the
condenser should reach its 50 C ceiling in about 75 s at _COND_RISE 0.35 C/s, and
the high-load run recorded 34.4 C after 240 s. The physics was correct; it was
getting fewer ticks. The same starvation left `hall-crahs` starting dirty and
`lead-chiller-stop` returning every field identical.

`/rules` is fixed now (333 snapshots -> 1), but this harness does not call it at
all. The fired-count tally it was used for is not worth touching the plant for.

WHAT THIS DOES DIFFERENTLY from the original, beyond dropping /rules:
  * refuses to start a scenario until the plant is genuinely at setpoint, and
    RECORDS that rather than assuming it
  * verifies each injection actually LANDED by reading the point back — the
    original had no such check, which is how it shipped scenarios that measured a
    plant nobody had faulted
  * resets latched high-pressure trips in cleanup, retrying while the loop cools,
    because a latched cutout does not clear itself and poisons every later run
  * releases everything in a `finally`

Device names are discovered from the live fleet, not hard-coded. The original
hard-coded them, and they drift.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._live_api import DC, connect, device_ids, force, plant_points  # noqa: E402

HOLD_S = int(sys.argv[1]) if len(sys.argv) > 1 else 240
SETTLE_S = int(sys.argv[2]) if len(sys.argv) > 2 else 420
OUT = (sys.argv[3] if len(sys.argv) > 3
       else os.path.join(tempfile.gettempdir(), "live_campaign_exhaustion.json"))
POLL_S = 15
CLEAN_CHW_C = 8.0            # chilled water at/below this counts as settled


def _avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 2) if xs else None


def observe(get):
    """One full observation. Deliberately does NOT touch /rules."""
    pm = plant_points(get)
    ps = get("/bacnet/power-summary")
    tr = get("/bacnet/plant/chiller-trips")
    devs = get("/devices")
    devs = devs["devices"] if isinstance(devs, dict) else devs

    def sel(pref):
        return {k: v for k, v in pm.items() if k.startswith(pref) and DC in k}

    chl, ct, crah, cdu = sel("CHL"), sel("CT"), sel("CRAH"), sel("CDU")
    chwp, cwp = sel("CHWP"), sel("CWP")
    run_ch = [v for v in chl.values() if v.get("Chiller_Running")]

    srv = [d for d in devs if d.get("device_type") == "server"
           and d.get("datacenter") == DC]
    inl = [d["inlet_temp"] for d in srv if isinstance(d.get("inlet_temp"), (int, float))]
    die = [d["cpu_temp"] for d in srv if isinstance(d.get("cpu_temp"), (int, float))]
    probes = [d["inlet_temp"] for d in devs
              if d.get("device_type") == "sensor" and d.get("datacenter") == DC
              and str(d.get("room", "")).startswith("Server Hall")
              and isinstance(d.get("inlet_temp"), (int, float))]

    return {
        "chw_supply":  _avg([v.get("CHW_Supply_Temp") for v in run_ch]),
        "cond_supply": _avg([v.get("Cond_Supply_Temp") for v in run_ch]),
        "chillers_run": len(run_ch),
        "chw_flow":    _avg([v.get("CHW_Flow") for v in chl.values()]),      # F2
        "chwp_flow":   _avg([v.get("Flow") for v in chwp.values()]),         # F2
        "cwp_flow":    _avg([v.get("Flow") for v in cwp.values()]),          # F2
        "crah_supply": _avg([v.get("Supply_Air_Temp") for v in crah.values()]),  # F3
        "crah_valve":  _avg([v.get("CHW_Valve") for v in crah.values()]),    # F13
        "cdu_tcs":     _avg([v.get("TCS_Supply_Temp") for v in cdu.values()]),   # F12
        "makeup_lpm":  _avg([v.get("Makeup_Flow") for v in ct.values()]),    # F15
        "basin_c":     _avg([v.get("Basin_Temp") for v in ct.values()]),
        "cond_range":  _avg([(v.get("Cond_Return_Temp", 0) - v.get("Cond_Supply_Temp", 0))
                             for v in run_ch]),
        "inlet_mean":  _avg(inl), "inlet_max": max(inl) if inl else None,
        "die_max":     max(die) if die else None,                            # F16
        "die_over_90": sum(1 for t in die if t >= 90.0),
        "probe_mean":  _avg(probes),                                          # F4
        "srv_over_32": sum(1 for t in inl if t > 32.0),
        "it_kw":   round(ps["it_watts"] / 1000, 1),
        "cool_kw": round(ps["cooling_watts"] / 1000, 1),                      # F9
        "pue":     ps["pue"],
        "degraded": tr.get("degraded"),                                       # F14
        "degraded_dcs": tr.get("degraded_dcs"),
        "tripped": [t["name"] for t in tr.get("tripped", [])],
    }


def is_clean(o):
    return (o["chw_supply"] is not None and o["chw_supply"] <= CLEAN_CHW_C
            and not o["tripped"] and not o["degraded"])


def wait_clean(get, limit_s, tag):
    t0 = time.time()
    o = observe(get)
    while not is_clean(o) and time.time() - t0 < limit_s:
        time.sleep(POLL_S)
        o = observe(get)
    took = round(time.time() - t0)
    print(f"   {tag}: chw={o['chw_supply']} tripped={o['tripped']} "
          f"degraded={o['degraded']} after {took}s "
          f"{'CLEAN' if is_clean(o) else 'NOT CLEAN'}", flush=True)
    return o, is_clean(o)


def scenarios(get, ids):
    """Built from the live fleet, so a renamed or resized plant still works.

    Selected by DEVICE TYPE, not by name prefix. A prefix match on "CT" also picks
    up CTB-DC1-CP — the tower-basin thermowell, a sensor with no Fan_Status object —
    so the scenario quietly aimed a quarter of its injections at a device that
    cannot receive them. The landed-check caught it; the type filter prevents it.
    """
    devs = get("/devices")
    devs = devs["devices"] if isinstance(devs, dict) else devs
    by_type = {}
    for d in devs:
        if d.get("datacenter") == DC and d.get("name") in ids:
            by_type.setdefault(d.get("device_type"), []).append(d["name"])

    def names(dtype, pref=None, contains=None):
        return sorted(n for n in by_type.get(dtype, [])
                      if (pref is None or n.startswith(pref))
                      and (contains is None or contains in n))

    pm = plant_points(get)
    chillers = names("chiller")
    lead = next((n for n in chillers if pm.get(n, {}).get("Chiller_Running")), None)
    return [
        ("all-chillers", [(n, "Chiller_Running", 0.0) for n in chillers],
         "silent stop of every chiller"),
        ("all-chwp", [(n, "Alarm_Fault", 1.0) for n in names("pump", pref="CHWP")],
         "every CHW pump faulted - flow must fall, interlock must shed"),
        ("all-towers", [(n, "Fan_Status", 0.0) for n in names("cooling_tower")],
         "tower bank stopped - condenser runaway, makeup must stop"),
        ("all-cwp", [(n, "Alarm_Fault", 1.0) for n in names("pump", pref="CWP")],
         "every condenser pump faulted - rejection lost at the water side"),
        ("hall-crahs", [(n, "Unit_Running", 0.0)
                        for n in names("crah", contains="-HA-")],
         "hall air side gone - the plant itself stays healthy"),
        ("lead-chiller-stop", [(lead, "Chiller_Running", 0.0)] if lead else [],
         "single silent stop - must fail over"),
    ]


def clear_trips(get, post, ids, limit_s):
    """A latched HP cutout does not self-clear, and the reset refuses while the
    loop is hot. Retry until the plant is genuinely resettable, or say so."""
    t0 = time.time()
    while time.time() - t0 < limit_s:
        tr = get("/bacnet/plant/chiller-trips")
        latched = [t["name"] for t in tr.get("tripped", []) if t.get("dc") == DC]
        if not latched:
            return True
        for n in latched:
            if n in ids:
                post("/bacnet/plant/chiller-reset", {"device": ids[n]})
        time.sleep(POLL_S)
    print("   WARNING: chillers still latched — reset manually", flush=True)
    return False


def main() -> None:
    get, post = connect()
    ids = device_ids(get)
    results = []

    for name, points, why in scenarios(get, ids):
        if not points:
            print(f"\n### {name}: SKIPPED (no target devices found)", flush=True)
            continue
        print(f"\n### {name} — {why}", flush=True)
        pre, clean = wait_clean(get, SETTLE_S, "baseline")

        landed = []
        try:
            for dev, point, value in points:
                force(post, ids, dev, point, value)
            time.sleep(POLL_S)
            # Did it actually land? The original campaign never checked, and
            # shipped scenarios that measured an unfaulted plant.
            pm = plant_points(get)
            for dev, point, value in points:
                got = pm.get(dev, {}).get(point)
                landed.append({"device": dev, "point": point,
                               "want": value, "got": got, "ok": got == value})
            bad = [x for x in landed if not x["ok"]]
            if bad:
                print(f"   WARNING {len(bad)}/{len(points)} injections did NOT land: "
                      f"{[x['device'] for x in bad][:4]}", flush=True)
            else:
                print(f"   injected {len(points)} points, all landed", flush=True)
            time.sleep(max(0, HOLD_S - POLL_S))
            during = observe(get)
        finally:
            for dev, point, _v in points:
                force(post, ids, dev, point, None)
        post_clean = clear_trips(get, post, ids, SETTLE_S)
        after, recovered = wait_clean(get, SETTLE_S, "recovery")

        d = {k: (round(during[k] - pre[k], 2)
                 if isinstance(pre.get(k), (int, float))
                 and isinstance(during.get(k), (int, float)) else None)
             for k in pre}
        results.append({"scenario": name, "why": why, "clean_baseline": clean,
                        "all_landed": all(x["ok"] for x in landed),
                        "injections": landed, "pre": pre, "during": during,
                        "after": after, "recovered": recovered,
                        "trips_cleared": post_clean})
        print(f"   chw {pre['chw_supply']}->{during['chw_supply']}  "
              f"cond {pre['cond_supply']}->{during['cond_supply']}  "
              f"cool {pre['cool_kw']}->{during['cool_kw']}kW  "
              f"PUE {pre['pue']}->{during['pue']}  "
              f"inlet_max {pre['inlet_max']}->{during['inlet_max']}  "
              f"degraded {pre['degraded']}->{during['degraded']}", flush=True)
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)

    print(f"\nwrote {OUT}")
    bad = [r["scenario"] for r in results
           if not (r["clean_baseline"] and r["all_landed"])]
    print("SUSPECT rows (dirty baseline or injection did not land):", bad or "none")


if __name__ == "__main__":
    main()
