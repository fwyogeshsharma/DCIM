#!/usr/bin/env python3
"""Drop the fake interface indices from power and cooling edges.

Every power and cooling edge in the topology carries src_iface/dst_iface = 0,
which reads as "this cord plugs into eth0". It does not. A power link is a C13/C14
cord from a PDU OUTLET to a PSU inlet; a cooling link is a PIPE between loop
connections. Neither terminates on an Ethernet interface, so neither has an
ifIndex to record.

The 0s were never chosen — TopologyEngine.add_link auto-allocated "the next free
interface" for every layer regardless of what the layer physically is, and every
power/cooling edge landed on iface 0. add_link no longer does this (it carries
None for non-Ethernet layers), so this migration only cleans what earlier saves
already wrote to disk.

Setting these to null loses nothing: nothing reads them. It removes a value that
LOOKS like a real termination — the port pickers, the LLDP/OSPF neighbour builders
and the SNMP/gNMI generators all index device.interfaces, and a 0 here is exactly
the kind of thing that gets picked up as a real port by the next reader.

Modelling the real terminations (PDU outlet number, PSU inlet) is a separate,
bigger job: it needs an outlet inventory per PDU and PSU slots per device, which
is what outlet-metered PDUs actually expose (Raritan PDU2-MIB outletSensorValue,
APC rPDU2OutletMetered*, ServerTech Sentry3-MIB). Null is the honest placeholder
until then — absent, rather than wrong.

Idempotent. Usage:
    python tools/clear_nonethernet_ifaces.py topologies/dual_dc_enterprise.json
    python tools/clear_nonethernet_ifaces.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

# Must match TopologyEngine.ETHERNET_LAYERS.
ETHERNET_LAYERS = ("production", "management")


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    edges = topo["edges"]

    cleared = Counter()
    for e in edges:
        layer = e.get("layer", "production")
        if layer in ETHERNET_LAYERS:
            continue
        if e.get("src_iface") is not None or e.get("dst_iface") is not None:
            cleared[layer] += 1
        e["src_iface"] = None
        e["dst_iface"] = None

    if not dry_run:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would clear" if dry_run else "Cleared"
    total = sum(cleared.values())
    print(f"{verb} the fake iface index on {total} non-Ethernet edge(s) of "
          f"{len(edges)} total. {'(dry run)' if dry_run else f'Wrote {p}'}\n")
    for layer, c in sorted(cleared.items()):
        print(f"  {c:5d}  {layer}")
    if not total:
        print("  nothing to do — already clean")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
