#!/usr/bin/env python3
"""Re-SKU the 3U/4U servers to 2U boxes so the racked estate matches real SKU heights.

Server height is per-SKU (core/device_models.py MODEL_U_HEIGHT): a DL360 is 1U, a
DL560 4U. The topology was built when every server was assumed 2U, and placed them all
on a 2U cadence (odd units U1, U3, U5…). Under real heights that cadence is only valid
for boxes of 2U or less:

    1U box on the 2U cadence   -> fits, leaves a 1U gap above it. Fine.
    2U box on the 2U cadence   -> fits exactly. Fine.
    3U/4U box on the 2U cadence-> its body runs 1-2U INTO the server above it.

85 servers are 3U/4U SKUs, which is 134 overlapping U across 18 racks — a rack
elevation that cannot be built. There are two ways to resolve that:

  (a) re-seat every server bottom-up at its real height. Faithful, but 5 racks then
      need 42-44U of a 40U face, so ~9 servers must move to a DIFFERENT rack, and
      "servers per rack" stops being a constant the capacity design can rely on.
  (b) THIS: swap the 3U/4U SKUs for 2U boxes from the SAME vendor. Every server is
      then 1U or 2U, the existing cadence is valid as-is, nothing is re-seated, no
      rack overflows, and every device keeps its position.

(b) is what this does. The code supports 1U/2U/3U/4U everywhere (the picker, the
add-device span check and the fleet placer all read MODEL_U_HEIGHT), so a 3U/4U server
added by hand or by fleet from here on is placed correctly — this only cleans up the
tall boxes that were already racked on the flat-2U assumption.

WHAT THIS DELIBERATELY DOES NOT TOUCH:

  interfaces  Only model_name changes. A server's OOB edge lands on its LAST port
              (iface 2 or 4 — the BMC), so regenerating ports to match the new SKU
              would move the BMC and leave that management edge pointing at a port
              that no longer exists. The port layout came from set_server_ports.py and
              already differs from the model registry; leaving it alone keeps every
              edge valid. Device.from_dict honours explicit interfaces, so nothing
              regenerates on load.
  power_draw_w  Server power in this topology is randomised ~470-760W and already
              ignores the SKU (the 4U dense-GPU AS-4124GS reads ~500W where the
              catalog says 6000W). Re-deriving it here would be a half-fix of a
              separate defect — power-follows-SKU is its own change.
  rack_unit   Nothing moves. That is the entire point of choosing (b).

Idempotent: a topology with no 3U/4U servers left is reported and untouched.

Usage:
    python tools/resku_tall_servers.py topologies/dual_dc_enterprise.json
    python tools/resku_tall_servers.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.device_models import MODEL_U_HEIGHT          # noqa: E402
from core.rack_capacity import SERVER_U_HEIGHT         # noqa: E402

# 3U/4U SKU -> the 2U box that replaces it. Same vendor in every case, so the fleet
# keeps its vendor mix (and its sysDescr/sysOID stay plausible for the hardware). The
# replacement is the closest 2U machine in that vendor's own line-up.
RESKU = {
    "HPE ProLiant DL560 Gen10":    "HPE ProLiant DL380 Gen11",   # 4U 4-socket -> 2U 2-socket
    "Dell PowerEdge R940":         "Dell PowerEdge R7525",       # 3U 4-socket -> 2U 2-socket
    "Lenovo ThinkSystem SR860 V2": "Lenovo ThinkSystem SR650 V2",# 4U 4-socket -> 2U 2-socket
    "Supermicro AS-4124GS-TNR":    "Supermicro SYS-220U-TNR",    # 4U GPU     -> 2U 2-socket
    "IBM System x3850 X6":         "IBM Power System S922",      # 4U 4-socket-> 2U
}


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    # Guard the map itself: every replacement must really be <= 2U, or this trades one
    # overlap for another.
    for old, new in RESKU.items():
        h = MODEL_U_HEIGHT.get(new)
        if h is None or h > SERVER_U_HEIGHT:
            print(f"ERROR: replacement {new!r} for {old!r} is {h}U — must be "
                  f"<= {SERVER_U_HEIGHT}U. Fix RESKU.")
            return 1

    done = Counter()
    for n in nodes:
        d = n["device"]
        if d.get("device_type") != "server":
            continue
        old = d.get("model_name") or ""
        if MODEL_U_HEIGHT.get(old, SERVER_U_HEIGHT) <= SERVER_U_HEIGHT:
            continue                       # already 1U/2U — nothing to do
        new = RESKU.get(old)
        if new is None:
            print(f"  !! {d['name']}: {old} is "
                  f"{MODEL_U_HEIGHT.get(old)}U but has no RESKU entry — left as-is")
            continue
        d["model_name"] = new              # ONLY this. See the docstring.
        done[f"{old}  ->  {new}"] += 1

    if not dry_run and done:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    total = sum(done.values())
    verb = "Would re-SKU" if dry_run else "Re-SKU'd"
    print(f"\n{verb} {total} server(s) from a 3U/4U SKU to a 2U one."
          f" {'(dry run)' if dry_run else (f'Wrote {p}' if done else 'No change')}\n")
    for k, c in sorted(done.items()):
        print(f"  {c:4d}  {k}")
    if total:
        print(f"\n  Every server is now 1U or 2U, so the existing 2U placement cadence "
              f"holds and nothing was re-seated.")
    print()
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
