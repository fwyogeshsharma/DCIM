#!/usr/bin/env python3
"""Read-only: is the live plant actually clean right now?

    python tools/live_probe_peek.py [samples] [interval_s]

Changes nothing. Run it BEFORE a probe to confirm a settled baseline, and AFTER one
to confirm nothing was left behind.

Two things it exists to settle, both of which cost time to work out the hard way:

  * The plant metrics publish on a COARSER cadence than a short poll, so a run of
    identical readings is NOT a stalled ticker. Watch several samples before
    concluding anything is stuck.
  * Recovery from a total loss of chilled water is genuinely slow — the loop and
    the hall both have to come back down. Measured: 19.0 -> 17.0 -> 14.3 -> 9.2 ->
    7.0 over about six minutes. Starting another probe before that finishes gives a
    baseline well off setpoint and understates every delta.

Clean means: chilled water near 7 C, nothing tripped, and no forced overrides.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._live_api import DC, connect, plant_points  # noqa: E402

SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 4
INTERVAL_S = int(sys.argv[2]) if len(sys.argv) > 2 else 20


def main() -> None:
    get, _post = connect()
    clean = False
    for i in range(SAMPLES):
        pm = plant_points(get)
        ps = get("/bacnet/power-summary")
        trips = get("/bacnet/plant/chiller-trips")
        overrides = get("/bacnet/plant/overrides")
        running = [n for n, v in pm.items()
                   if n.startswith("CHL") and DC in n and v.get("Chiller_Running")]
        chw = next((pm[n].get("CHW_Supply_Temp") for n in running), None)
        forced = sum(len(v) for v in overrides.values()) if isinstance(
            overrides, dict) and all(isinstance(v, dict) for v in overrides.values()) else 0
        tripped = [t["name"] for t in trips.get("tripped", [])]
        clean = (chw is not None and chw < 8.0 and not tripped and not forced
                 and not trips.get("degraded"))
        print(f"t={i * INTERVAL_S:4d}s chw={chw} chillers={len(running)} "
              f"IT={ps['it_watts'] / 1000:.1f} cool={ps['cooling_watts'] / 1000:.1f} "
              f"pue={ps['pue']} tripped={tripped} degraded={trips.get('degraded')} "
              f"forced={forced}", flush=True)
        if i < SAMPLES - 1:
            time.sleep(INTERVAL_S)

    print("\nCLEAN — safe to probe" if clean else
          "\nNOT CLEAN — settle first, or clear whatever is listed above")


if __name__ == "__main__":
    main()
