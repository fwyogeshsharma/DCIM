"""Make every device's vendor the maker of its model, and its BMC port follow.

A part number has one maker. tools/fill_cold_plate_loops.py re-SKUed air servers
to liquid parts and, despite its docstring, left the vendor behind: 23 Hall A
servers were "HPE" or "Lenovo" on a Supermicro SYS-221H-TNR LCC, and their BMCs
announced iLO/XCC on a Supermicro board - a DCIM sweeping the IT-OOB network read
an identity no real machine has. That tool now follows the vendor; this repairs a
topology it already wrote.

    python tools/fix_model_vendor.py topologies/dual_dc_enterprise.json [--dry-run]

Only devices whose model is in core.device_models are touched; an unknown model
says nothing about its maker.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.device_models import follow_model_vendor  # noqa: E402


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8"))
    moved: Counter = Counter()
    for node in topo.get("nodes") or []:
        d = node.get("device", node)
        change = follow_model_vendor(d)
        if change:
            moved[f"{d.get('device_type')}: {change[0]} -> {change[1]} ({d.get('model_name')})"] += 1
            print(f"  {d['name']}: {change[0]} -> {change[1]}")
    for k, n in sorted(moved.items()):
        print(f"{n:4}  {k}")
    print(f"{sum(moved.values())} device(s) {'would change' if dry_run else 'changed'}")
    if moved and not dry_run:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--dry-run" in sys.argv[2:]))
