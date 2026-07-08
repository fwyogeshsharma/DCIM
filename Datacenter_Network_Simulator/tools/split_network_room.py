"""Split the network core out of the server hall into a dedicated Network Room.

Real datacenters keep the WAN/edge routers and the shared network-services
appliances (firewalls, load balancers) in a dedicated network core room — an
MDF / meet-me room near the carrier entrance — NOT inside a compute white-space
hall. The curated topology originally placed the edge routers (ER), firewalls
(FW) and load balancers (LB) in the front row of "Server Hall A". This tool
relocates them into a new per-DC "Network Room".

What it does NOT move (correct as-is):
  * leaf/ToR switches   — live in the server racks (top-of-rack)
  * spine switches      — per-hall pod fabric, stay in the hall
  * OOB (mgmt) switches — per-pod out-of-band management, stay in the hall

Idempotent: re-running is a no-op once the devices are already relocated.

    python tools/split_network_room.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import hall_geometry as geo  # noqa: E402

TOPO = "topologies/dual_dc_enterprise.json"
FLOOR = "topologies/dual_dc_enterprise_floorplan.json"

# Device types that make up the network core / services tier.
CORE_TYPES = {"router", "firewall", "load_balancer"}
SRC_ROOM = "Server Hall A"
NEW_ROOM = "Network Room"


def _network_room_extent(dc: str, racks_per_row: int) -> dict:
    """A small MDF-style room sized to hold the core racks in a single row."""
    return {
        "datacenter": dc,
        "room": NEW_ROOM,
        "class": "white_space",       # raised-floor racks (not a facility slab)
        "containment": "none",        # no hot/cold aisle — a network room
        "width_m": round(racks_per_row * geo.RACK_PITCH + 2 * geo._X0, 4),
        "depth_m": round(geo.row_y(1) + geo.RACK_D / 2 + 0.6, 4),
        "rows": [1],
        "racks_per_row": racks_per_row,
        "aisles": [],
    }


def main() -> None:
    with open(TOPO, "r", encoding="utf-8") as f:
        doc = json.load(f)

    # Which DCs have core gear in Server Hall A, and how many rack columns it uses.
    dc_racks: dict[str, set] = {}
    moved = 0
    for node in doc.get("nodes", []):
        dev = node.get("device") or {}
        if dev.get("device_type") in CORE_TYPES and dev.get("room") == SRC_ROOM:
            dc = dev.get("datacenter") or ""
            dev["room"] = NEW_ROOM
            # Placement is already a clean 3-rack front row (routers/FW/LB at
            # rack_num 1/2/3, x 0.3/0.9/1.5, units 42/40) — keep it, it maps 1:1
            # into the new room's own local grid.
            dc_racks.setdefault(dc, set()).add(dev.get("rack_num") or 1)
            moved += 1

    if not moved:
        print("No core gear in", SRC_ROOM, "— already split (no-op).")
        return

    # Add / refresh the Network Room extent for every DC we moved gear into.
    rooms = doc.setdefault("floorplan", {}).setdefault("rooms", {})
    for dc, racks in sorted(dc_racks.items()):
        rooms[f"{dc}/{NEW_ROOM}"] = _network_room_extent(dc, max(racks))
        print(f"  {dc}: moved core gear -> {NEW_ROOM} ({max(racks)} rack columns)")

    with open(TOPO, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    # Keep the standalone floor-plan file (tooling source) in sync if present.
    if os.path.exists(FLOOR):
        with open(FLOOR, "r", encoding="utf-8") as f:
            fdoc = json.load(f)
        frooms = (fdoc.get("floorplan") or fdoc).setdefault("rooms", {}) \
            if isinstance(fdoc.get("floorplan"), dict) else fdoc.setdefault("rooms", {})
        for dc, racks in sorted(dc_racks.items()):
            frooms[f"{dc}/{NEW_ROOM}"] = _network_room_extent(dc, max(racks))
        with open(FLOOR, "w", encoding="utf-8") as f:
            json.dump(fdoc, f, indent=2)
        print("  synced", FLOOR)

    print(f"Done: relocated {moved} device(s) into per-DC {NEW_ROOM}.")


if __name__ == "__main__":
    main()
