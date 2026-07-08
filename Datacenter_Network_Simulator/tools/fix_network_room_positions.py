"""Place the Network Room RPP/EV2 nodes next to the existing IT RPPs on the
topology canvas.

network_room_power.py / mount_ev2_network_room.py created the RPP-NET pair and
their EV2 meters at canvas position (0,0), so they pile on the origin in the
topology graph. The curated IT RPPs sit in a row (RPP-A, EV2-A, RPP-B, EV2-B at
a shared y). This tool lays the new Network Room panels out in that same row,
just to the right of the existing IT RPP cluster.

Idempotent: re-running just re-sets the same coordinates.

    python tools/fix_network_room_positions.py
"""
from __future__ import annotations

import json
import os

TOPO = "topologies/dual_dc_enterprise.json"
STEP = 80   # canvas px between adjacent panels (matches the curated RPP row)


def main() -> None:
    with open(TOPO, "r", encoding="utf-8") as f:
        doc = json.load(f)

    node_by_name = {n["device"]["name"]: n for n in doc["nodes"] if n.get("device")}
    dcs = sorted({n["device"].get("datacenter") for n in doc["nodes"]
                  if n.get("device", {}).get("name", "").startswith("RPP-NET-")})

    moved = 0
    for dc in dcs:
        b1 = node_by_name.get(f"RPP-IT-{dc}-B1")
        if not b1:
            print(f"  {dc}: no RPP-IT-{dc}-B1 anchor — skip.")
            continue
        bx, by = b1["position"]["x"], b1["position"]["y"]
        # Continue the RPP row to the right of B1 and its EV2 (B1 + 2 steps).
        layout = {
            f"RPP-NET-{dc}-A": bx + STEP * 2,
            f"EV2-{dc}-NET-A": bx + STEP * 3,
            f"RPP-NET-{dc}-B": bx + STEP * 4,
            f"EV2-{dc}-NET-B": bx + STEP * 5,
        }
        for name, x in layout.items():
            n = node_by_name.get(name)
            if n is None:
                print(f"  {dc}: {name} missing — skip.")
                continue
            n["position"] = {"x": x, "y": by}
            moved += 1
        print(f"  {dc}: placed Network Room panels at y={by}, x={bx + STEP*2}..{bx + STEP*5}")

    if not moved:
        print("Nothing to place.")
        return
    with open(TOPO, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    print(f"Done: repositioned {moved} node(s).")


if __name__ == "__main__":
    main()
