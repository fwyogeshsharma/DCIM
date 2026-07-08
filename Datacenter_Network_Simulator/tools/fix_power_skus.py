#!/usr/bin/env python3
"""Right-size the power-distribution / backup SKUs in an existing topology JSON.

Delegates to core.power_sizing.rightsize_nodes — the SAME load-aware selector the
topology generator uses at build time — so a shipped topology and a freshly
generated one agree. For each PDU/RPP/UPS/generator it computes the node's actual
downstream electrical load from the power-layer edges and picks the smallest real
SKU (keeping the node's vendor) that covers it at a realistic utilisation, then
clears rated_power_w so the value re-derives from the per-SKU catalog at load
time. Everything else (IDs, IPs, links, positions) is preserved. Idempotent.

Why this matters: with rated_power_w unset the live power model self-derived every
node's rating as load÷0.8 and froze it — so a node sat at ~80% regardless of its
(cosmetic) label. Now the label drives capacity and an undersized SKU on a growing
fleet reads a real OVERLOAD.

Usage:  python -m tools.fix_power_skus [path/to/topology.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from core.power_sizing import rightsize_nodes

DEFAULT = Path("topologies/dual_dc_enterprise.json")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    if not path.exists():
        print(f"topology not found: {path}", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))
    changes = rightsize_nodes(data.get("nodes", []), data.get("edges", []))
    for c in changes:
        print(f"  {c['name']:20} {str(c['from'])!r:32} -> {c['to']!r} "
              f"(load {c['load_kw']}kW / rated {c['rated_kw']}kW)")
    if changes:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\n{len(changes)} power node(s) re-SKU'd in {path}"
          if changes else "(no changes — SKUs already sized to load)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
