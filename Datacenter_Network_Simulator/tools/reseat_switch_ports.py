#!/usr/bin/env python3
"""Give every Ethernet link its own port, and materialise ports that were only a count.

Two defects, one root cause — a link's port was never actually chosen:

1. PORT-0 PILEUP. 638 of 1127 ethernet links share a port with another link, because
   the far end defaulted to iface 0 instead of being allocated. An OOB switch ends up
   with 33 console links on GigabitEthernet0/0 — one RJ45, thirty-three cables. Over
   SNMP that switch reports one connected port and 49 idle ones, and its LLDP table
   claims 33 neighbours on a single interface. This is the same defect that made a
   spine's port list unreadable, seen from the other end of the cable.

2. PHANTOM PORTS. The BMS cores (OOBM1-*-CP) carry interface_count=48 and
   interface_groups=48 but only 14 Interface objects — a count was raised without
   materialising the ports behind it. Links then reference ports 49..57 that cannot
   exist. A Catalyst 9300-48T has 48 ports, so the count is right and the list is
   what is missing.

WHAT IS PRESERVED: a link already sitting alone on a real port keeps that port.
Only the links that collide, or point past the end of the port list, are moved —
so this is not a reshuffle of working cabling.

DETERMINISM: displaced links are re-seated in peer-name order onto the lowest free
ports, so a re-run reproduces the same assignment instead of churning the topology.

Refuses to write if any device would need more ports than it has.

Usage:
    python tools/reseat_switch_ports.py topologies/dual_dc_enterprise.json
    python tools/reseat_switch_ports.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ETHERNET_LAYERS = ("production", "management")


def _mac(seed: str) -> str:
    b = bytearray(hashlib.md5(seed.encode()).digest()[:6])
    b[0] = (b[0] & 0xFE) | 0x02          # locally administered, unicast
    return ":".join(f"{x:02x}" for x in b)


def _iface_name(existing: list, idx0: int) -> str:
    """Name port idx0 the way this device already names its ports."""
    if existing:
        proto = existing[0]["name"]
        for prefix, sep in (("GigabitEthernet0/", ""), ("eth1/", "")):
            if proto.startswith(prefix):
                # GigabitEthernet0/N is 0-based, eth1/N is 1-based
                n = idx0 if prefix.startswith("Gigabit") else idx0 + 1
                return f"{prefix}{n}"
    return f"GigabitEthernet0/{idx0}"


def materialise(d: dict) -> int:
    """Append Interface objects until the list matches interface_count."""
    ifs = d.get("interfaces") or []
    want = d.get("interface_count") or len(ifs)
    if len(ifs) >= want:
        return 0
    speed = ifs[0]["speed"] if ifs else 1_000_000_000
    added = 0
    for i in range(len(ifs), want):
        ifs.append({
            "index": i + 1,
            "name": _iface_name(ifs, i),
            "speed": speed,
            "oper_status": 1,
            "in_octets": 0, "out_octets": 0,
            "in_errors": 0, "out_errors": 0,
            "in_discards": 0, "out_discards": 0,
            "mac_address": _mac(f"{d['name']}:{i}"),
            "connected_to_device": None, "connected_to_iface": None,
            "role": "data",
        })
        added += 1
    d["interfaces"] = ifs
    return added


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n["device"] for n in nodes}

    # 1. materialise ports that exist only as a count
    made = Counter()
    for n in nodes:
        d = n["device"]
        a = materialise(d)
        if a:
            made[f"{d['name']} ({d.get('model_name')})"] = a

    # 2. index every ethernet termination: device -> [(edge, key)]
    terms = defaultdict(list)
    for e in edges:
        if e.get("layer") not in ETHERNET_LAYERS:
            continue
        if e.get("src_iface") is not None:
            terms[e["src"]].append((e, "src_iface"))
        if e.get("dst_iface") is not None:
            terms[e["dst"]].append((e, "dst_iface"))

    # 3. capacity gate — never write a topology that cannot physically exist
    over = []
    for nid, ts in terms.items():
        n_ports = len(byid[nid].get("interfaces") or [])
        if len(ts) > n_ports:
            over.append(f"{byid[nid]['name']}: {len(ts)} links, {n_ports} ports")
    if over:
        print(f"REFUSING TO WRITE — {len(over)} device(s) have more links than ports:")
        for o in over[:20]:
            print(f"   {o}")
        return 1

    # 4. re-seat only what is broken
    moved = Counter()
    detail = []
    for nid, ts in terms.items():
        d = byid[nid]
        n_ports = len(d.get("interfaces") or [])
        seen: dict = {}          # port -> the (edge,key) that keeps it
        displaced = []
        for e, key in ts:
            port = e[key]
            if port < n_ports and port not in seen:
                seen[port] = (e, key)      # first valid claimant keeps its seat
            else:
                displaced.append((e, key))
        if not displaced:
            continue
        # deterministic: peer-name order onto the lowest free ports
        def peer_name(item):
            e, key = item
            other = e["dst"] if key == "src_iface" else e["src"]
            return byid[other]["name"]
        displaced.sort(key=peer_name)
        free = (i for i in range(n_ports) if i not in seen)
        for e, key in displaced:
            try:
                port = next(free)
            except StopIteration:
                print(f"REFUSING — {d['name']} ran out of ports mid-reseat")
                return 1
            e[key] = port
            seen[port] = (e, key)
            moved[d["device_type"]] += 1
            if len(detail) < 6:
                detail.append(f"{d['name']}: {peer_name((e, key))} -> port {port}")

    if not dry_run:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would materialise" if dry_run else "Materialised"
    print(f"{verb} {sum(made.values())} interface(s) on {len(made)} device(s):")
    for k, v in sorted(made.items()):
        print(f"   +{v:3d}  {k}")
    verb = "Would re-seat" if dry_run else "Re-seated"
    print(f"\n{verb} {sum(moved.values())} link(s) onto their own port:")
    for k, v in sorted(moved.items()):
        print(f"   {v:5d}  {k}")
    if detail:
        print("\n   e.g. " + "\n        ".join(detail))
    print(f"\n{'(dry run)' if dry_run else f'Wrote {p}'}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
