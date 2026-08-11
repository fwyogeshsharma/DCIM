"""Load-aware SKU sizing for power-distribution / backup gear.

Picks the smallest real SKU whose nameplate covers a node's *computed* downstream
electrical load at a target utilisation — so a PDU/RPP/UPS/generator's model label
matches what it actually carries, and load% (measured against the catalog rating
in core.device_manager) lands in a realistic band instead of the old load÷0.8
self-derivation that always read ~80%.

Operates on the JSON node/edge lists (``[{id, device:{...}}]`` + ``[{src,dst,
layer}]``) so it is shared by the topology GENERATOR (tools/generate_topology.py,
at build time) and the one-shot topology PATCHER (tools/fix_power_skus.py). One
source of truth — both stay in agreement.

Sizing basis (real datacenter practice):
  • Generator : sized to the WHOLE DC facility load (IT + cooling), because an
                N+1 genset must carry the full plant when its peer is out. Run
                target ~50% (gensets carry with margin; oversizing is normal).
  • UPS       : ~80% target — modules run hot but within the continuous rating;
                each 2N bus carries the full DC load on failover.
  • RPP       : ~70% target — panel/branch-breaker headroom.
  • Rack PDU  : ~60% target, and it must have enough OUTLETS for the cords already
                seated on it — a strip is chosen by receptacle count as much as by
                amps. A full compute rack (~11-15 kW, ~18 cords) lands on a 22 kW
                3-phase unit; a 300 W plant rack with four cords stays on a
                single-phase strip, which is what anyone would actually install.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

from core.device_manager import PDU_OUTLET_CATALOG, DeviceType, _MODEL_RATED_W
from core.device_models import DEVICE_MODELS

# Tier rank along the power chain; loads (servers, switches, cooling, …) = 9.
_TIER = {"utility_feed": 0, "generator": 0, "switchgear": 1, "ats": 2,
         "ups": 3, "mcc": 3, "rpp": 4, "mpp": 4, "pdu": 5, "floor_pdu": 5}
_LOAD_TIER = 9

# Per-type target utilisation and a minimum rating floor (W). The electrical
# upstream (utility feed, switchgear, ATS, MCC) is deliberately absent: those are
# rated by BUS AMPACITY, a discrete construction choice (a 3000 A switch, an 800 A
# MCC), not something you re-derive from today's load. They keep their catalog
# rating and are only traversed, never re-SKU'd.
_TARGET = {"generator": 0.50, "ups": 0.80, "rpp": 0.70, "pdu": 0.60, "floor_pdu": 0.70}

# A floor-PDU is a room-scale cabinet feeding many racks; nothing under 8.6 kW is
# one. Rack PDUs are NOT floored here any more.
#
# They used to be, at the same 8600 W, "to keep a lightly loaded network/OOB rack on
# a proper rack PDU" — but 8600 W is the rating of the smallest THREE-PHASE strip in
# the catalog, so the floor silently meant "three-phase or bigger". It wanted to move
# twenty network- and plant-room strips off a single-phase APC AP8941 (4992 VA, 21
# C13 + 3 C19) onto an AP8865 (8.6 kW, 3-phase) to serve racks drawing 0.3-2.1 kW
# through three to six cords. Nobody runs a three-phase feed to a 300 W rack: it
# triples the RPP poles the rack consumes (a 3-pole breaker per side instead of one)
# for capacity that will never be used.
#
# What a strip actually has to satisfy is amps AND RECEPTACLES, so the outlet count
# is the real constraint on a light-but-dense rack — see _min_outlets/select_sku.
_MIN_RATING = {"floor_pdu": 8600}

# Types whose SKU is decided ONCE FOR THE DESIGN, not per site's current fill.
#
# A panelboard is a construction item. You order the room's board with the room,
# to the room's design load, and it is still that board when half the floor is
# empty — nobody swaps a 125 A RPP for a 100 A one because two racks have not
# arrived yet, and nobody swaps it back when they do. Sizing it to the load
# CONNECTED TODAY is what produced the defect this exists to fix: DC1's halls got
# 125 A boards on 8 racks while DC2's identical halls — same 5x13 = 65 rack grid,
# same design, same drawings — got 100 A boards because they currently hold 7.
# Two sites built to one design cannot have two different bills of material.
#
# So peers are sized together: every board that plays the same role in the same
# kind of room, at any site, gets the SKU that covers the WORST case among them.
# Not the whole room's build-out — that would size this estate's single hall pair
# to 65 racks (~975 kW, a board that does not exist) and misread the topology,
# where one A/B pair serves the racks presently on the floor. Worst-peer keeps the
# selector honest about real load while making the two sites agree, and it holds
# as DC2 fills: the board it already has covers what DC1's is carrying.
#
# Rack PDUs are deliberately NOT here. A strip is bought per rack, with the rack,
# and a 30 A unit in a light OOB rack beside a 32 A three-phase unit in a compute
# rack is correct, not an inconsistency.
_PEER_EQUALISED = {"rpp"}


def _board_side(name: str) -> str:
    """'A' / 'B' off a board's leading name code (RPPA-DC1-HA-R1-04 -> 'A').

    The estate's naming puts the functional role in the FIRST segment (see the
    device-naming scheme), and an A-side board and a B-side board in the same room
    are peers of each other only across sites, never with each other — on a 2N
    floor they can legitimately carry different racks.
    """
    code = (name or "").split("-", 1)[0].upper()
    return code[-1] if code[-1:] in ("A", "B") else ""


def _peer_key(d: dict) -> tuple:
    """What makes two boards the same item in the bill of materials: same type,
    same kind of room, same side of the 2N pair — at any datacenter."""
    return (d.get("device_type") or "", d.get("room") or "", _board_side(d.get("name") or ""))


def _rating_for(model_name: str) -> int:
    """Catalog throughput rating (W) for a model string; 0 if unknown.
    Mirrors core.device_manager.rated_capacity_w (first substring match wins)."""
    m = (model_name or "").lower()
    for key, w in _MODEL_RATED_W.items():
        if key in m:
            return w
    return 0


def _candidates(dtype_value: str, vendor_value: str) -> list[tuple[int, str]]:
    """[(rating_w, model_name)] for a (device_type, vendor), ascending by rating,
    limited to models that carry a catalog rating."""
    out: list[tuple[int, str]] = []
    for (dt, vn), models in DEVICE_MODELS.items():
        if dt.value != dtype_value or vn.value != vendor_value:
            continue
        for mdl in models:
            r = _rating_for(mdl.name)
            if r > 0:
                out.append((r, mdl.name))
    return sorted(out)


def _outlet_count(model_name: str) -> Optional[int]:
    """Receptacles (C13 + C19) on a rack-PDU SKU; None when the catalog has no
    entry, in which case the caller cannot judge it and must not exclude it."""
    spec = PDU_OUTLET_CATALOG.get(model_name)
    if not spec:
        return None
    c13, c19 = spec[0], spec[1]
    return int(c13) + int(c19)


def select_sku(dtype_value: str, vendor_value: str, load_w: float,
               min_outlets: int = 0) -> Optional[str]:
    """Smallest SKU of this (type, vendor) whose rating covers *load_w* at the
    type's target utilisation AND offers at least *min_outlets* receptacles.
    Falls back to the largest available if nothing is big enough (it will then
    legitimately read a high load%). None if the vendor offers no rated model of
    this type.

    min_outlets is what stops the watt figure alone deciding a rack PDU. A rack of
    twenty idle machines draws little and still needs twenty receptacles, and this
    estate seats every cord on a real outlet — so a strip that covers the amps but
    not the plugs is not a candidate.

    A SKU whose receptacle count is NOT in the outlet catalog cannot be certified to
    fit, so once min_outlets is in play it is passed over rather than assumed
    adequate. Being lenient there defeated the check on the only case that matters:
    APC AP8681 carries no catalog entry, and a permissive branch handed that 3.7 kW
    strip to a twenty-cord rack. It stays reachable through the largest-available
    fallback below, which is the honest place for a SKU nothing is known about."""
    cands = _candidates(dtype_value, vendor_value)
    if not cands:
        return None
    target = _TARGET.get(dtype_value, 0.70)
    need = load_w / target if target > 0 else load_w
    need = max(need, _MIN_RATING.get(dtype_value, 0))
    for r, name in cands:
        if r < need:
            continue
        if min_outlets:
            n_out = _outlet_count(name)
            if n_out is None or n_out < min_outlets:
                continue
        return name
    return cands[-1][1]


def dangling_power_edges(nodes: list, edges: list) -> list[dict]:
    """Power edges naming a node that is not in *nodes*.

    A cord with one end missing is a load the sizer cannot see and a device the
    floor plan cannot show. Surfaced so a caller can print it; repairing it means
    knowing whether the node was deleted in error or the edge was left behind,
    which is a decision for a human.
    """
    ids = {n["id"] for n in nodes}
    out = []
    for e in edges:
        if e.get("layer") != "power":
            continue
        missing = [k for k in ("src", "dst") if e.get(k) not in ids]
        if missing:
            out.append({"src": e.get("src"), "dst": e.get("dst"), "missing": missing})
    return out


def _downstream_load(start: str, adj: dict, tier, pdraw) -> float:
    """Sum of load-tier power draw reachable strictly downstream of *start*
    (never traverses back upstream), in W."""
    st = tier(start)
    seen = {start}
    q = deque([start])
    total = 0.0
    while q:
        c = q.popleft()
        for nb in adj[c]:
            if nb in seen:
                continue
            tb = tier(nb)
            if tb <= tier(c):
                continue                      # only ever move downstream
            seen.add(nb)
            if tb == _LOAD_TIER:
                total += pdraw(nb)
            else:
                q.append(nb)
    return total


def rightsize_nodes(nodes: list, edges: list,
                    only_types: set | None = None) -> list[dict]:
    """Rewrite vendor-consistent model_name on every power-distribution/backup
    node to a SKU sized against its computed downstream load, and clear
    rated_power_w so it re-derives from the catalog. Mutates *nodes* in place;
    returns a list of {name, from, to, load_kw, rated_kw} change records.

    *only_types* restricts which device types are REWRITTEN (the whole graph is
    still walked, so loads are unchanged). Re-SKUing a rack PDU is not a free
    relabel — outlet count and receptacle mix come from the SKU, and this estate
    seats every cord on a real outlet with capacity enforced — so a run aimed at
    the panelboards should not quietly re-strip twenty racks on the way past."""
    dev = {n["id"]: n["device"] for n in nodes}

    # dev.get, not dev[...]: an edge can outlive the node it points at. This
    # topology ships two such edges (a power cord and a management link to a device
    # that was deleted without them), and indexing straight into dev raised
    # KeyError the moment the walk reached one — so the whole re-SKU pass, generator
    # and patcher alike, could not run against the estate at all. A cord to a device
    # that is not there carries nothing, which is exactly what a 0 W unknown-tier
    # leaf contributes here. Reported by _dangling_power_edges, not silently healed:
    # deciding whether the node or the edge is the mistake is not this function's
    # call to make.
    def dtype(nid: str) -> str:
        return (dev.get(nid) or {}).get("device_type") or ""

    def tier(nid: str) -> int:
        return _TIER.get(dtype(nid), _LOAD_TIER)

    def pdraw(nid: str) -> float:
        return float((dev.get(nid) or {}).get("power_draw_w") or 0)

    adj: dict[str, set] = defaultdict(set)
    for e in edges:
        if e.get("layer") == "power":
            adj[e["src"]].add(e["dst"])
            adj[e["dst"]].add(e["src"])

    # Whole-DC facility totals — used to size N+1 gensets to the full plant.
    dc_total: dict[str, float] = defaultdict(float)
    for d in dev.values():
        if _TIER.get(d.get("device_type"), _LOAD_TIER) == _LOAD_TIER:
            dc_total[d.get("datacenter") or ""] += float(d.get("power_draw_w") or 0)

    # Cords SEATED on each PDU — its downstream power edges only. The upstream feed
    # from the RPP is an inlet, not an outlet, and counting it would demand one
    # receptacle too many on every strip in the estate.
    cords_of: dict[str, int] = defaultdict(int)
    for e in edges:
        if e.get("layer") != "power":
            continue
        for a, b in ((e.get("src"), e.get("dst")), (e.get("dst"), e.get("src"))):
            if dtype(a) in ("pdu", "floor_pdu") and tier(b) > tier(a):
                cords_of[a] += 1

    # Pass 1 — each node's own downstream load.
    load_of: dict[str, float] = {}
    for n in nodes:
        d = n["device"]
        t = d.get("device_type")
        if t not in _TARGET:
            continue          # in _TIER (so it is traversed) but never re-SKU'd
        load = _downstream_load(n["id"], adj, tier, pdraw)
        if t == "generator":
            load = max(load, dc_total.get(d.get("datacenter") or "", 0.0))
        load_of[n["id"]] = load

    # Pass 2 — lift every peer-equalised board to the worst load in its peer group,
    # so identical rooms at different sites are issued the identical board.
    peer_max: dict[tuple, float] = {}
    for n in nodes:
        d = n["device"]
        if d.get("device_type") in _PEER_EQUALISED and n["id"] in load_of:
            k = _peer_key(d)
            peer_max[k] = max(peer_max.get(k, 0.0), load_of[n["id"]])

    changes: list[dict] = []
    for n in nodes:
        d = n["device"]
        t = d.get("device_type")
        if n["id"] not in load_of:
            continue
        if only_types is not None and t not in only_types:
            continue
        load = load_of[n["id"]]
        if t in _PEER_EQUALISED:
            load = max(load, peer_max.get(_peer_key(d), 0.0))
        model = select_sku(t, d.get("vendor") or "", load,
                           min_outlets=cords_of.get(n["id"], 0))
        if not model or model == d.get("model_name"):
            continue
        # RATCHET: capacity only ever goes UP.
        #
        # Installed gear is installed. A rack whose servers were decommissioned last
        # month still has the 30 A strip that was fitted with it, and nobody swaps a
        # 22 kW compute strip for a 16 A one because the rack is half empty this
        # quarter — the same reason an RPP is not re-boarded when two racks are
        # pulled (see _PEER_EQUALISED). Without this, re-running the sizer against a
        # partly-filled estate quietly writes DOWN forty-four strips, and the next
        # run after the racks refill writes them back up: churn that reads as real
        # capacity change on every report that watches these labels.
        #
        # Undersizing is still corrected, which is the case that actually matters —
        # a node carrying more than its nameplate must show as an overload.
        if _rating_for(model) < _rating_for(d.get("model_name") or ""):
            continue
        changes.append({
            "name": d.get("name"), "from": d.get("model_name"), "to": model,
            "load_kw": round(load / 1000, 1),
            "rated_kw": round(_rating_for(model) / 1000, 1),
        })
        d["model_name"] = model
        d["rated_power_w"] = None            # force catalog re-derive at load time
    return changes
