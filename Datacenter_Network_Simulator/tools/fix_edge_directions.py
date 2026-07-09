#!/usr/bin/env python3
"""Orient fabric production edges top-down (upper tier = edge source).

The topology view anchors an edge to a node's TOP handle when the node is the
edge's TARGET and its BOTTOM handle when it's the SOURCE. The curated fabric wires
every edge from the upper tier down (core→spine, spine→leaf, leaf→server), so a
spine's core edges land on top and its leaf edges on the bottom. promote_hall_b_pod
wired Hall B as leaf→spine, so the spine was the TARGET of its leaf edges too and
every edge bunched on the top handle.

This flips any fabric production edge whose source is the LOWER tier, so
core→spine→leaf→server holds everywhere. src_iface/dst_iface are swapped with the
endpoints (connectivity is undirected; only the handle anchoring changes).
Idempotent. Upper-stack edges (router/fw/lb/core) are left alone.

Usage:
    python tools/fix_edge_directions.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def rank(dv) -> int:
    """Fabric tier rank (upper = smaller). None for non-fabric devices."""
    t = dv["device_type"]
    if t == "server":
        return 3
    if t == "switch":
        code = (dv.get("name") or "").split("-", 1)[0].upper()
        if code.startswith("COR"):
            return 0
        if code.startswith("SP"):
            return 1
        if code.startswith("LF"):
            return 2
    return None


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    N = {n["id"]: n["device"] for n in topo["nodes"]}

    def is_oob_core(dv):
        return dv.get("device_type") == "oob_switch" \
            and (dv.get("name") or "").split("-", 1)[0].upper().startswith("OOBC")

    def is_oob_access(dv):
        return dv.get("device_type") == "oob_switch" and not is_oob_core(dv)

    flipped = mgmt = 0
    for e in topo["edges"]:
        lay = e.get("layer")
        if lay == "production":
            rs, rd = rank(N.get(e["src"], {})), rank(N.get(e["dst"], {}))
            if rs is None or rd is None:
                continue                   # not a fabric edge — leave as is
            if rs > rd:                    # source is the LOWER tier → flip
                e["src"], e["dst"] = e["dst"], e["src"]
                e["src_iface"], e["dst_iface"] = e.get("dst_iface", 0), e.get("src_iface", 0)
                flipped += 1
        elif lay == "management":
            # Mgmt is wired bottom-up (device→oob→oob_core). Only the access-OOB ↔
            # OOB-CORE link was mixed; orient it access→core.
            s, d = N.get(e["src"], {}), N.get(e["dst"], {})
            if is_oob_core(s) and is_oob_access(d):
                e["src"], e["dst"] = e["dst"], e["src"]
                e["src_iface"], e["dst_iface"] = e.get("dst_iface", 0), e.get("src_iface", 0)
                mgmt += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Reoriented {flipped} fabric edges + {mgmt} mgmt oob->core edges (upper tier = source).")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
