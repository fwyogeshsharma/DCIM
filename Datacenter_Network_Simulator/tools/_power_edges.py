"""Read a device's A/B power feed from a raw topology's EDGES.

The JSON twin of `TopologyEngine.power_feeds`. Tools work on the topology dict, not a
live engine, so they cannot call the real thing — but they must answer the question the
same way it does: from the cord.

There is no `Device.power_source_a/b` to read. It was a cache of exactly this, and it
drifted the moment anything cloned or re-corded a device — 14 network devices ended up
naming the wrong HALL's PDUs, and 72 references pointed at deleted devices. The edge
carries `supply_node`/`load_node`/`outlet`/`psu` and cannot lie about what is plugged in.
See `Device` in core/device_manager.py.
"""
from __future__ import annotations

_PDU_TYPES = ("pdu", "floor_pdu")


def _by_id(topo: dict) -> dict:
    return {n["device"].get("id", n["id"]): n["device"] for n in topo["nodes"]}


def feed_ids(topo: dict, dev_id: str, byid: dict | None = None) -> tuple:
    """(a_id, b_id) — the PDUs CORDED to *dev_id*'s PSUs, ordered by PSU index, so
    a = PSU1's supply and b = PSU2's. Either may be None.

    Reads ONLY edges carrying supply_node/load_node, exactly like the engine's
    power_feeds. An edge without them is null-terminated ON PURPOSE and is NOT a cord:
    `_power_ends` returns None unless the supply has outlets AND the load has PSUs, so
    rpp->pdu (a breaker position), mcc->pump, and pdu->sensor (a DPX2 hangs off the
    PDU's sensor port and has no PSU) all land here. 239 of this topology's 1079 power
    edges are such feeds. Treating one as a cord invents a feed that does not exist —
    which is the exact fiction the power_source_a/b field was removed for. If you want
    "what draws from this PDU at all", use fed_by().
    """
    cords = []
    for e in topo.get("edges", []):
        if e.get("layer") != "power":
            continue
        sup, load = e.get("supply_node"), e.get("load_node")
        if sup and load and load == dev_id:
            cords.append((e.get("psu"), sup))
    cords.sort(key=lambda x: (x[0] is None, x[0]))
    return (cords[0][1] if cords else None,
            cords[1][1] if len(cords) > 1 else None)


# Distribution tiers that FEED a PDU rather than draw from one. Used to orient an edge
# that carries no terminations, so an RPP upstream of a PDU never reads as its load.
_UPSTREAM_TYPES = ("rpp", "ups", "switchgear", "ats", "utility_feed", "generator",
                   "mcc", "mpp", "floor_pdu")


def fed_by(topo: dict, pdu_ids, byid: dict | None = None) -> list:
    """Every device drawing power from any PDU in *pdu_ids* — the DOWNSTREAM side.

    Broader than feed_ids on purpose: this answers "what would be affected if these PDUs
    moved", so it must include the sensor-port devices that have no PSU and therefore no
    cord (a DPX2 is powered over the PDU's RJ-12 sensor port). It excludes whatever
    feeds the PDU from above (an RPP is on the other end of a power edge too, and is not
    its load).
    """
    byid = byid if byid is not None else _by_id(topo)
    want = set(pdu_ids)
    out, seen = [], set()
    for e in topo.get("edges", []):
        if e.get("layer") != "power":
            continue
        src, dst = e.get("src"), e.get("dst")
        sup, load = e.get("supply_node"), e.get("load_node")
        if sup and load:
            if sup not in want:
                continue                    # this PDU is the load here, not the supply
            cand = load
        else:
            if src in want:
                cand = dst
            elif dst in want:
                cand = src
            else:
                continue
            if (byid.get(cand) or {}).get("device_type") in _UPSTREAM_TYPES:
                continue                    # that end feeds the PDU, it is not its load
        d = byid.get(cand)
        if d is not None and cand not in seen:
            seen.add(cand)
            out.append(d)
    return out
