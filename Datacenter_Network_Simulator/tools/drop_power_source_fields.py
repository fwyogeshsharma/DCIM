#!/usr/bin/env python3
"""Strip the dead power_source / power_source_a / power_source_b fields from a topology.

Which PDU feeds a device is a fact about the CORD, so it is read from the power edge —
`TopologyEngine.power_feeds` returns psu index -> {supply_id, supply_name, supply_model,
outlet, feed A|B}. `Device` no longer carries a mirrored field, for the same reason
`PowerSupply` never carried a `feed` one:

    "Cached here they would be right until the first re-cord and wrong forever after —
     the same trap as Interface.connected_to_device."   (core/device_manager.py)

These fields WERE that trap, and had drifted four ways before removal: 14 network devices
naming the wrong HALL's PDUs (their pod was cloned from another hall — the cords were
redone, the record came along), 6 CDUs corded but unrecorded, 55 records for cords that
did not exist, 72 references to deleted devices. Every cord was correct; only the cache
lied. Nothing read them at runtime — Redfish (`redfish_data_generator.power`),
`/topology/devices/{id}/power-terminations` and the floor-plan export all already went to
`power_feeds`, so the fields were write-only drift.

`Device.from_dict` drops unknown keys, so a topology still carrying them loads fine — this
is housekeeping, not a migration. It runs so the file stops shipping a stale answer to a
question the edges already answer, and so nothing is tempted to trust it again.

`power_source` (the plain one) was already dead on arrival: present on 627 nodes in the
shipped topology, non-empty on ZERO of them, and read by nothing since it was added.

Idempotent: a topology with none of the fields reports 0 and writes nothing.

Usage:
    python tools/drop_power_source_fields.py topologies/dual_dc_enterprise.json
    python tools/drop_power_source_fields.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

DEAD = ("power_source", "power_source_a", "power_source_b")


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    removed, had_value = Counter(), Counter()
    for n in nodes:
        d = n["device"]
        for k in DEAD:
            if k in d:
                if d.get(k):
                    had_value[k] += 1
                del d[k]
                removed[k] += 1

    total = sum(removed.values())
    if not dry_run and total:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would drop" if dry_run else "Dropped"
    print(f"\n{verb} {total} dead power_source field(s) from {len(nodes)} device(s)."
          f" {'(dry run)' if dry_run else (f'Wrote {p}' if total else 'No change')}\n")
    for k in DEAD:
        if removed[k]:
            print(f"  {removed[k]:4d}  {k:16s} ({had_value[k]} carried a value, "
                  f"{removed[k]-had_value[k]} were already empty)")
    if total:
        print("\n  The cords are unchanged — every power edge still carries "
              "supply_node/load_node/outlet/psu.\n  Ask TopologyEngine.power_feeds(id) "
              "for a device's A/B feed.")
    print()
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
