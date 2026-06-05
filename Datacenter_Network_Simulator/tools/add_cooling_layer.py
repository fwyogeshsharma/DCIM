"""Add a 'cooling' layer to the topology: directed edges modelling the
chilled-water (CHW) and condenser-water (CW) loops, so the graph shows how
coolant flows through the plant and out to the server-hall CRAHs.

Representative loops per DC (flow direction = edge direction):

  CHW loop (cools the hall, crosses buildings to the server hall):
    CHILLER ─► CHW pump ─► VLV-CHW ─► flow/supply sensors ═╗ (cross-building)
                                                           ╠═► CRAH ×4
    CHILLER ◄─ return sensor ◄════════════════════════════╝ (cross-building)

  CW loop (rejects heat, stays in the chiller plant):
    CHILLER ─► CW pump ─► VLV-CW ─► cooling tower ─► CHILLER

Idempotent: clears existing cooling edges, then rebuilds. Pure topology — no
power/PUE impact. The cross-building segments are CHILLER-PLANT sensors ↔
SERVER-HALL CRAHs (the supply/return pipes between the two buildings).
"""
import json
from pathlib import Path

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"

def loops(dc):
    """Return list of (src_name, dst_name) flow edges for one DC."""
    e = []
    # ── CHW loop ───────────────────────────────────────────────────
    # chillers -> CHW pumps
    e += [(f"CHILLER-{dc}-1", f"CHWP-{dc}-1"),
          (f"CHILLER-{dc}-2", f"CHWP-{dc}-2"),
          (f"CHILLER-{dc}-2", f"CHWP-{dc}-3")]
    # pumps -> supply control valve
    e += [(f"CHWP-{dc}-1", f"VLV-{dc}-CHW"),
          (f"CHWP-{dc}-2", f"VLV-{dc}-CHW"),
          (f"CHWP-{dc}-3", f"VLV-{dc}-CHW")]
    # valve -> flow meter -> supply-temp sensor (still in chiller plant)
    e += [(f"VLV-{dc}-CHW", f"SENS-{dc}-FLOW"),
          (f"SENS-{dc}-FLOW", f"SENS-{dc}-CHWS")]
    # supply-temp sensor -> CRAHs  (CROSS-BUILDING: chiller plant -> server hall)
    e += [(f"SENS-{dc}-CHWS", f"CRAH-{dc}-{k}") for k in range(1, 5)]
    # CRAHs -> return-temp sensor  (CROSS-BUILDING: server hall -> chiller plant)
    e += [(f"CRAH-{dc}-{k}", f"SENS-{dc}-CHWR") for k in range(1, 5)]
    # return sensor -> chillers
    e += [(f"SENS-{dc}-CHWR", f"CHILLER-{dc}-1"),
          (f"SENS-{dc}-CHWR", f"CHILLER-{dc}-2")]
    # ── CW loop (condenser, stays in the chiller plant) ────────────
    e += [(f"CHILLER-{dc}-1", f"CWP-{dc}-1"),
          (f"CHILLER-{dc}-2", f"CWP-{dc}-2"),
          (f"CWP-{dc}-1", f"VLV-{dc}-CW"),
          (f"CWP-{dc}-2", f"VLV-{dc}-CW"),
          (f"VLV-{dc}-CW", f"CT-{dc}-1"),
          (f"VLV-{dc}-CW", f"CT-{dc}-2"),
          (f"CT-{dc}-1", f"CHILLER-{dc}-1"),
          (f"CT-{dc}-2", f"CHILLER-{dc}-2")]
    return e

def main():
    d = json.loads(TOPO.read_text())
    nodes, edges = d["nodes"], d["edges"]
    byname = {n["device"]["name"]: n["id"] for n in nodes}

    # idempotent: drop existing cooling edges
    before = len(edges)
    edges[:] = [e for e in edges if e.get("layer") != "cooling"]

    added = missing = 0
    for dc in ("DC1", "DC2"):
        for a, b in loops(dc):
            ia, ib = byname.get(a), byname.get(b)
            if ia is None or ib is None:
                missing += 1
                print(f"  ! missing node: {a if ia is None else b}")
                continue
            edges.append({"src": ia, "dst": ib, "src_iface": 0,
                          "dst_iface": 0, "layer": "cooling"})
            added += 1

    TOPO.write_text(json.dumps(d, indent=2))
    print(f"cooling edges: removed {before - (len(edges) - added)} old, added {added}"
          + (f", {missing} missing" if missing else ""))

if __name__ == "__main__":
    main()
