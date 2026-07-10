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
  • Rack PDU  : ~60% target, floored at a real 3-phase-class unit so a lightly
                loaded network/OOB rack still gets a proper rack PDU, while a full
                compute rack (~11-15 kW) lands on a 22 kW 3-phase strip.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

from core.device_manager import DeviceType, _MODEL_RATED_W
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
_MIN_RATING = {"pdu": 8600, "floor_pdu": 8600}   # keep rack PDUs a real 3-phase-class unit


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


def select_sku(dtype_value: str, vendor_value: str, load_w: float) -> Optional[str]:
    """Smallest SKU of this (type, vendor) whose rating covers *load_w* at the
    type's target utilisation. Falls back to the largest available if nothing is
    big enough (it will then legitimately read a high load%). None if the vendor
    offers no rated model of this type."""
    cands = _candidates(dtype_value, vendor_value)
    if not cands:
        return None
    target = _TARGET.get(dtype_value, 0.70)
    need = load_w / target if target > 0 else load_w
    need = max(need, _MIN_RATING.get(dtype_value, 0))
    for r, name in cands:
        if r >= need:
            return name
    return cands[-1][1]


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


def rightsize_nodes(nodes: list, edges: list) -> list[dict]:
    """Rewrite vendor-consistent model_name on every power-distribution/backup
    node to a SKU sized against its computed downstream load, and clear
    rated_power_w so it re-derives from the catalog. Mutates *nodes* in place;
    returns a list of {name, from, to, load_kw, rated_kw} change records."""
    dev = {n["id"]: n["device"] for n in nodes}

    def dtype(nid: str) -> str:
        return dev[nid].get("device_type") or ""

    def tier(nid: str) -> int:
        return _TIER.get(dtype(nid), _LOAD_TIER)

    def pdraw(nid: str) -> float:
        return float(dev[nid].get("power_draw_w") or 0)

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

    changes: list[dict] = []
    for n in nodes:
        d = n["device"]
        t = d.get("device_type")
        if t not in _TARGET:
            continue          # in _TIER (so it is traversed) but never re-SKU'd
        load = _downstream_load(n["id"], adj, tier, pdraw)
        if t == "generator":
            load = max(load, dc_total.get(d.get("datacenter") or "", 0.0))
        model = select_sku(t, d.get("vendor") or "", load)
        if not model or model == d.get("model_name"):
            continue
        changes.append({
            "name": d.get("name"), "from": d.get("model_name"), "to": model,
            "load_kw": round(load / 1000, 1),
            "rated_kw": round(_rating_for(model) / 1000, 1),
        })
        d["model_name"] = model
        d["rated_power_w"] = None            # force catalog re-derive at load time
    return changes
