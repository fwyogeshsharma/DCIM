#!/usr/bin/env python3
"""Seed each server hall's FULL CRAH complement into the topology JSON.

Background
----------
The curated topology only hand-authored 4 CRAHs per hall. At runtime the fleet
lifecycle (`_ensure_all_hall_crahs`) topped every hall up to its N+1 complement
sized to the hall's ULTIMATE rack load. That meant the real CRAH count only
existed after the app booted -- the topology file was not the source of truth.

This one-shot tool bakes that full complement straight into the topology so the
JSON is authoritative (Option 1: all CRAHs seeded up front, all run VFD-modulated
on their hall's inlet temp; none are staged off). After running this the runtime
top-up is a no-op for the curated halls (len(existing) >= target).

It mirrors the sizing/placement logic of FleetLifecycle exactly:
  * target   = crah_count_for(racks_per_row * compute_rows * DESIGN_RACK_KW)  (N+1)
  * placement= evenly spread along the hall's back-wall CRAH row
  * wiring   = CHW supply -> CRAH -> CHW return (cooling), CRAH -> OOB (management)

Usage:
    python tools/seed_hall_crahs.py topologies/dual_dc_enterprise.json
    # then regenerate the derived DCIM export:
    python tools/export_dcim_floorplan.py \
        topologies/dual_dc_enterprise.json \
        topologies/dual_dc_enterprise_floorplan.json
"""
from __future__ import annotations

import copy
import json
import math
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import hall_geometry as geo  # noqa: E402

# ── Sizing constants — kept in lock-step with FleetLifecycle / cooling_model ──
DESIGN_RACK_KW = 12.0    # FleetLifecycle._DESIGN_RACK_KW
CRAH_COOL_KW   = 80.0    # cooling_model.CRAH_COOL_KW  (per-CRAH sensible capacity)
RPP_POLES      = 42      # FleetLifecycle._RPP_POLES


def crah_count_for(it_kw: float) -> int:
    """CRAHs for *it_kw* of IT heat: ceil(load / per-unit) + 1 spare (N+1)."""
    return max(1, math.ceil(max(0.0, it_kw) / CRAH_COOL_KW) + 1)


