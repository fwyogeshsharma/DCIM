#!/usr/bin/env python3
"""Populate a server hall's RESERVED compute row — in place, footprint-frozen.

The curated halls were commissioned with spare, already-laid-out rows + aisles
inside their existing floor extent (real DCs build the room + cooling, then fill
rows over time). This tool fills one such reserved row by faithfully cloning the
hall's existing compute row:

  * one leaf ToR per rack (uplinked to ALL spines, MLAG-ready), 0U dual rack PDUs,
    a stack of regular compute servers (dual-fed A/B), per-rack CDU + leak/rack
    sensors, and the row's two end-of-row RPP power racks + EV2 energy monitors.
  * intra-row wiring (leaf->server, RPP->PDU->server, CDU->server) is recreated
    between the freshly-cloned devices; uplinks to SHARED infrastructure (spines,
    UPS A/B, OOB switches, chilled-water supply sensor) attach the new row to the
    same fabric/power/cooling/management as the existing row, on fresh ports.

Deliberately NOT cloned as-is:
  * Special one-off servers (DHCP/DNS/NTP/MON/STOR) are infra, not per-row
    compute — their slots are filled with regular compute servers instead.
  * Provider->consumer edges leaving the row (RPP feeding another room's PDUs,
    a CDU loop into another hall) are dropped: the new row only attaches UP to
    shared parents, it does not inherit the template's downstream dependents.

The room's physical extent (width_m x depth_m) and the floorplan 'rooms' block
are untouched — the reserved row already sits within the existing aisle grid, so
capacity rises with ZERO footprint change. Re-run export_dcim_floorplan.py after
this to refresh the asset/floor-plan file.

Usage:
    python tools/add_compute_row.py topologies/dual_dc_enterprise.json
    python tools/add_compute_row.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import ipaddress
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.rack_capacity import TOR_A_UNIT, TOR_B_UNIT  # noqa: E402

# ── What to fill, per hall ────────────────────────────────────────────────────
# Each target fills ONE reserved row by cloning the hall's existing compute row.
# new_floor_y / aisles place the row in the next free hot-aisle-contained slot
# within the EXISTING extent (verified against the floorplan 'rooms' aisles).
TARGETS = [
    # DC1 Hall A reserved row: CRAH sits at y=9.0 (geometric row 4), leaving the
    # hot-aisle-contained slot at y=6.6 free inside the existing 8.4x9.6 extent.
    # mode="new_row": clone the compute row into the reserved row at new_floor_y.
    dict(mode="new_row", suffix="R3",
         dc="DC1", floor="1", room="Server Hall A", src_row=2, new_row=3,
         new_floor_y=6.6, hot_aisle="HA1", cold_aisle="CA2", rack_facing="S"),
    # DC2 Hall A has NO free row (CRAH occupies y=6.6 in a 7.2m-deep room) but its
    # compute row uses only 6 of 13 reserved rack slots. mode="widen": clone the
    # compute row again, appended in the SAME row within the reserved width (rack
    # numbers shifted past the existing racks, floor_y/aisles unchanged). The
    # clone brings its own RPP pair so the added ~45 kW has matching power.
    dict(mode="widen", suffix="W",
         dc="DC2", floor="1", room="Server Hall A", src_row=2),
]

# Special (one-off infra) server name fragments — cloned as REGULAR compute.
_SPECIAL_FRAGS = ("-DHCP", "-DNS", "-NTP", "-MON", "-STOR")


def _is_special_server(dev: dict) -> bool:
    return dev.get("device_type") == "server" and any(
        f in (dev.get("name") or "") for f in _SPECIAL_FRAGS)


class Allocator:
    """Hands out non-colliding production + management IPs and unique names."""

    def __init__(self, devs: list[dict]):
        self.used_ip = {d.get("ip_address") for d in devs if d.get("ip_address")}
        self.used_mgmt = {d.get("mgmt_ip") for d in devs if d.get("mgmt_ip")}
        self.used_names = {d.get("name") for d in devs if d.get("name")}
        # Next compute-server ordinal per DC (regular DC<n>-SRV### only).
        self.srv_seq: dict[str, int] = {}
        for d in devs:
            nm = d.get("name") or ""
            if d.get("device_type") == "server" and "-SRV" in nm:
                dc, _, num = nm.partition("-SRV")
                if num.isdigit():
                    self.srv_seq[dc] = max(self.srv_seq.get(dc, 0), int(num))

    def next_in_subnet(self, ip: str, used: set) -> str:
        """Next free address in ip's /24, spilling into adjacent /24s if full."""
        net = ipaddress.ip_network(ip + "/24", strict=False)
        base = int(net.network_address)
        for block in range(0, 64):                      # try this /24 then onward
            for off in range(1, 255):
                cand = str(ipaddress.ip_address(base + block * 256 + off))
                if cand not in used:
                    used.add(cand)
                    return cand
        raise RuntimeError(f"no free IP near {ip}")

    def prod_ip(self, ip: str) -> str:
        return self.next_in_subnet(ip, self.used_ip)

    def mgmt_ip(self, ip: str) -> str:
        return self.next_in_subnet(ip, self.used_mgmt)

    def srv_name(self, dc: str) -> str:
        self.srv_seq[dc] = self.srv_seq.get(dc, 0) + 1
        nm = f"{dc}-SRV{self.srv_seq[dc]:03d}"
        while nm in self.used_names:
            self.srv_seq[dc] += 1
            nm = f"{dc}-SRV{self.srv_seq[dc]:03d}"
        self.used_names.add(nm)
        return nm

    def uniq_name(self, base: str) -> str:
        if base not in self.used_names:
            self.used_names.add(base)
            return base
        i = 2
        while f"{base}-{i}" in self.used_names:
            i += 1
        nm = f"{base}-{i}"
        self.used_names.add(nm)
        return nm


