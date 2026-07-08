#!/usr/bin/env python3
"""Scale the cooling plant's electrical nameplates to realistic values.

The curated topology shipped tiny plant draws (a chiller at 7.3 kW — a real
water-cooled chiller is ~90-150 kW electrical), which capped the metered cooling
far below what the staged cooling model actually demands. That understated cooling
watts, per-unit chiller kW, and the mechanical power chain, and (before the staging
work) collapsed PUE toward 1.0 as the fleet outgrew the plant.

This sets each cooling device's `power_draw_w` (electrical nameplate) to a realistic
figure. Everything DOWNSTREAM re-derives automatically at load time — the mechanical
RPP / UPS / generator ratings come from Σ(downstream nameplate) ÷ 0.8 in
DeviceStateStore, so bumping the plant nameplates re-sizes the whole mech power chain
to match; the live draw is the staged-model cooling (normalised in the plant loop),
so a resized plant sits at ~80 % of its mech feed at peak instead of overloading it.

Idempotent — safe to re-run. Writes a .bak beside the topology on first change.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# Realistic full-load ELECTRICAL nameplate (W) per cooling device type. Ratios are
# what matter for the per-unit split (chiller-dominant); the sum sets the mech-RPP
# rating. A chiller's electrical ≈ its cooling tons ÷ COP (~5.5): a ~600 kW-thermal
# unit ≈ 110 kW electrical.
PLANT_NAMEPLATE_W = {
    "chiller":       110_000,   # water-cooled centrifugal, ~600 kW-thermal @ COP 5.5
    "pump":           15_000,   # CHW / condenser-water pump (VFD)
    "cooling_tower":  30_000,   # induced-draft tower fans (VFD)
    "crah":            4_000,   # CRAH EC fan (per unit; many units across the floor)
    "cdu":             4_000,   # in-row coolant distribution pumps
    # valves are actuators — negligible draw, left as-is
}


def main(path: str) -> int:
    p = Path(path)
    if not p.exists():
        print(f"topology not found: {p}", file=sys.stderr)
        return 1
    topo = json.loads(p.read_text())
    devs = topo.get("devices") or []
    changed = 0
    summary: dict[str, list[int]] = {}
    for d in devs:
        t = d.get("device_type")
        if t in PLANT_NAMEPLATE_W:
            new = PLANT_NAMEPLATE_W[t]
            if int(d.get("power_draw_w") or 0) != new:
                d["power_draw_w"] = new
                changed += 1
            summary.setdefault(t, []).append(new)
    if changed:
        bak = p.with_suffix(p.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(p, bak)
            print(f"backup → {bak.name}")
        p.write_text(json.dumps(topo, indent=2))
    for t, vals in sorted(summary.items()):
        print(f"  {t:14s} x {len(vals):3d} -> {vals[0]/1000:.0f} kW each "
              f"(sum {sum(vals)/1000:.0f} kW)")
    print(f"{'updated' if changed else 'already current'}: {changed} device(s)")
    return 0


if __name__ == "__main__":
    topo = sys.argv[1] if len(sys.argv) > 1 else "topologies/dual_dc_enterprise_floorplan.json"
    raise SystemExit(main(topo))
