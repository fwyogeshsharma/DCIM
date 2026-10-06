"""Give every Ethernet cable its own port.

One switch port takes one cable. OOBM1-DC1-CP and OOBM1-DC2-CP each had three
management cables on port 0 - an energy monitor, a Modbus gateway and a BACnet
router - because add_link trusted a stale per-interface cache when it picked a
"free" port (fixed in core.topology_engine). An importer that terminates cables
on ports refused the third every time.

The first cable on a doubled port stays; each extra one moves to the lowest port
on that device with no cable at all. Only the doubled end moves - the far end
(the device's own NIC) is untouched.

    python tools/fix_doubled_ports.py topologies/dual_dc_enterprise.json [--dry-run]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ETHERNET = ("production", "management")


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8"))
    nodes = {n["id"]: n["device"] for n in topo["nodes"]}
    edges = topo.get("edges") or topo.get("links") or []
    used: dict = defaultdict(set)
    seen: dict = defaultdict(list)          # (device, iface) -> [(edge, end)]
    for e in edges:
        if e.get("layer") not in ETHERNET:
            continue
        for end in ("src", "dst"):
            i = e.get(f"{end}_iface")
            if i is not None:
                used[e[end]].add(i)
                seen[(e[end], i)].append((e, end))
    moved = 0
    for (dev_id, iface), cables in sorted(seen.items(), key=lambda kv: (nodes[kv[0][0]]["name"], kv[0][1])):
        for e, end in cables[1:]:
            n_ports = len(nodes[dev_id].get("interfaces") or [])
            free = next((i for i in range(n_ports) if i not in used[dev_id]), None)
            peer = nodes[e["dst" if end == "src" else "src"]]["name"]
            if free is None:
                print(f"  !! {nodes[dev_id]['name']} port {iface}: no free port for {peer}")
                continue
            print(f"  {nodes[dev_id]['name']}: {peer} port {iface} -> {free}")
            e[f"{end}_iface"] = free
            used[dev_id].add(free)
            moved += 1
    print(f"{moved} cable(s) {'would move' if dry_run else 'moved'}")
    if moved and not dry_run:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--dry-run" in sys.argv[2:]))
