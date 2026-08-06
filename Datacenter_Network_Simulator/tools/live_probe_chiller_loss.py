#!/usr/bin/env python3
"""Live check of the METERED cooling branch: a total silent loss of chilled water.

    python tools/live_probe_chiller_loss.py [hold_s]

WHY THIS CANNOT BE A UNIT TEST

`get_power_summary` reports `max(metered, model)`. The model half is covered by
tests/test_cooling_regression.py, but the metered half runs through the EV2 meter
hierarchy and the fixture plants carry no metering, so the summary falls through to
the computed path and the metered branch is never touched. F9 lived there for two
fault campaigns: with every chiller stopped, cooling read 132.9 -> 140.3 kW — RISING,
because the chassis-fan term was driving IT up underneath a mechanical figure that
could not move — and PUE fell the wrong way. Every test was green throughout.

WHAT IT ASSERTS BY EYE

  * cooling kW must FALL. Roughly a third, which is the compressor share of the
    mechanical panel; the CRAH fans ramp UP into the hot hall at the same time.
  * `degraded` must read True for the WHOLE fault, sampled densely. The lead
    rotates on the 90 s run-proof dwell and a phase bug showed up only on the
    changeover tick, which a 15 s sample steps straight over.
  * makeup water must stop — nothing is rejecting heat into the condenser loop.

Faults are released in a `finally`. A silent stop latches nothing, so no reset is
needed afterwards; see live_probe_condenser_flow.py for the case that does.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._live_api import DC, connect, device_ids, force, plant_points  # noqa: E402

HOLD_S = int(sys.argv[1]) if len(sys.argv) > 1 else 180
SAMPLE_S = 5           # dense enough to catch a single-tick changeover flicker
REPORT_EVERY = 3       # print every Nth sample; every one is still tested
RECOVER_S = 120


def snapshot(get):
    ps = get("/bacnet/power-summary")
    pm = plant_points(get)
    trips = get("/bacnet/plant/chiller-trips")
    running = [n for n, v in pm.items()
               if n.startswith("CHL") and DC in n and v.get("Chiller_Running")]
    return {
        "it_kw": round(ps["it_watts"] / 1000, 1),
        "cool_kw": round(ps["cooling_watts"] / 1000, 1),
        "pue": ps["pue"],
        "source": ps.get("source"),
        "chw": next((pm[n].get("CHW_Supply_Temp") for n in running), None),
        "chillers": len(running),
        "degraded": trips.get("degraded"),
        "degraded_dcs": trips.get("degraded_dcs"),
        "pumps": {n: v.get("Flow") for n, v in pm.items()
                  if n.startswith(("CHWP", "CWP")) and DC in n},
        "makeup": [v.get("Makeup_Flow") for n, v in pm.items()
                   if n.startswith("CT") and DC in n],
    }


def line(tag, s):
    print(f"{tag:>10}  IT={s['it_kw']:7.1f}kW  cool={s['cool_kw']:7.1f}kW  "
          f"PUE={s['pue']:.3f}  src={s['source']:8}  chw={s['chw']}  "
          f"chillers={s['chillers']}  degraded={s['degraded']} "
          f"{s['degraded_dcs'] or ''}", flush=True)


def main() -> None:
    get, post = connect()
    ids = device_ids(get)
    chillers = sorted(n for n in ids if n.startswith("CHL") and DC in n)
    print(f"{DC} chillers: {chillers}", flush=True)

    base = snapshot(get)
    line("BASELINE", base)
    print(f"   pump flows: {base['pumps']}\n   makeup    : {base['makeup']}", flush=True)
    if base["chw"] is None or base["chw"] > 8.5:
        print(f"   NOTE baseline chilled water is {base['chw']}, not ~7.0 — the plant "
              f"has not settled. Wait, or the deltas below will understate.", flush=True)

    during, seen_false = base, []
    try:
        for n in chillers:
            force(post, ids, n, "Chiller_Running", 0.0)
        print(f"\n-- silent stop forced on {len(chillers)} chillers --", flush=True)
        for i in range(HOLD_S // SAMPLE_S):
            time.sleep(SAMPLE_S)
            during = snapshot(get)
            if i % REPORT_EVERY == REPORT_EVERY - 1:
                line(f"t+{(i + 1) * SAMPLE_S}s", during)
            if not during["degraded"]:
                seen_false.append((i + 1) * SAMPLE_S)
        print(f"\n   degraded sampled {HOLD_S // SAMPLE_S}x over {HOLD_S}s; "
              f"read False at: {seen_false or 'never'}", flush=True)
        print(f"   pump flows: {during['pumps']}\n   makeup    : {during['makeup']}",
              flush=True)
    finally:
        for n in chillers:
            force(post, ids, n, "Chiller_Running", None)
        print("\n-- released --", flush=True)

    for i in range(RECOVER_S // 15):
        time.sleep(15)
        s = snapshot(get)
        line(f"rec+{(i + 1) * 15}s", s)
        if s["chillers"] and not s["degraded"] and (s["chw"] or 99) < 8.0:
            break

    d = round(during["cool_kw"] - base["cool_kw"], 1)
    print(f"\nVERDICT  cooling {base['cool_kw']} -> {during['cool_kw']} kW "
          f"({d:+.1f})   PUE {base['pue']} -> {during['pue']}")
    print("PASS = cooling FELL and degraded never read False after the first sample.")
    print("Before the fix this read 132.9 -> 140.3 (+7.4) with PUE falling.")


if __name__ == "__main__":
    main()
