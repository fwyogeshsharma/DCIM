"""Plug the rack environmental probes into their PDU's sensor port.

WHAT WAS WRONG
--------------
Every Raritan DPX2 probe carried its own management IP and its own SNMP agent
publishing RARITAN-PX2-MIB. That MIB is the PDU's. A DPX2 is an RJ-12 lead with a
thermistor on the end — no processor, no IP stack, no Ethernet. The PX2 polls it
over its SENSOR port and publishes it in the PDU's own external-sensor table, at
a slot. Serving it from a per-probe agent invented a network node that does not
exist, and put the Raritan table on the wrong device.

WHAT THIS DOES
--------------
For each rack holding probes:
  * chains them off the A-feed PDU (a PX2 has one sensor port; DPX2 units
    daisy-chain up to 8, which is what makes one host correct rather than a
    simplification);
  * assigns each probe a base slot, consecutive across the chain in name order,
    sized by model — T3H1 takes 4 slots (inlet/mid/exhaust/humidity), CC2 takes 2
    (water rope/temperature);
  * strips IP, SNMP and Ethernet port from the probe and records the chain on the
    PDU (sensor_children).

The probes keep their names, models and readings, so the rack thermal model and
the cold-aisle trap rules are untouched. Traps raised on a probe now source from
the host PDU, which is correct: the probe cannot send one.

Usage:
    python tools/migrate_dpx2_probes.py topologies/dual_dc_enterprise.json
    python tools/migrate_dpx2_probes.py <file> --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SLOTS_BY_MODEL = {
    "Raritan DPX2-T3H1": 4,
    "Raritan DPX2-CC2": 2,
}
DEFAULT_SLOTS = 2

# A PX2 sensor port supports a chain of 8 DPX2 units.
MAX_CHAIN = 8


def _rack(d: dict):
    return (d.get("datacenter"), d.get("room"), d.get("rack_row"), d.get("rack_num"))


def migrate(path: Path, dry_run: bool = False) -> int:
    topo = json.loads(path.read_text(encoding="utf-8"))
    nodes = topo["nodes"]
    devs = [n["device"] for n in nodes]

    probes = [d for d in devs
              if d.get("device_type") == "sensor"
              and str(d.get("model_name", "")).startswith("Raritan DPX2")
              and not d.get("host_pdu_ip")]
    if not probes:
        print("No un-migrated DPX2 probes found — nothing to do.")
        return 0

    # A-feed PDU per rack. Deterministic: the chain must not move between runs.
    pdu_by_rack: dict = {}
    for d in devs:
        if d.get("device_type") not in ("pdu", "floor_pdu"):
            continue
        pdu_by_rack.setdefault(_rack(d), []).append(d)
    for k in pdu_by_rack:
        pdu_by_rack[k].sort(key=lambda x: x.get("name", ""))

    by_rack: dict = {}
    for p in probes:
        by_rack.setdefault(_rack(p), []).append(p)

    attached, orphaned = [], []
    for rack, group in sorted(by_rack.items(), key=lambda kv: str(kv[0])):
        hosts = pdu_by_rack.get(rack) or []
        if not hosts:
            orphaned += [p["name"] for p in group]
            continue
        host = hosts[0]                       # A feed
        host_ip = host.get("mgmt_ip") or host.get("ip_address") or ""
        if not host_ip:
            orphaned += [p["name"] for p in group]
            continue
        if len(group) > MAX_CHAIN:
            print(f"  WARNING {host['name']}: {len(group)} probes exceeds the "
                  f"{MAX_CHAIN}-unit chain limit", file=sys.stderr)

        slot = 1
        chain = []
        for p in sorted(group, key=lambda x: x.get("name", "")):
            width = SLOTS_BY_MODEL.get(p.get("model_name", ""), DEFAULT_SLOTS)
            p["ip_address"] = ""
            p["mgmt_ip"] = ""
            p["snmp_port"] = 0
            p["snmp_community"] = ""
            p["metrics_enabled"] = False
            p["interfaces"] = []
            p["interface_count"] = 0
            p["interface_groups"] = []
            p["host_pdu_ip"] = host_ip
            p["sensor_slot"] = slot
            chain.append(p["name"])
            attached.append((p["name"], host["name"], slot, width,
                             p.get("model_name", "")))
            slot += width
        host["sensor_children"] = chain

    print(f"\nProbes moved onto PDU sensor ports ({len(attached)}):")
    for name, host, slot, width, model in attached:
        print(f"  {name:22s} -> {host:22s} slots {slot}-{slot + width - 1}  {model}")
    if orphaned:
        print(f"\nSkipped (no A-feed PDU with an address in the rack): {orphaned}",
              file=sys.stderr)

    if dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    backup = path.with_suffix(path.suffix + ".predpx2.bak")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nWrote {path}\nBackup {backup}")
    print(f"Released {len(attached)} management addresses.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("topology", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.topology.exists():
        print(f"No such file: {args.topology}", file=sys.stderr)
        return 2
    return migrate(args.topology, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
