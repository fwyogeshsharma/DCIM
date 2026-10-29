#!/usr/bin/env python3
"""Give every load in a topology the nameplate its SKU is rated at.

    python tools/restamp_nameplates.py topologies/dual_dc_enterprise.json [--dry-run]

A topology carries `power_draw_w` per device, and the catalog's per-SKU
figure is only consulted when that field is empty. So a device stamped with
a per-TYPE default when it was generated keeps that default for ever, even
after the catalog learns its SKU: every switch here sat at the 250 W a ToR
guess gave it, a 64 x 100G spine included, and the Power page budgeted the
rack off that number.

This re-stamps a load whose figure is still the per-type default with the
catalog's per-SKU nameplate (device_manager.nameplate_power_w), and leaves
alone any device somebody set deliberately - a figure that is neither the
type default nor the catalog's is an operator's and stays. Only loads: a
strip, a panel or a UPS carries power and is rated by throughput, not draw.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.device_manager import DEFAULT_NAMEPLATE_W, DeviceType, nameplate_power_w  # noqa: E402

LOAD_TYPES = {"server", "switch", "router", "firewall", "load_balancer", "oob_switch", "cdu"}


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    changed: Counter = Counter()
    kept: Counter = Counter()
    for node in topo["nodes"]:
        d = node["device"]
        dtype = d.get("device_type")
        if dtype not in LOAD_TYPES:
            continue
        try:
            dt = DeviceType(dtype)
        except ValueError:
            continue
        model = d.get("model_name") or ""
        have = int(d.get("power_draw_w") or 0)
        want = int(nameplate_power_w(dt, model) or 0)
        default = int(DEFAULT_NAMEPLATE_W.get(dt, 0))
        if not want or want == have:
            continue
        if have and have != default:
            kept[f"{model}: {have} W (operator's figure, catalog says {want})"] += 1
            continue
        changed[f"{model}: {have} -> {want} W"] += 1
        d["power_draw_w"] = want
    total = sum(changed.values())
    if total and not dry_run:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would re-stamp" if dry_run else "Re-stamped"
    print(f"{verb} {total} load(s). {'(dry run)' if dry_run else (f'Wrote {p}' if total else 'No change')}")
    for k, c in sorted(changed.items()):
        print(f"  {c:4d}  {k}")
    if kept:
        print("\n  Left alone - a figure somebody set:")
        for k, c in sorted(kept.items()):
            print(f"  {c:4d}  {k}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
