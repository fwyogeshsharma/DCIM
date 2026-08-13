"""Re-wire the graph after the field-device migrations.

The three migrations stripped IPs and Ethernet ports from 50 field devices but
left their EDGES untouched, so the topology still draws them cabled to OOB
switches they have no port for — and the carriers that replaced them (Modbus
gateways, BACnet/IP routers) were created with no edges at all, floating.

This pass fixes both ends:

  1. Every portless field device loses its `management` edge. It has no Ethernet
     port; the link was a leftover.
  2. It gains a `fieldbus` edge to its CARRIER — the PDU sensor port, the Modbus
     gateway, or the BACnet/IP router that actually reaches it.
  3. Every carrier gains the `management` edge to the room's OOB switch that its
     children used to hold. The carrier is the thing with the Ethernet port, so
     the uplink belongs to it.

`fieldbus` is a new layer on purpose. It must NOT be `management`: TopologyEngine
treats management as an Ethernet layer and allocates an interface on both ends,
which is precisely the "lie that reads back as a real termination" its own
_add_link comment warns about. Anything outside ETHERNET_LAYERS carries no
interface, which is what an RJ-12 sensor lead and an RS-485 drop are.

Usage:
    python tools/fix_fieldbus_edges.py topologies/dual_dc_enterprise.json [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
from pathlib import Path

CARRIER_TYPES = ("modbus_gateway", "bacnet_router")


def carrier_ip(dev: dict) -> str:
    """The address of whatever carries this device, or "" if it stands alone."""
    return (dev.get("host_pdu_ip")
            or dev.get("modbus_gateway_ip")
            or dev.get("mstp_router_ip")
            or "")


def migrate(path: Path, dry_run: bool = False) -> int:
    topo = json.loads(path.read_text(encoding="utf-8"))
    nodes, edges = topo["nodes"], topo["edges"]
    by_id = {n["id"]: n["device"] for n in nodes}
    id_of_ip = {d.get("mgmt_ip"): i for i, d in by_id.items() if d.get("mgmt_ip")}
    id_of_name = {d["name"]: i for i, d in by_id.items()}

    portless = {i: d for i, d in by_id.items()
                if carrier_ip(d) and not d.get("mgmt_ip") and not d.get("ip_address")}
    carriers = {i: d for i, d in by_id.items() if d["device_type"] in CARRIER_TYPES}
    if not portless and not carriers:
        print("Nothing to re-wire.")
        return 0

    # 1. Drop management edges on portless devices, remembering the OOB switch
    #    they used — that is the uplink their carrier should inherit.
    uplink_votes: dict = collections.defaultdict(collections.Counter)
    kept, dropped = [], 0
    for e in edges:
        s, t, layer = e["src"], e["dst"], e.get("layer")
        ends = [x for x in (s, t) if x in portless]
        if layer == "management" and ends:
            other = t if s in ends[0:1] and s == ends[0] else s
            other = t if s == ends[0] else s
            cip = carrier_ip(by_id[ends[0]])
            if cip in id_of_ip:
                uplink_votes[id_of_ip[cip]][other] += 1
            dropped += 1
            continue
        kept.append(e)
    edges[:] = kept

    # 2. fieldbus edge: portless device -> its carrier.
    have = {(e["src"], e["dst"], e.get("layer")) for e in edges}
    added_fb = 0
    for i, d in portless.items():
        cid = id_of_ip.get(carrier_ip(d))
        if cid is None:
            print(f"  WARNING {d['name']}: carrier {carrier_ip(d)} not in topology",
                  file=sys.stderr)
            continue
        if (i, cid, "fieldbus") in have or (cid, i, "fieldbus") in have:
            continue
        edges.append({"src": cid, "dst": i, "src_iface": None, "dst_iface": None,
                      "layer": "fieldbus"})
        have.add((cid, i, "fieldbus"))
        added_fb += 1

    # 3. Carrier -> OOB switch on the management layer.
    added_mgmt = 0
    for cid, d in carriers.items():
        if any(e.get("layer") == "management" and cid in (e["src"], e["dst"])
               for e in edges):
            continue
        oob = None
        if uplink_votes.get(cid):
            oob = uplink_votes[cid].most_common(1)[0][0]
        else:
            # Fall back to an OOB switch in the same room.
            for oid, od in by_id.items():
                if (od["device_type"] == "oob_switch"
                        and od.get("datacenter") == d.get("datacenter")
                        and od.get("room") == d.get("room")):
                    oob = oid
                    break
        if oob is None:
            print(f"  WARNING {d['name']}: no OOB switch found for its room",
                  file=sys.stderr)
            continue
        edges.append({"src": oob, "dst": cid, "src_iface": 0, "dst_iface": 0,
                      "layer": "management"})
        added_mgmt += 1
        print(f"  uplink  {d['name']:18s} -> {by_id[oob]['name']} (management)")

    print(f"\ndropped stale management edges on portless devices: {dropped}")
    print(f"added fieldbus edges (device -> carrier):            {added_fb}")
    print(f"added management edges (carrier -> OOB switch):      {added_mgmt}")

    if dry_run:
        print("\n--dry-run: nothing written.")
        return 0
    backup = path.with_suffix(path.suffix + ".prefieldbus.bak")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nWrote {path}\nBackup {backup}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("topology", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.topology.exists():
        print(f"No such file: {a.topology}", file=sys.stderr)
        return 2
    return migrate(a.topology, a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
