#!/usr/bin/env python3
"""Assign each hall's IT gear to the out-of-band switch that serves its RACK, and
stop over-filling one switch while others sit nearly empty.

THE PROBLEM

    DC1 Server Hall A            managed   ports
      OOB1-DC1-HA-R1-03              16      50
      OOB2-DC1-HA-R1-03              52      50   <- 2 over, before uplinks
      OOB3-DC1-HA-R1-03              38      50
      OOB4-DC1-HA-R1-03               6      50
      OOB5-DC1-HA-R1-03               3      50

OOB2 carried 46 servers, 4 leaves and 2 rack PDUs — more management ports than the
switch has — while OOB4 and OOB5 sat almost idle. And the assignment was scattered:
rack R2-01's devices were split across four different OOB switches, R2-05's across
four more. No structured cable plant looks like that.

A real hall patches every device in a rack to the OOB switch serving that rack.
This groups devices by rack and hands each OOB a CONTIGUOUS block of racks, sized
so no switch exceeds its port count once its uplinks are counted:

    OOB1  <- R1-01, R1-02      (network row)
    OOB2  <- R1-03, R2-01
    OOB3  <- R2-02, R2-03
    ...

Only IT gear moves. Facility devices (CRAH, CDU, MPP, environmental sensors) live
on the hall's BMS switch and are not touched — see separate_bms_network.py. Uplinks
to the OOB cores are left alone, and are reserved against the port budget rather
than being allowed to overflow it.

`fleet_lifecycle._oob_port_cap()` reads interface_count to decide when a hall needs
another OOB stacked in, so an over-filled switch was already lying to the fleet
logic about how much room it had.

Idempotent: re-running reproduces the same assignment (racks sort deterministically).
Re-export the floorplan afterwards.

Usage:
    python tools/rebalance_oob_ports.py topologies/dual_dc_enterprise.json
    python tools/rebalance_oob_ports.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

# IT device classes managed by the IT out-of-band network. Facility gear (crah, cdu,
# mpp, sensor, energy_monitor, and the whole electrical plant) belongs to the BMS.
IT_TYPES = {"server", "switch", "router", "firewall", "load_balancer", "pdu"}


def is_it_oob(d) -> bool:
    n = d.get("name") or ""
    return (d["device_type"] == "oob_switch"
            and not n.startswith("OOBC") and not n.startswith("OOBM")
            and not n.startswith("BMSC"))


def chunk_racks(racks, counts, n):
    """Split *racks* (in order) into *n* contiguous groups of near-equal load."""
    total = sum(counts[r] for r in racks)
    target = total / max(1, n)
    groups, cur, cur_n = [], [], 0
    for i, r in enumerate(racks):
        remaining_groups = n - len(groups)
        racks_left = len(racks) - i
        # Close the group when it is at target, but never leave a later group empty.
        if cur and remaining_groups > 1 and racks_left < remaining_groups:
            groups.append(cur)
            cur, cur_n = [], 0
        elif cur and remaining_groups > 1 and cur_n + counts[r] / 2 >= target:
            groups.append(cur)
            cur, cur_n = [], 0
        cur.append(r)
        cur_n += counts[r]
    if cur:
        groups.append(cur)
    while len(groups) < n:
        groups.append([])
    return groups[:n]


def main(argv) -> int:
    path = argv[1]
    dry = "--dry-run" in argv
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    byid = {n["id"]: n for n in nodes}

    def dev(i):
        return byid[i]["device"]

    moved = 0
    for dc in sorted({n["device"]["datacenter"] for n in nodes
                      if n["device"].get("datacenter")}):
        rooms = sorted({n["device"].get("room") for n in nodes
                        if n["device"].get("datacenter") == dc
                        and (n["device"].get("room") or "").startswith("Server Hall")})
        for room in rooms:
            here = [n for n in nodes if n["device"].get("datacenter") == dc
                    and n["device"].get("room") == room]
            oobs = sorted((n for n in here if is_it_oob(n["device"])),
                          key=lambda n: n["device"]["name"])
            if not oobs:
                continue
            oob_ids = {n["id"] for n in oobs}

            managed = [n for n in here if n["device"]["device_type"] in IT_TYPES]
            if not managed:
                continue

            # Uplinks (to the OOB cores) are not free ports — reserve them.
            uplinks = collections.Counter()
            for e in edges:
                if e.get("layer") != "management":
                    continue
                for a, b in ((e["src"], e["dst"]), (e["dst"], e["src"])):
                    if a in oob_ids and dev(b)["device_type"] == "oob_switch":
                        uplinks[a] += 1

            by_rack = collections.defaultdict(list)
            for n in managed:
                d = n["device"]
                by_rack[(d["rack_row"], d["rack_num"])].append(n)
            racks = sorted(by_rack)
            counts = {r: len(by_rack[r]) for r in racks}
            groups = chunk_racks(racks, counts, len(oobs))

            # ── Rewire: drop every managed<->IT-OOB link in this hall, re-add ──
            managed_ids = {n["id"] for n in managed}
            before = len(edges)
            edges[:] = [e for e in edges if not (
                e.get("layer") == "management"
                and ((e["src"] in managed_ids and e["dst"] in oob_ids)
                     or (e["dst"] in managed_ids and e["src"] in oob_ids)))]
            dropped = before - len(edges)

            report = []
            over = []
            for oob, grp in zip(oobs, groups):
                n_dev = sum(counts[r] for r in grp)
                cap = oob["device"]["interface_count"]
                used = n_dev + uplinks[oob["id"]]
                if used > cap:
                    over.append((oob["device"]["name"], used, cap))
                for r in grp:
                    for n in by_rack[r]:
                        edges.append({"src": n["id"], "dst": oob["id"], "src_iface": 0,
                                      "dst_iface": 0, "broken": False,
                                      "layer": "management"})
                        moved += 1
                report.append(f"{oob['device']['name'].split('-')[0]}={used}/{cap}"
                              f"[{','.join(f'R{a}-{b:02d}' for a, b in grp) or '-'}]")

            print(f"{dc}/{room}: dropped {dropped}, re-homed {len(managed)} IT devices")
            for line in report:
                print(f"    {line}")
            if over:
                print(f"    !! still over capacity: {over}")

    if dry:
        print("\n--dry-run: nothing written")
        return 0
    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nRe-homed {moved} management links. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv))
