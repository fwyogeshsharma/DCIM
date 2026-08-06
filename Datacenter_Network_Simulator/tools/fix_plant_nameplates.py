#!/usr/bin/env python3
"""Give the cooling plant its real nameplates, and re-size the electrical gear that
now has to carry them.

THE PROBLEM
-----------
Every plant device carried a placeholder nameplate one to two orders of magnitude
below its spec sheet:

    Carrier 19DV 800kW chiller        7 335 W     (real: ~145 000 W)
    Grundfos condenser-water pump     1 359 W     (real:  ~18 500 W)
    BAC PT2 cooling tower               733 W     (real:  ~30 000 W)
    Vertiv Liebert PCW 100kW CRAH     2 091 W     (real:   ~6 500 W)

The live per-unit kW that DCIM sees was never wrong — _compute_power_flow
normalises the running plant's draws so their sum equals the staged cooling model.
But the nameplates are what a DCIM asset database reports, what sizes the gensets
(core/power_sizing sums plant draw into the whole-DC total), and what sets each
VFD's duty fraction. A 7.3 kW chiller made the mechanical load invisible.

WHAT CHANGES
------------
Nameplates come from the per-SKU catalog (core.device_manager._MODEL_NAMEPLATE_W),
so they follow the model rather than being sprinkled per device. The chiller's
figure is derived from the model's own design efficiency: 800 kW cooling ÷ COP 5.5
(cooling_model.CHILLER_COP_RATED) ≈ 145 kW, i.e. ~0.64 kW/ton.

Two knock-on corrections, both forced by the arithmetic:

  * The primary chilled-water pumps are re-SKU'd from a Grundfos NB 65-200 to an
    NB 100-200. An 800 kW chiller needs ~115 m3/h of chilled water at a 6 K delta-T;
    the 65-200 frame will not pass that.

  * The gear above the plant was rated against the OLD, invisible mechanical load
    and is now undersized:

        connected mech load        745 kW  (both MCC buses)
        MCC  800 A =  499 kW   ->  1600 A =  997 kW   one MCC carries BOTH buses
                                                      when the tie closes
        ATS 3000 A = 1870 kW   ->  4000 A = 2490 kW   1200 kW UPS + 745 kW mech
        gen  1500 kW           ->  2000 kW            facility design ~1945 kW,
                                                      and either genset alone must
                                                      carry the site

    Their rated_power_w is cleared so it re-derives from the catalog at load.

The nameplates are consistent with the cooling model rather than invented: at
PUE 1.5 a 1200 kW-IT datacenter runs ~600 kW of cooling, against 745 kW of
connected nameplate — the usual diversity between connected and running load.

Idempotent. Run after fix_cooling_trains.py, then re-export the floorplan.

Usage:
    python tools/fix_plant_nameplates.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.device_manager import nameplate_power_w, DeviceType  # noqa: E402

PLANT_TYPES = ("chiller", "pump", "cooling_tower", "crah", "cdu")

# Re-SKU: [(device_type, name prefix or None, old model substring, new model)]
RESKU = [
    ("pump",      "CHWP", "nb 65-200",           "Grundfos NB 100-200"),
    # CHWP4, the header standby, carried a TP 100-360 — the CONDENSER-water SKU. It
    # is a chilled-water pump and must be the same frame as the trains it backs up,
    # otherwise it cannot substitute for a failed evaporator pump. Same 18.5 kW.
    ("pump",      "CHWP", "tp 100-360",          "Grundfos NB 100-200"),
    ("mcc",       None,   "mcc 800a",            "Eaton Freedom 2100 MCC 1600A"),
    ("ats",       None,   "7000 series 3000a",   "ASCO 7000 Series 4000A"),
    ("generator", None,   "3512c",               "Caterpillar 3516B"),
]


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    resku = 0
    for n in nodes:
        d = n["device"]
        model = (d.get("model_name") or "").lower()
        for dtype, prefix, old, new in RESKU:
            if d["device_type"] != dtype or old not in model:
                continue
            if prefix and not (d.get("name") or "").startswith(prefix):
                continue
            print(f"  re-SKU {d['name']:16s} {d['model_name']} -> {new}")
            d["model_name"] = new
            d["rated_power_w"] = None      # re-derive the throughput rating at load
            resku += 1
            break

    stamped = []
    for n in nodes:
        d = n["device"]
        if d["device_type"] not in PLANT_TYPES:
            continue
        w = nameplate_power_w(DeviceType(d["device_type"]), d.get("model_name") or "")
        if w <= 0:
            print(f"  !! no catalog nameplate for {d['name']} "
                  f"({d.get('model_name')!r}) — left at {d.get('power_draw_w')} W")
            continue
        old = d.get("power_draw_w")
        if old != w:
            stamped.append((d["name"], old, w))
        d["power_draw_w"] = w

    by_type: dict = {}
    for name, old, new in stamped:
        t = next(n["device"]["device_type"] for n in nodes if n["device"]["name"] == name)
        by_type.setdefault(t, []).append((old, new))
    for t, vals in sorted(by_type.items()):
        olds = sorted({o for o, _ in vals})
        print(f"  {t:14s} n={len(vals):3d}  {olds} W -> {vals[0][1]} W")

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nRe-SKU'd {resku} devices, re-stamped {len(stamped)} nameplates. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
