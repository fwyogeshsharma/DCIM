#!/usr/bin/env python3
"""Right-size the electrical plant to the halls' full build-out load, and make
both DCs' UPS + generator fleets identical.

Before: UPS/gen were sized to the CURRENT (partly-filled) load and differed per
DC (DC1 320 kW UPS, DC2 245 kW; mixed generator SKUs). The cooling plant is
already sized to full build-out (3×800 kW chillers) — this brings the electrical
plant to the same philosophy.

Build-out target per DC: 78 racks × 12 kW = 936 kW IT; facility ≈ 1.3 MW with
cooling + house. Design (identical both DCs):
  * UPS       — 2N dual bus, 2 × Vertiv Liebert EXL S1 1200 kVA (1200 kW each);
                either bus alone backs the 936 kW build-out IT load.
  * Generator — 2N, 2 × Caterpillar 3512C (1500 kW each); either genset alone
                backs the ~1.3 MW facility load.

Only re-SKUs the existing UPS/generator nodes (model + vendor); no new nodes, no
rewiring. rated_power_w is cleared so it re-derives from the catalog. Idempotent.

Usage:
    python tools/resize_electrical_plant.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

UPS_MODEL = "Vertiv Liebert EXL S1 1200kVA"
UPS_VENDOR = "Vertiv (Liebert)"
GEN_MODEL = "Caterpillar 3512C"
GEN_VENDOR = "Caterpillar"


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    changed = Counter()
    for n in topo["nodes"]:
        dv = n["device"]
        if dv["device_type"] == "ups":
            dv["model_name"] = UPS_MODEL
            dv["vendor"] = UPS_VENDOR
            dv["rated_power_w"] = None          # re-derive 1200 kW from catalog
            changed[(dv["datacenter"], "ups")] += 1
        elif dv["device_type"] == "generator":
            dv["model_name"] = GEN_MODEL
            dv["vendor"] = GEN_VENDOR
            dv["rated_power_w"] = None          # re-derive 1500 kW from catalog
            changed[(dv["datacenter"], "generator")] += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    for (dc, t), c in sorted(changed.items()):
        model = UPS_MODEL if t == "ups" else GEN_MODEL
        print(f"{dc}: {c} × {t} -> {model}")
    print(f"\nWrote {p}\nNext: python tools/export_dcim_floorplan.py {path} "
          f"{path.replace('.json','_floorplan.json')}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
