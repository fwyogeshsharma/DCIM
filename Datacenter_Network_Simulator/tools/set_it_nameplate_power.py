#!/usr/bin/env python3
"""Make IT power_draw_w follow the SKU, from the nameplate catalog.

power_draw_w is the device's NOMINAL full-load nameplate — the figure the live power
cascade scales 0.55-1.0x for telemetry, the rack budget sums, and PDU/UPS/generator
load% divide by. It is a property of the hardware, so it must come from the model.

In this topology it does not. IT gear carries a random-looking sample instead, baked in
as if it were the nameplate, and disconnected from core/device_manager's catalog:

    Dell PowerEdge R7525          604W stored   ->  1000W catalog
    Lenovo ThinkSystem SR630 V2   641W stored   ->   500W catalog
    Cisco ISR 4431                  0W stored   ->   450W catalog   (!)
    PA-5220 (some)                  0W stored   ->   500W catalog   (!)

Every SKU lands in a flat ~470-760W band whatever it actually is, so a 1U single-socket
box and a 2U dual-socket AMD box draw the same, and ten devices draw NOTHING at all —
they are invisible to the cascade they should be loading.

The facility side was already fixed (CRAH 6500W, CDU 1800W, chiller 145 kW and the
pumps all match the catalog exactly — see tools/fix_plant_nameplates.py). This is the
same pass for the IT side, which was never done.

SCOPE — only the types that ADD load to the cascade:

    server, switch, router, firewall, load_balancer, oob_switch

Deliberately NOT touched:
  * pdu / ups / rpp / floor_pdu / generator / switchgear / ats / mcc / mpp — they CARRY
    power, they do not add it, so their power_draw_w is 0 by design and must stay 0.
    They are excluded by name here rather than by "catalog == 0": the catalog is
    substring-matched on model_name, and an unlucky match must not be able to power up
    a distribution node.
  * crah / chiller / pump / cooling_tower / cdu — already correct, and mechanical load
    is a separate model.
  * sensor — a few watts either way, and a DPX2 has no cord (it hangs off the PDU's
    sensor port), so its draw does not cascade through a PSU at all.
  * Any SKU the catalog does not know (returns 0) — a real stored value beats zeroing
    it out on an unknown model.

Impact, measured on this topology: IT nameplate 204.8 kW -> 235.8 kW (+15.2%). No
compute rack crosses the per-rack budget (worst 14.6 kW of 17.6 kW), and no server
crosses C13_CONTINUOUS_W, so every existing C13/C14 cord stays valid — nothing needs
re-cording. Expect PDU/UPS/generator load% and PUE to read HIGHER and, for the first
time, to differ per SKU.

Only power_draw_w changes. Nothing is re-racked, re-corded or re-SKU'd.

Idempotent: values already equal to the catalog are left alone and reported as skipped.

Usage:
    python tools/set_it_nameplate_power.py topologies/dual_dc_enterprise.json
    python tools/set_it_nameplate_power.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.device_manager import DeviceType, nameplate_power_w   # noqa: E402

# The IT loads. These are the devices whose draw the power cascade sums.
IT_TYPES = {"server", "switch", "router", "firewall", "load_balancer", "oob_switch"}


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    # Grouped per SKU, not per device: 400 lines of "637W -> 700W" hides the one thing
    # worth reading, which is what each MODEL now draws and how far it moved.
    changed: dict = {}
    skipped, unknown = 0, Counter()
    before = after = 0
    zeroed = 0
    for n in nodes:
        d = n["device"]
        t = d.get("device_type")
        if t not in IT_TYPES:
            continue
        model = d.get("model_name") or ""
        cat = nameplate_power_w(DeviceType(t), model)
        if not cat:
            unknown[f"{t}: {model or '(no model)'}"] += 1
            continue                       # unknown SKU — keep whatever is there
        old = d.get("power_draw_w") or 0
        before += old
        after += cat
        if old == cat:
            skipped += 1
            continue
        if old == 0:
            zeroed += 1
        d["power_draw_w"] = cat
        g = changed.setdefault(model or t, {"n": 0, "lo": old, "hi": old, "cat": cat})
        g["n"] += 1
        g["lo"] = min(g["lo"], old)
        g["hi"] = max(g["hi"], old)

    total = sum(g["n"] for g in changed.values())
    if not dry_run and total:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would set" if dry_run else "Set"
    print(f"\n{verb} the nameplate on {total} IT device(s) from the SKU catalog; "
          f"{skipped} already correct."
          f" {'(dry run)' if dry_run else (f'Wrote {p}' if total else 'No change')}\n")
    if changed:
        print(f"  {'n':>4}  {'model':30s} {'was':>14}   {'now':>7}")
        for m, g in sorted(changed.items()):
            was = f"{g['lo']}W" if g['lo'] == g['hi'] else f"{g['lo']}..{g['hi']}W"
            print(f"  {g['n']:4d}  {m:30s} {was:>14} ->{g['cat']:>6}W")
    if total:
        print(f"\n  IT nameplate load: {before/1000:.1f} kW -> {after/1000:.1f} kW "
              f"({100*(after-before)/before:+.1f}%)")
        if zeroed:
            print(f"  {zeroed} device(s) drew 0W and were invisible to the power "
                  f"cascade — they now load it.")
    if unknown:
        print(f"\n  Left alone — the catalog has no nameplate for these SKUs, so the "
              f"stored value stands:")
        for k, c in sorted(unknown.items()):
            print(f"  {c:4d}  {k}")
    print()
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
