"""Add a 'cooling' layer to the topology: directed edges modelling the
chilled-water (CHW), condenser-water (CW) and direct-to-chip (TCS) loops, so the
graph shows how coolant flows through the plant and out to the loads.

Loops per DC (flow direction = edge direction), all discovered dynamically by
name prefix so the layer tracks however many chillers/pumps/CDUs exist:

  CHW loop (cools the hall + the chips, crosses to the server hall):
    CHILLER ─► CHW pump ─► VLV-CHW ─► FLOW ─► CHWS ═╗ (cross-building)
                                                    ╠═► CRAH ×N
                                                    ╠═► CDU  ×M   (direct-to-chip)
    CHILLER ◄─ CHWR ◄═══════════════════════════════╝ (cross-building)

  CW loop (rejects heat, stays in the chiller plant):
    CHILLER ─► CW pump ─► VLV-CW ─► cooling tower ─► CHILLER

  TCS loop (secondary cold-plate loop, isolated by the CDU):
    CDU ─► server cold plate ─► CDU      (representative sample of servers)

Idempotent: clears existing cooling edges, then rebuilds. Pure topology — no
power/PUE impact. The cross-building segments are CHILLER-PLANT sensors ↔
SERVER-HALL CRAH/CDU (the supply/return pipes between the two buildings).
"""
import json
import re
from pathlib import Path

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"

# Representative number of server cold plates wired per CDU (the TCS loop is
# illustrative — not every server is individually drawn, to keep the graph
# readable, mirroring how a handful of CRAH represent hall air cooling).
TCS_SERVERS_PER_CDU = 6


def _sorted_names(nodes, pattern):
    rx = re.compile(pattern)
    return sorted(n["device"]["name"] for n in nodes if rx.fullmatch(n["device"]["name"]))


def loops(dc, nodes):
    """Return list of (src_name, dst_name) flow edges for one DC, discovered
    from whatever plant/CDU/server nodes are present."""
    e = []
    chillers = _sorted_names(nodes, rf"CHILLER-{dc}-\d+")
    chwp     = _sorted_names(nodes, rf"CHWP-{dc}-\d+")
    cwp      = _sorted_names(nodes, rf"CWP-{dc}-\d+")
    towers   = _sorted_names(nodes, rf"CT-{dc}-\d+")
    crahs    = _sorted_names(nodes, rf"CRAH-{dc}-\d+")
    cdus     = _sorted_names(nodes, rf"CDU-{dc}-\d+")
    vchw, vcw = f"VLV-{dc}-CHW", f"VLV-{dc}-CW"
    s_flow, s_chws, s_chwr = f"SENS-{dc}-FLOW", f"SENS-{dc}-CHWS", f"SENS-{dc}-CHWR"

    # ── CHW loop ───────────────────────────────────────────────────
    # chillers -> CHW pumps (distribute chillers across the available pumps)
    for i, ch in enumerate(chillers):
        if chwp:
            e.append((ch, chwp[i % len(chwp)]))
    # pumps -> supply control valve
    e += [(p, vchw) for p in chwp]
    # valve -> flow meter -> supply-temp sensor (still in chiller plant)
    e += [(vchw, s_flow), (s_flow, s_chws)]
    # supply-temp sensor -> CRAH and CDU (CROSS-BUILDING: plant -> server hall)
    e += [(s_chws, c) for c in crahs]
    e += [(s_chws, c) for c in cdus]
    # CRAH and CDU -> return-temp sensor (CROSS-BUILDING: server hall -> plant)
    e += [(c, s_chwr) for c in crahs]
    e += [(c, s_chwr) for c in cdus]
    # return sensor -> chillers
    e += [(s_chwr, ch) for ch in chillers]

    # ── CW loop (condenser, stays in the chiller plant) ────────────
    for i, ch in enumerate(chillers):
        if cwp:
            e.append((ch, cwp[i % len(cwp)]))
    e += [(p, vcw) for p in cwp]
    e += [(vcw, t) for t in towers]
    for i, t in enumerate(towers):
        if chillers:
            e.append((t, chillers[i % len(chillers)]))

    # ── TCS loop (direct-to-chip cold plates) ──────────────────────
    # Each CDU feeds a representative sample of the DC's servers and returns.
    dc_servers = sorted(
        n["device"]["name"] for n in nodes
        if n["device"]["device_type"] == "server" and n["device"].get("datacenter") == dc
    )
    if cdus and dc_servers:
        # round-robin assign servers to CDUs, capped per CDU
        per_cdu = {c: [] for c in cdus}
        ci = 0
        for srv in dc_servers:
            cdu = cdus[ci % len(cdus)]
            if len(per_cdu[cdu]) < TCS_SERVERS_PER_CDU:
                per_cdu[cdu].append(srv)
            ci += 1
            if all(len(v) >= TCS_SERVERS_PER_CDU for v in per_cdu.values()):
                break
        for cdu, srvs in per_cdu.items():
            for srv in srvs:
                e.append((cdu, srv))     # CDU supply -> cold plate
                e.append((srv, cdu))     # cold plate -> CDU return
    return e


def main():
    d = json.loads(TOPO.read_text())
    nodes, edges = d["nodes"], d["edges"]
    byname = {n["device"]["name"]: n["id"] for n in nodes}

    # idempotent: drop existing cooling edges
    before = len(edges)
    edges[:] = [e for e in edges if e.get("layer") != "cooling"]
    base = len(edges)

    added = missing = 0
    for dc in ("DC1", "DC2"):
        for a, b in loops(dc, nodes):
            ia, ib = byname.get(a), byname.get(b)
            if ia is None or ib is None:
                missing += 1
                print(f"  ! missing node: {a if ia is None else b}")
                continue
            edges.append({"src": ia, "dst": ib, "src_iface": 0,
                          "dst_iface": 0, "layer": "cooling"})
            added += 1

    TOPO.write_text(json.dumps(d, indent=2))
    print(f"cooling edges: removed {before - base} old, added {added}"
          + (f", {missing} missing" if missing else ""))


if __name__ == "__main__":
    main()