def _short_id(seed: str, taken: set) -> str:
    """8-hex stable-ish id derived from seed, guaranteed unique."""
    import hashlib
    h = hashlib.sha1(seed.encode()).hexdigest()
    for i in range(0, len(h) - 8):
        cand = h[i:i + 8]
        if cand not in taken:
            taken.add(cand)
            return cand
    raise RuntimeError("id space exhausted")


def add_row(topo: dict, t: dict, alloc: Allocator, ids: set) -> dict:
    nodes = topo["nodes"]
    edges = topo["edges"]
    by_id = {n["device"]["id"]: n for n in nodes}
    devs = [n["device"] for n in nodes]

    # Row device set (the populated compute row we clone).
    def in_row(d):
        return (d.get("datacenter") == t["dc"] and str(d.get("floor")) == t["floor"]
                and d.get("room") == t["room"] and d.get("rack_row") == t["src_row"])
    row_nodes = [n for n in nodes if in_row(n["device"])]
    row_ids = {n["device"]["id"] for n in row_nodes}
    if not row_nodes:
        raise RuntimeError(f"no source row for {t}")

    # Used iface index per device (so new edges to SHARED gear get fresh ports).
    used_iface: dict[str, set] = {}
    for e in edges:
        used_iface.setdefault(e["src"], set()).add(e.get("src_iface", 0))
        used_iface.setdefault(e["dst"], set()).add(e.get("dst_iface", 0))

    def free_iface(dev_id: str) -> int:
        u = used_iface.setdefault(dev_id, set())
        i = 0
        while i in u:
            i += 1
        u.add(i)
        return i

    # ── placement geometry (mode-specific) ────────────────────────────────────
    mode = t.get("mode", "new_row")
    suffix = t.get("suffix", "R")
    row_devs = [n["device"] for n in row_nodes]
    if mode == "widen":
        # Append in the SAME row, shifting rack numbers past the existing racks;
        # floor_x follows the rack pitch, floor_y/aisles stay as the row's.
        offset = max((d.get("rack_num") or 0) for d in row_devs)
        rack_pitch = topo.get("floorplan", {}).get("rack_pitch", 0.6)
        xs = [d.get("floor_x") for d in row_devs if d.get("floor_x") is not None]
        origin_x = min(xs) if xs else 0.3

    # ── clone devices ─────────────────────────────────────────────────────────
    idmap: dict[str, str] = {}
    new_nodes: list[dict] = []
    for n in row_nodes:
        d = n["device"]
        nd = deepcopy(d)
        new_id = _short_id(d["id"] + f"|{suffix}", ids)
        idmap[d["id"]] = new_id
        nd["id"] = new_id
        nd["sys_location_override"] = ""        # let it recompute from new fields
        if mode == "widen":
            nd["rack_num"] = (d.get("rack_num") or 0) + offset
            nd["floor_x"] = round(origin_x + rack_pitch * (nd["rack_num"] - 1), 4)
            # rack_row / floor_y / aisles / facing inherited from template as-is.
        else:                                    # new_row
            nd["rack_row"] = t["new_row"]
            nd["floor_y"] = t["new_floor_y"]
            nd["hot_aisle"] = t["hot_aisle"]
            nd["cold_aisle"] = t["cold_aisle"]
            nd["rack_facing"] = t["rack_facing"]
        # Name.
        dt = d.get("device_type")
        if dt == "server":
            nd["name"] = alloc.srv_name(t["dc"])
        else:
            nd["name"] = alloc.uniq_name(f"{d.get('name')}-{suffix}")
        # IPs.
        if d.get("ip_address"):
            nd["ip_address"] = alloc.prod_ip(d["ip_address"])
            if nd.get("snmp_community") == d.get("ip_address"):
                nd["snmp_community"] = nd["ip_address"]
        if d.get("mgmt_ip"):
            nd["mgmt_ip"] = alloc.mgmt_ip(d["mgmt_ip"])
        # Clear stale per-interface connection bookkeeping (rebuilt on load).
        for iface in nd.get("interfaces") or []:
            iface["connected_to_device"] = None
            iface["connected_to_iface"] = None
        # Leaf stays MLAG-ready in the reserved peer slot.
        if dt == "switch":
            nd["mlag_ready"] = True
            nd["mlag_peer_unit"] = TOR_B_UNIT
            nd["rack_unit"] = TOR_A_UNIT
        pos = deepcopy(n.get("position") or {"x": 0, "y": 0})
        pos["y"] = pos.get("y", 0) + 1500       # shove off existing canvas rows
        new_nodes.append({"id": new_id, "position": pos, "device": nd})

    # Remap power-feed id references to the cloned PDUs.
    for nn in new_nodes:
        d = nn["device"]
        for k in ("power_source_a", "power_source_b", "power_source"):
            ref = d.get(k)
            if ref in idmap:
                d[k] = idmap[ref]
            elif ref:
                d[k] = ""                       # fed by a non-cloned source -> drop

    # ── clone edges ───────────────────────────────────────────────────────────
    SHARED = {
        "production": lambda ext: ext.get("device_type") == "switch"
        and "-SP" in (ext.get("name") or ""),                       # spine uplink
        "power":      lambda ext: ext.get("device_type") == "ups",  # UPS -> RPP
        "management": lambda ext: ext.get("device_type") == "oob_switch",
        "cooling":    lambda ext: ext.get("device_type") == "sensor"
        and "CHW" in (ext.get("name") or ""),                       # CHW supply
    }
    new_edges: list[dict] = []
    for e in edges:
        s, dst, lay = e["src"], e["dst"], e.get("layer", "production")
        s_in, d_in = s in row_ids, dst in row_ids
        if not (s_in or d_in):
            continue
        if s_in and d_in:                       # intra-row: remap both, keep ifaces
            new_edges.append({**e, "src": idmap[s], "dst": idmap[dst]})
            continue
        # one endpoint external: only attach UP to shared parents.
        ext_id = dst if s_in else s
        ext = by_id.get(ext_id, {}).get("device", {})
        if not SHARED.get(lay, lambda _x: False)(ext):
            continue                            # provider->other consumer: drop
        in_new = idmap[s] if s_in else idmap[dst]
        ext_iface = free_iface(ext_id)          # fresh port on the shared device
        if s_in:                                # in -> ext  (e.g. leaf -> oob)
            new_edges.append({"src": in_new, "dst": ext_id, "layer": lay,
                              "src_iface": e.get("src_iface", 0), "dst_iface": ext_iface})
        else:                                   # ext -> in  (e.g. spine -> leaf, ups -> rpp)
            new_edges.append({"src": ext_id, "dst": in_new, "layer": lay,
                              "src_iface": ext_iface, "dst_iface": e.get("dst_iface", 0)})

    nodes.extend(new_nodes)
    edges.extend(new_edges)
    servers = sum(1 for nn in new_nodes if nn["device"]["device_type"] == "server")
    where = (f"row {t['new_row']}" if mode == "new_row"
             else f"row {t['src_row']} widened")
    return {"target": f"{t['dc']}/{t['room']} {where}",
            "devices": len(new_nodes), "servers": servers, "edges": len(new_edges)}


def main(argv) -> int:
    if not (2 <= len(argv) <= 3):
        print(__doc__)
        return 2
    path = argv[1]
    dry = "--dry-run" in argv
    topo = json.loads(Path(path).read_text(encoding="utf-8"))

    devs = [n["device"] for n in topo["nodes"]]
    alloc = Allocator(devs)
    ids = {d["id"] for d in devs}

    print(f"Loaded {path}: {len(topo['nodes'])} nodes, {len(topo['edges'])} edges")
    stats = [add_row(topo, t, alloc, ids) for t in TARGETS]
    for s in stats:
        print(f"  + {s['target']}: {s['devices']} devices "
              f"({s['servers']} servers), {s['edges']} edges")
    print(f"Now: {len(topo['nodes'])} nodes, {len(topo['edges'])} edges")

    if dry:
        print("[dry-run] not written")
        return 0
    Path(path).write_text(json.dumps(topo, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