def hall_grid(ext: dict, has_local_spine: bool):
    """(racks_per_row, compute_rows, n_rows) for a hall, from its floor extent.
    Replicates FleetLifecycle._hall_grid so the seeded count matches runtime."""
    rows = ext.get("rows") or []
    w = ext.get("width_m")
    stored = ext.get("racks_per_row") or 0
    rpr = max(stored, geo.racks_for_width(w) if w else 0) or 1
    rpr = max(1, int(rpr))
    if not has_local_spine:                       # compute annex — fills from row 1
        n_rows = max(2, len(rows)); compute_rows = max(1, n_rows - 1)
    else:                                         # network hall — row 1 is spines
        n_rows = max(3, len(rows)); compute_rows = max(1, n_rows - 2)
    rpr = max(1, min(rpr, (RPP_POLES - 1) // compute_rows))
    return rpr, compute_rows, n_rows


def perimeter_positions(ext: dict, rpr: int, n_rows: int, target: int) -> list:
    """(floor_x, floor_y) for *target* CRAHs lined along the hall's BACK wall
    (behind the last IT row), evenly spread across the width — the curated
    Hall A layout. The front wall can't hold CRAHs (in a network hall Row 1 sits
    there, and a unit centered off the front wall pokes past it); the long side
    walls are blocked by full-width rack rows. The halls are wide enough (7.8–
    8.4 m) that all `target` units fit one back wall at ~1.1–1.2 m pitch."""
    width = float(ext.get("width_m") or (rpr * geo.RACK_PITCH + 2 * geo.rack_x(1)))
    by = round(geo.row_y(n_rows), 4)                           # back wall, from geometry
    return [(round(width * (j + 0.5) / target, 4), by) for j in range(target)]


def next_free_mgmt(seed_ip: str, used: set) -> str:
    """Next free IP at/above *seed_ip*, incrementing the last two octets (stays
    inside the DC's mgmt /22). Returns '' if seed is blank."""
    if not seed_ip:
        return ""
    a, b, c, d = (int(x) for x in seed_ip.split("."))
    for _ in range(4096):
        d += 1
        if d > 254:
            d = 1; c += 1
        ip = f"{a}.{b}.{c}.{d}"
        if ip not in used:
            return ip
    return ""


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]
    edges = topo["edges"]
    rooms = (topo.get("floorplan") or {}).get("rooms") or {}

    by_id = {n["id"]: n for n in nodes}
    name_to_id = {n["device"].get("name"): n["id"] for n in nodes}
    used_ids = set(by_id)
    used_mgmt = {(n["device"].get("mgmt_ip") or "") for n in nodes}
    used_names = set(name_to_id)

    # Which halls have a local spine (switch with '-SP' in its name)?
    spine_rooms = {
        (n["device"]["datacenter"], n["device"].get("room"))
        for n in nodes
        if n["device"]["device_type"] == "switch" and "-SP" in (n["device"].get("name") or "")
    }

    # Mechanical distribution per DC. CRAHs are bulk mechanical loads, so they hang
    # off a Motor Control Center — upstream of the UPS, downstream of a transfer
    # switch — not off an IT power panel. Resolved by ROOM so it survives renames.
    # Both MCCs are kept so seeded CRAHs alternate across the 2N mechanical split
    # instead of piling onto one side.
    mcc_mech: dict = {}
    for n in nodes:
        if (n["device"]["device_type"] == "mcc"
                and (n["device"].get("room") or "") == "Mechanical Room"):
            mcc_mech.setdefault(n["device"]["datacenter"], []).append(n)
    for v in mcc_mech.values():
        v.sort(key=lambda n: n["device"]["name"])
    mcc_mech = {dc: [n["id"] for n in v] for dc, v in mcc_mech.items()}

    # Highest CRAH index per DC, to continue the CRAH-<DC>-<n> naming sequence.
    max_idx: dict = {}
    for n in nodes:
        dev = n["device"]
        if dev["device_type"] == "crah":
            nm = dev.get("name") or ""
            try:
                dc, idx = nm.rsplit("-", 1)[0].split("-")[0], int(nm.rsplit("-", 1)[1])
            except Exception:
                continue
            dck = dev["datacenter"]
            max_idx[dck] = max(max_idx.get(dck, 0), idx)

    total_added = 0
    total_powered = 0
    for (dc, room), _ in sorted({(n["device"]["datacenter"], n["device"].get("room")): 1
                                 for n in nodes
                                 if n["device"]["device_type"] == "crah"
                                 and "Server Hall" in (n["device"].get("room") or "")}.items()):
        ext = rooms.get(f"{dc}/{room}")
        if not ext:
            print(f"  ! no floor extent for {dc}/{room} — skipped")
            continue
        existing = [n for n in nodes
                    if n["device"]["device_type"] == "crah"
                    and n["device"]["datacenter"] == dc
                    and n["device"].get("room") == room]
        rpr, comp_rows, n_rows = hall_grid(ext, (dc, room) in spine_rooms)
        ult_kw = rpr * comp_rows * DESIGN_RACK_KW
        target = crah_count_for(ult_kw)
        print(f"{dc}/{room}: rpr={rpr} rows={comp_rows} ult={ult_kw:.0f}kW "
              f"target={target} existing={len(existing)} -> add {max(0, target - len(existing))}")

        # Ensure every CRAH already in the hall draws power from a mechanical MCC.
        # Earlier seed runs added CHW + mgmt edges but no power edge, leaving
        # seeded CRAHs unpowered. Unpowered CRAHs alternate A/B across the two
        # MCCs so a lost transfer switch takes out roughly half a hall's air
        # handlers, not all of them. Idempotent — adds only the missing feed.
        powered = {e["dst"] for e in edges if e.get("layer") == "power"}
        mccs = mcc_mech.get(dc) or []
        for i, n in enumerate(sorted(existing, key=lambda n: n["device"]["name"])):
            if not mccs or n["id"] in powered:
                continue
            edges.append({"src": mccs[i % len(mccs)], "dst": n["id"], "src_iface": 0,
                          "dst_iface": 0, "broken": False, "layer": "power"})
            total_powered += 1

        # Line all CRAHs along the BACK wall, evenly spread. Repositions the
        # existing units too (so re-runs re-lay them onto the current geometry),
        # and runs BEFORE the at-target early-continue so a hall already at its
        # complement still gets re-laid.
        positions = perimeter_positions(ext, rpr, n_rows, target)
        for i, n in enumerate(existing):
            n["device"]["floor_x"], n["device"]["floor_y"] = positions[i]
            n["device"]["rack_num"] = i + 1

        if len(existing) >= target:
            continue

        tmpl = existing[0]
        # Per-hall CHW headers + OOB switch, resolved from an existing CRAH's edges.
        chws = chwr = oob = None
        for e in edges:
            if e["dst"] == tmpl["id"] and e.get("layer") == "cooling":
                chws = e["src"]
            elif e["src"] == tmpl["id"] and e.get("layer") == "cooling":
                chwr = e["dst"]
            elif e["src"] == tmpl["id"] and e.get("layer") == "management":
                oob = e["dst"]

        for i in range(len(existing), target):
            new = copy.deepcopy(tmpl)
            nid = uuid.uuid4().hex[:8]
            while nid in used_ids:
                nid = uuid.uuid4().hex[:8]
            used_ids.add(nid)
            max_idx[dc] = max_idx.get(dc, 0) + 1
            nm = f"CRAH-{dc}-{max_idx[dc]}"
            while nm in used_names:
                max_idx[dc] += 1
                nm = f"CRAH-{dc}-{max_idx[dc]}"
            used_names.add(nm)
            mgmt = next_free_mgmt(tmpl["device"].get("mgmt_ip") or "", used_mgmt)
            used_mgmt.add(mgmt)

            new["id"] = nid
            dev = new["device"]
            dev["id"] = nid
            dev["name"] = nm
            dev["mgmt_ip"] = mgmt
            dev["snmp_community"] = mgmt          # snmpsim routes by community == IP
            dev["floor_x"], dev["floor_y"] = positions[i]
            dev["rack_num"] = i + 1
            # Fresh L2 identity so the new unit is not a MAC/interface clone.
            for iface in dev.get("interfaces", []):
                iface["mac_address"] = ":".join(f"{b:02x}" for b in uuid.uuid4().bytes[:6])
                iface["connected_to_device"] = None
                iface["connected_to_iface"] = None
            # Cosmetic canvas position: nudge off the template so nodes don't stack.
            pos = new.get("position") or {"x": 0, "y": 0}
            new["position"] = {"x": pos.get("x", 0) + 40 * (i + 1), "y": pos.get("y", 0)}

            nodes.append(new)
            by_id[nid] = new
            if chws:
                edges.append({"src": chws, "dst": nid, "src_iface": 0, "dst_iface": 0,
                              "broken": False, "layer": "cooling"})
            if chwr:
                edges.append({"src": nid, "dst": chwr, "src_iface": 0, "dst_iface": 0,
                              "broken": False, "layer": "cooling"})
            if oob:
                edges.append({"src": nid, "dst": oob, "src_iface": 0, "dst_iface": 0,
                              "broken": False, "layer": "management"})
            if pwr_src:
                edges.append({"src": pwr_src, "dst": nid, "src_iface": 0,
                              "dst_iface": 0, "broken": False, "layer": "power"})
            total_added += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"\nSeeded {total_added} CRAH(s); powered {total_powered} pre-existing "
          f"unpowered CRAH. Wrote {p}")
    print("Now regenerate the DCIM export:")
    print(f"  python tools/export_dcim_floorplan.py {path} "
          f"{path.replace('.json', '_floorplan.json')}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
