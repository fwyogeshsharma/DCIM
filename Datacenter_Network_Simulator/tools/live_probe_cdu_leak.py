#!/usr/bin/env python3
"""Live check of IT thermal protection (F16): a direct-to-chip coolant leak.

    python tools/live_probe_cdu_leak.py [hold_s] [sample_s] [load_pct]

*load_pct* (0 = leave the fleet alone) pins cpu_usage on the servers of the leaking
loops. A liquid die is 35 + 0.30 x cpu_usage, so at the fleet's usual ~50 % the
leak's +38 K lands around 89 C — just under the throttle. That is not a defect: a
leak on a half-idle loop legitimately should not trip protection. Direct-to-chip
exists for BUSY nodes, and 65 % puts the die inside the throttle band, which is
exactly why the fixture test pins the same figure.

F16 is the one cooling finding closed on the fixture alone. Its real cause was a
quantisation stall — cpu_temp relaxed toward its target through `round(x, 1)`, and
at a 1 s tick the per-tick step fell below 0.05 once the gap was under 7.5 K, so
the die parked 7.5 K short of target FOREVER and 90 C was unreachable by any fault.
Fixed by keeping more precision. The fixture proves the throttle fires; this proves
the live plant can actually get there.

WHY A LEAK AND NOT LOAD. Server inlet is hard-clamped at 45 C, and an air-cooled
die is base + 0.9 x (inlet - 22), so the intake term saturates around +20.7 K
however much IT load is added — the high-load campaign peaked at 76.7 C, LOWER than
low load. A cold-plate leak is the only mechanism in the model that clears 90 C: it
adds up to +38 K directly to the die, because the plate has stopped removing heat
from the package.

PATIENCE IS THE POINT. _CPU_THERMAL_TAU_S is 150 s, so the die needs MINUTES to
approach its target. A 30 s hold proves nothing here.

Releases the leak in a `finally`. A leak latches nothing, so no reset is needed.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._live_api import DC, connect, device_ids, force, plant_points  # noqa: E402

HOLD_S = int(sys.argv[1]) if len(sys.argv) > 1 else 600
SAMPLE_S = int(sys.argv[2]) if len(sys.argv) > 2 else 60
LOAD_PCT = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
THROTTLE_C, SHUTDOWN_C = 90.0, 95.0
LEAK_PRESSURE_KPA = 140.0        # intact loop is 250; this is a fully open leak
TOPOLOGY = "topologies/dual_dc_enterprise.json"


def loop_servers(cdu_names):
    """Server names on each CDU's cold-plate loop, from the cooling-layer edges —
    the same edges the store walks to build its loop map."""
    import json
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, TOPOLOGY), encoding="utf-8") as fh:
        topo = json.load(fh)
    nodes = {n["device"].get("id", n["id"]): n["device"] for n in topo["nodes"]}
    want = set(cdu_names)
    out = set()
    for e in topo["edges"]:
        if e.get("layer") != "cooling":
            continue
        a, b = nodes.get(e["src"]) or {}, nodes.get(e["dst"]) or {}
        types = {a.get("device_type"), b.get("device_type")}
        if types != {"cdu", "server"}:
            continue
        cdu, srv = (a, b) if a.get("device_type") == "cdu" else (b, a)
        if cdu.get("name") in want:
            out.add(srv.get("name"))
    return out


def servers(get):
    devs = get("/devices")
    devs = devs["devices"] if isinstance(devs, dict) else devs
    return [d for d in devs
            if d.get("device_type") == "server" and d.get("datacenter") == DC
            and isinstance(d.get("cpu_temp"), (int, float))]


def snap(get):
    srv = servers(get)
    temps = sorted((d["cpu_temp"], d["name"]) for d in srv)
    loads = [d.get("cpu_usage") for d in srv if isinstance(d.get("cpu_usage"), (int, float))]
    off = [d["name"] for d in srv if str(d.get("power_state", "On")) == "Off"]
    return {
        "n": len(srv),
        "max": temps[-1][0] if temps else None,
        "hottest": temps[-1][1] if temps else None,
        "mean": round(sum(t for t, _ in temps) / len(temps), 1) if temps else None,
        "over_throttle": sum(1 for t, _ in temps if t >= THROTTLE_C),
        "over_shutdown": sum(1 for t, _ in temps if t >= SHUTDOWN_C),
        "load_mean": round(sum(loads) / len(loads), 1) if loads else None,
        "off": off,
    }


def line(tag, s):
    print(f"{tag:>10}  servers={s['n']}  die max={s['max']} ({s['hottest']})  "
          f"mean={s['mean']}  >=90C:{s['over_throttle']}  >=95C:{s['over_shutdown']}  "
          f"load={s['load_mean']}%  off={len(s['off'])}", flush=True)


def main() -> None:
    get, post = connect()
    ids = device_ids(get)
    cdus = sorted(n for n in ids if n.startswith("CDU") and DC in n)
    if not cdus:
        sys.exit(f"no CDUs found in {DC} — nothing to leak")
    print(f"{DC} CDUs: {cdus}", flush=True)

    base = snap(get)
    line("BASELINE", base)
    if base["load_mean"] is not None and base["load_mean"] < 55:
        print(f"   NOTE fleet load is {base['load_mean']}% — a liquid die is "
              f"35 + 0.30 x cpu_usage, so the leak's +38 K may land short of 90 C. "
              f"Direct-to-chip exists for BUSY nodes; a leak on an idle loop "
              f"legitimately should not trip anything.", flush=True)

    peak, ever_throttled = base["max"] or 0.0, set()
    pinned = []
    try:
        if LOAD_PCT > 0:
            # Pin BEFORE the leak so the die starts from the busy baseline; the
            # override is resolved in _step_device ahead of the die model, so this
            # genuinely raises cpu_usage rather than only the published metric.
            for name in sorted(loop_servers(cdus)):
                if name in ids:
                    post(f"/devices/{ids[name]}/override",
                         {"metric": "cpu_usage", "value": LOAD_PCT})
                    pinned.append(name)
            print(f"\n-- pinned cpu_usage={LOAD_PCT}% on {len(pinned)} loop servers --",
                  flush=True)
            time.sleep(SAMPLE_S)
            line("PINNED", snap(get))
        for n in cdus:
            force(post, ids, n, "Alarm_Leak", 1.0)
            force(post, ids, n, "TCS_Loop_Pressure", LEAK_PRESSURE_KPA)
        print(f"\n-- leak forced on {len(cdus)} CDUs "
              f"(loop pressure {LEAK_PRESSURE_KPA} kPa) --", flush=True)
        for i in range(HOLD_S // SAMPLE_S):
            time.sleep(SAMPLE_S)
            s = snap(get)
            line(f"t+{(i + 1) * SAMPLE_S}s", s)
            peak = max(peak, s["max"] or 0.0)
            ever_throttled |= set(s["off"])
    finally:
        for n in cdus:
            for pt in ("Alarm_Leak", "TCS_Loop_Pressure"):
                force(post, ids, n, pt, None)
        for name in pinned:
            post(f"/devices/{ids[name]}/override",
                 {"metric": "cpu_usage", "value": None})
        print(f"\n-- leak released, {len(pinned)} load pins cleared --", flush=True)

    for i in range(6):
        time.sleep(SAMPLE_S)
        s = snap(get)
        line(f"rec+{(i + 1) * SAMPLE_S}s", s)
        if (s["max"] or 99) <= (base["max"] or 0) + 2.0:
            break

    print(f"\nVERDICT  peak die {base['max']} -> {peak} C "
          f"(throttle point {THROTTLE_C}, shutdown {SHUTDOWN_C})")
    print("PASS = the die crosses 90 C and a protective response follows.")
    print("Before the round(x,1) fix the die could not come within 7.5 K of target,")
    print("so 90 C was unreachable by ANY fault and the throttle never fired.")


if __name__ == "__main__":
    main()
