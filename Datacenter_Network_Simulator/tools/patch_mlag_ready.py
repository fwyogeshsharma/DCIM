#!/usr/bin/env python3
"""Make an existing curated topology dual-homing (MLAG) ready — in place.

Forward-compat only: nothing here turns dual-homing ON. It reserves the geometry
and marks the leaves so a SECOND top-of-rack leaf can be added later (MLAG/vPC)
without moving any server. See core/rack_capacity.py for the contract.

What it changes (idempotent):
  * Leaf switches (LF*): mlag_ready=True, mlag_peer_unit=U41.
  * Rack PDUs: rack_unit -> 0 (0U vertical side-rail mount; frees RU, keeps U41
    clear for the future peer leaf).
  * Compute racks: ensure U41 is empty (relocate any stray occupant down to the
    next free server U <= U40).

What it deliberately does NOT touch:
  * interface_groups / interfaces of the curated leaves — those carry live
    counters, MACs and connection state. Per-rack capacity is derived from the
    leaf MODEL (leaf_port_roles), so the 48/6 downlink/uplink split is already
    known without rewriting interface rows. New from-scratch builds
    (generate_topology.py) DO carry the explicit 25G/100G split.

Usage:
    python tools/patch_mlag_ready.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.rack_capacity import (  # noqa: E402
    TOR_A_UNIT, TOR_B_UNIT, PDU_UNIT, FIRST_SERVER_UNIT, LAST_SERVER_UNIT,
)


def _rack_key(dev: dict) -> tuple:
    return (dev.get("datacenter"), dev.get("rack_row"), dev.get("rack_num"))


def patch(topology: dict) -> dict:
    nodes = topology["nodes"]
    devs = [n["device"] for n in nodes]

    leaves = [d for d in devs
              if d.get("device_type") == "switch" and "-LF" in (d.get("name") or "")]
    pdus = [d for d in devs if d.get("device_type") == "pdu"]

    # 1. Mark leaves MLAG-ready, reserving the U41 peer slot.
    for lf in leaves:
        lf["mlag_ready"] = True
        lf["mlag_peer_unit"] = TOR_B_UNIT
        # Leaf itself sits at ToR-A (U42); normalize if a curated layout drifted.
        if lf.get("rack_unit") not in (TOR_A_UNIT,):
            lf["rack_unit"] = TOR_A_UNIT

    # 2. Rack PDUs -> 0U vertical.
    for p in pdus:
        p["rack_unit"] = PDU_UNIT

    # 3. Vacate U41 in every compute (leaf) rack — keep it reserved/empty.
    leaf_rack_keys = {_rack_key(lf) for lf in leaves}
    moved = 0
    for key in leaf_rack_keys:
        members = [d for d in devs if _rack_key(d) == key]
        used = {d.get("rack_unit") for d in members
                if isinstance(d.get("rack_unit"), int) and d.get("rack_unit") > 0}
        for d in members:
            if d.get("rack_unit") == TOR_B_UNIT and "-LF" not in (d.get("name") or ""):
                u = FIRST_SERVER_UNIT
                while u in used and u < LAST_SERVER_UNIT:
                    u += 1
                d["rack_unit"] = u
                used.add(u)
                moved += 1

    return {"leaves": len(leaves), "pdus": len(pdus), "u41_vacated": moved}


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = argv[1]
    with open(path, "r", encoding="utf-8") as f:
        topology = json.load(f)
    stats = patch(topology)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topology, f, indent=2)
        f.write("\n")
    print(f"Patched {path}\n"
          f"  leaves marked MLAG-ready: {stats['leaves']} (peer slot U{TOR_B_UNIT})\n"
          f"  rack PDUs set to 0U:      {stats['pdus']}\n"
          f"  U41 occupants relocated:  {stats['u41_vacated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
