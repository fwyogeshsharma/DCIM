#!/usr/bin/env python3
"""Delete .snmprec datasets that no device in a topology should serve.

snmpsim resolves a request by community -> "<community>.snmprec", so every file in
the dataset directory is a LIVE AGENT whether or not the topology still contains that
device. Datasets are only ever written, never removed, so addresses from retired
topologies keep answering -- a DCIM polling one inventories hardware that does not
exist, and plant gear that must have no SNMP agent at all (chiller / pump / cooling
tower / valve / RPP -- BACnet and Modbus devices) can answer anyway if it once held
an address a server used.

The app already reaps: SNMPRecGenerator.reap_orphans runs inside the API's dataset
GENERATION job (api/routers/snmp.py). It never runs on a restart, because a restart
ADOPTS the datasets already on disk instead of rebuilding them. So a directory can
accumulate orphans for as long as nobody triggers a full regen. This is that reap,
callable on its own -- no regen, no snmpsim bounce, no running app required.

Authority is SNMPRecGenerator.snmp_bind_ips(), the same function the binder and the
generator use, imported rather than reimplemented so this cannot drift from what
actually gets served. It returns [] for the no-SNMP types, which is what makes their
stale files orphans rather than expected.

DRY RUN BY DEFAULT -- prints what it would delete and touches nothing. Pass --apply
to actually unlink. Refuses to run against an empty expectation (unloadable topology,
no devices) rather than emptying the directory.

Usage:
    python tools/reap_snmp_orphans.py topologies/dual_dc_enterprise.json
    python tools/reap_snmp_orphans.py topologies/dual_dc_enterprise.json --apply
    python tools/reap_snmp_orphans.py <topology> --datasets /path/to/datasets/snmp
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.device_manager import DeviceManager                # noqa: E402
from core.snmprec_generator import SNMPRecGenerator          # noqa: E402
from core.topology_engine import TopologyEngine              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("topology", help="topology .json the datasets must match")
    ap.add_argument("--datasets", default="datasets/snmp",
                    help="dataset directory (default: datasets/snmp)")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without this it only reports")
    ap.add_argument("--limit", type=int, default=12,
                    help="how many orphan names to list (default: 12)")
    args = ap.parse_args()

    ds_dir = Path(args.datasets)
    if not ds_dir.is_dir():
        print(f"No dataset directory at {ds_dir} — nothing to reap")
        return 1

    topo = TopologyEngine()
    dm = DeviceManager()
    topo.from_dict(json.loads(Path(args.topology).read_text(encoding="utf-8-sig")))
    for d in topo.get_all_devices():
        dm.add_device(d)

    gen = SNMPRecGenerator(output_dir=str(ds_dir))

    # The same authority reap_orphans uses, so the preview cannot disagree with what
    # --apply then deletes.
    expected = {f"{ip}.snmprec"
                for d in topo.get_all_devices()
                for ip in gen.snmp_bind_ips(d)}
    if not expected:
        print("Topology yielded NO expected datasets — refusing to run "
              "(that would empty the directory)")
        return 2

    on_disk = {p.name for p in ds_dir.glob("*.snmprec")}
    orphans = sorted(on_disk - expected)
    missing = sorted(expected - on_disk)

    print(f"topology : {args.topology}  ({topo.node_count()} devices)")
    print(f"datasets : {ds_dir}")
    print(f"on disk  : {len(on_disk)}")
    print(f"expected : {len(expected)}")
    print(f"orphans  : {len(orphans)}")
    print(f"missing  : {len(missing)}"
          + ("  (run a dataset regen — these devices have no agent file)" if missing else ""))

    if orphans:
        shown = orphans[:args.limit]
        print("\norphans:")
        for n in shown:
            print(f"  {n}")
        if len(orphans) > len(shown):
            print(f"  … and {len(orphans) - len(shown)} more")

    if not args.apply:
        print("\nDRY RUN — nothing deleted. Re-run with --apply to remove the orphans.")
        return 0

    if not orphans:
        print("\nNothing to delete.")
        return 0

    # One deletion path: the generator's own reaper, not a second implementation here.
    removed = gen.reap_orphans(topo)
    print(f"\nDeleted {len(removed)} orphaned dataset(s). "
          f"{len(on_disk) - len(removed)} file(s) remain.")
    left = len({p.name for p in ds_dir.glob('*.snmprec')} - expected)
    if left:
        print(f"WARNING: {left} orphan(s) survived (file busy or permission denied) "
              f"— re-run once snmpsim is stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
