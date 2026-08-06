#!/usr/bin/env python3
"""Live check of the derived condenser range: every condenser pump lost.

    python tools/live_probe_condenser_flow.py [hold_s]

The condenser range used to be a fixed constant published at four separate sites —
the tower cell's Cond_Water_In, both chiller Cond_Return_Temp branches, and the CWR
header probe. So a condenser loop with ZERO flow still advertised a healthy 5 K
range on the wire while the chillers behind it latched out on head pressure. It is
now derived from the flow the pumps actually deliver, and must widen toward the
saturation ceiling when they stop.

CAUTION — this one latches, which is why the cleanup is long. Losing condenser flow
trips the chiller high-head-pressure cutout, and that trip HOLDS: it needs a manual
reset, and the reset itself refuses while the loop is still hot. So this releases
the pumps, waits for the condenser to come down, and resets anything latched,
retrying until the plant is genuinely clean.

The hold is deliberately short. The range widens within a tick or two, while the
cutout needs roughly 38 s (the loop climbs ~0.35 C/s against an 11.5 K trip margin,
then a 5 s dwell) — so the default stays under it and normally nothing latches.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._live_api import DC, connect, device_ids, force, plant_points  # noqa: E402

HOLD_S = int(sys.argv[1]) if len(sys.argv) > 1 else 30
SAMPLE_S = 5
RESET_TRIES, RESET_WAIT_S = 20, 15


def snapshot(get, chillers):
    pm = plant_points(get)
    ranges = {}
    for n in chillers:
        v = pm.get(n, {})
        if v.get("Cond_Return_Temp") is not None and v.get("Cond_Supply_Temp") is not None:
            ranges[n] = (round(v["Cond_Return_Temp"] - v["Cond_Supply_Temp"], 2),
                         bool(v.get("Chiller_Running")))
    cells = {n: round(v["Cond_Water_In"] - v.get("Cond_Water_Out", 0.0), 2)
             for n, v in pm.items()
             if n.startswith("CT") and DC in n and v.get("Cond_Water_In") is not None}
    flows = {n: v.get("Flow") for n, v in pm.items()
             if n.startswith("CWP") and DC in n}
    return ranges, cells, flows


def line(get, chillers, tag, lead0=None):
    """Print one observation. Returns the machine currently reporting running.

    The lead is called out because it MOVES: a silent stop or a shed promotes a
    standby, and the newly promoted chiller publishes a zeroed range for its first
    ticks while the one it replaced holds a stale value. Reading "the range" off
    whichever machine happens to be lead then shows ~0 and a stale figure instead
    of the ceiling — which is a reporting artefact, not the model failing, and it
    cost a whole probe run to work out.
    """
    ranges, cells, flows = snapshot(get, chillers)
    shown = " ".join(f"{n.split('-')[0]}:{r}{'*' if run else ''}"
                     for n, (r, run) in ranges.items())
    lead = next((n for n, (_r, run) in ranges.items() if run), None)
    note = ""
    if lead0 is not None and lead != lead0:
        note = f"   << LEAD MOVED {(lead0 or '-').split('-')[0]} -> " \
               f"{(lead or '-').split('-')[0]}, range on the new lead is not settled"
    print(f"{tag:>10}  chiller range {shown}   cell range {list(cells.values())}   "
          f"CWP flow {list(flows.values())}{note}", flush=True)
    return lead


def main() -> None:
    get, post = connect()
    ids = device_ids(get)
    cwps = sorted(n for n in ids if n.startswith("CWP") and DC in n)
    chillers = sorted(n for n in ids if n.startswith("CHL") and DC in n)
    print(f"{DC} condenser pumps: {cwps}\n\n(* = chiller reporting running)", flush=True)
    lead0 = line(get, chillers, "BASELINE")

    try:
        for n in cwps:
            force(post, ids, n, "Run_Status", 0.0)
            force(post, ids, n, "Alarm_Fault", 1.0)
        print(f"\n-- faulted {len(cwps)} condenser pumps --", flush=True)
        for i in range(HOLD_S // SAMPLE_S):
            time.sleep(SAMPLE_S)
            line(get, chillers, f"t+{(i + 1) * SAMPLE_S}s", lead0)
    finally:
        for n in cwps:
            for point in ("Run_Status", "Alarm_Fault"):
                force(post, ids, n, point, None)
        print("\n-- pumps released --", flush=True)

        for attempt in range(RESET_TRIES):
            time.sleep(RESET_WAIT_S)
            trips = get("/bacnet/plant/chiller-trips")
            latched = [t["name"] for t in trips.get("tripped", []) if t.get("dc") == DC]
            if not latched:
                print(f"   no latched trips (after {(attempt + 1) * RESET_WAIT_S}s)",
                      flush=True)
                break
            done = []
            for n in latched:
                r = post("/bacnet/plant/chiller-reset", {"device": ids[n]})
                done.append(f"{n.split('-')[0]}:"
                            f"{'ok' if r.get('ok', True) else r.get('message', '')}")
            print(f"   t+{(attempt + 1) * RESET_WAIT_S}s latched={latched} -> {done}",
                  flush=True)
        else:
            print("   WARNING: chillers still latched — reset them manually", flush=True)

        line(get, chillers, "FINAL", lead0)
        trips = get("/bacnet/plant/chiller-trips")
        print(f"   tripped={[t['name'] for t in trips.get('tripped', [])]} "
              f"degraded={trips.get('degraded')} {trips.get('degraded_dcs')}", flush=True)
    print("\nPASS = range widens to the ceiling with zero flow, returns on release,\n"
          "       and the plant is left with nothing tripped and nothing forced.")


if __name__ == "__main__":
    main()
