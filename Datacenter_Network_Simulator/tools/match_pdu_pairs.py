#!/usr/bin/env python3
"""Make each rack's A/B PDU pair the SAME SKU, so the pair is symmetric.

Every rack in this topology shipped a MISMATCHED pair — A an APC, B a Raritan, with
different outlet counts. That is not how racks are bought. An A/B pair is ordered
identical because:

  * FAILOVER SYMMETRY. In 2N the survivor carries 100% of the rack, so the rack's real
    redundant capacity is min(A, B). Anything B has beyond A is stranded by definition —
    here B's 24 C13 against A's 21 meant 3 receptacles that could never be used in a
    redundant pair.
  * OUTLET PARITY. Operators rely on "this server is outlet 7 on both sides". Different
    SKUs mean different numbering, different MIBs (APC rPDU2OutletMeteredStatusTable vs
    Raritan PDU2-MIB), different firmware.
  * SPARING. One spare covers both sides.

Each pair standardizes on its ROOM's vendor at its own electrical class (see TARGETS):
the halls go Raritan (their compute racks already are), the Network Room and Central
Plant stay APC — which is how sites really buy (a room is fitted out as one project),
and it keeps BOTH vendors' MIBs exercised, this being a simulator that exists to be
polled. Class picks the model, so a strip is never moved onto one that cannot carry its
load: a hall's network row draws ~0.3 kW and takes the 5 kW 1-phase strip; its compute
racks draw 7.3 kW/side and take the 18.4 kW 3-phase one. Ratings are unchanged by any
swap here, so the power cascade does not move.

WHAT THIS DOES **NOT** FIX — be clear about it: a compute rack needs ~29 C13 per side to
reach its 17.6 kW budget on ~600W servers, and 24 is the catalog's ceiling at that
capacity. Outlets still bind before power on an all-1U fleet (24 < 29). The real fix is a
3-phase strip with ~36 C13 (trading the C19s, which sit 6% used), and no such SKU exists
in the catalog yet. This removes the mismatch; it does not remove the outlet ceiling.

The Sentry PT40 / 4805-XLS (40 and 48 C13) are NOT candidates despite the outlet count:
they are 1-phase 30A/208V = 5.0 kW, and the compute PDUs carry 7.3 kW per side. Fitting
one would be an instant 46% overload. Outlets you cannot feed are not capacity.

OUTLETS ARE REGENERATED, AND CORDS RE-MAPPED. Unlike a server re-SKU (where the port
list is left alone so the BMC edge keeps its index), a PDU's outlets ARE its SKU — the
whole point is to get the new layout. The index meaning shifts between SKUs:

    APC AP8865        C13 = 1..21   C19 = 22..33
    Raritan PX3-5878  C13 = 1..24   C19 = 25..36
    APC AP8941        C13 = 1..21   C19 = 22..24   (24 outlets)
    Raritan PX2-5170CR C13 = 1..24  C19 = 25..30   (30 outlets)

so a cord on outlet 22 is a C19 on an AP8865 and a C13 on a PX3-5878, and index 25 does
not exist at all on an AP8941. Any cord whose outlet would change TYPE, or fall off the
end of a shorter strip, is re-seated onto a real receptacle of the type its PSU inlet
needs. C13 cords on 1..21 keep their index, since every SKU here starts C13 at 1.

Idempotent: a topology whose pairs already match reports 0 and writes nothing.

Usage:
    python tools/match_pdu_pairs.py topologies/dual_dc_enterprise.json
    python tools/match_pdu_pairs.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.device_manager import (          # noqa: E402
    Device, DeviceType, Vendor, PDU_OUTLET_CATALOG,
)

# Which SKU a pair standardizes on, by (room family, electrical class). Both members of
# every pair already share a class — (phases, amps) — so matching never changes what a
# rack can carry; only the receptacle layout and the badge move.
#
# ROOM decides the vendor, because that is how sites really buy: a room is fitted out as
# one project, so it standardizes on one PDU vendor. Different rooms may differ (bought
# at different times) — which is also what keeps BOTH vendors' MIBs exercised here, and
# the point of this simulator is to be a target for monitoring tools. The halls go
# Raritan (their compute racks already are); the Network Room and Central Plant stay APC.
#
# CLASS decides the model, so a strip is never moved onto one that cannot carry its load:
# a hall's network row draws ~0.3 kW and takes the 5 kW 1-phase strip, while its compute
# racks draw 7.3 kW/side and take the 18.4 kW 3-phase one.
#
# Keyed on PHASES, not (phases, amps): the amp rating is part of what the datasheet pass
# corrected, so keying on it would silently stop matching the moment a SKU's real current
# was fixed.
TARGETS = {
    # Compute racks. The AP8886 is the ONLY strip in the catalog verified to 2N a 17.6 kW
    # rack: 22.0 kW (32A, 3-phase 400V wye) and 30 C13 — enough receptacles to reach that
    # budget at ~600W/server, which nothing else here has. It replaces a pair that was
    # fabricated on both sides: the AP8865 is really an 8.6 kW strip (it would OVERLOAD
    # by 5.9 kW on an A/B failover of these racks), and the PX3-5878 really carries 6 C13.
    ("hall",  3): ("APC AP8886",        "APC by Schneider Electric"),
    # Hall network rows: ~0.3 kW. The 5 kW 1-phase strip is right-sized; a 22 kW 3-phase
    # one there would be absurd.
    ("hall",  1): ("Raritan PX2-5170CR", "Raritan"),
    ("other", 1): ("APC AP8941",         "APC by Schneider Electric"),  # Network Room / Central Plant
}


def room_family(room: str) -> str:
    return "hall" if (room or "").startswith("Server Hall") else "other"


def rack_key(d: dict) -> tuple:
    return (d.get("datacenter") or "", d.get("room") or "",
            d.get("rack_row") or 0, d.get("rack_num") or 0)


def side(d: dict) -> str:
    code = "".join(c for c in (d.get("name") or "").split("-", 1)[0] if c.isalpha()).upper()
    return "A" if code == "PDUA" else ("B" if code == "PDUB" else "")


def new_outlets(model: str) -> list:
    """The SKU's real outlet list, built by the model itself (Device._generate_outlets
    off PDU_OUTLET_CATALOG) rather than re-derived here — one source of truth."""
    d = Device(name="_probe", device_type=DeviceType.PDU, vendor=Vendor.RARITAN,
               ip_address="0.0.0.0", model_name=model)
    return [o.to_dict() for o in d.outlets]


def target_for(room: str, model: str):
    """(model, vendor) this pair should standardize on, or None if we have no rule."""
    spec = PDU_OUTLET_CATALOG.get(model)
    if not spec:
        return None
    _c13, _c19, phases, _amps, _v = spec
    return TARGETS.get((room_family(room), phases))


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    byid = {n["device"].get("id", n["id"]): n["device"] for n in topo["nodes"]}

    pairs: dict = defaultdict(dict)
    for i, d in byid.items():
        if d.get("device_type") == "pdu" and side(d):
            pairs[rack_key(d)][side(d)] = i

    changed, remapped, skipped = Counter(), [], Counter()
    for key, ab in sorted(pairs.items()):
        if len(ab) != 2:
            continue
        room = byid[ab["A"]].get("room") or ""
        tgt = target_for(room, byid[ab["A"]].get("model_name") or "")
        if tgt is None:
            skipped[f"{room}: {byid[ab['A']].get('model_name')} — no rule"] += 1
            continue
        model, vendor = tgt

        # Re-SKU EITHER side that is not already on the room's target. Whichever it is,
        # the pair ends identical.
        for s in ("A", "B"):
            pid = ab[s]
            d = byid[pid]
            if d.get("model_name") == model:
                continue

            old_type = {o["index"]: o["type"] for o in d["outlets"]}
            fresh = new_outlets(model)
            new_type = {o["index"]: o["type"] for o in fresh}

            # Re-seat any cord whose outlet would change TYPE (or fall off the end of a
            # shorter strip) under the new layout. A C20 inlet must land on a C19;
            # silently leaving it on what is now a C13 would model a plug that does not
            # fit, and an index the new strip does not have is a cord to nowhere.
            taken = {e["outlet"] for e in topo["edges"]
                     if e.get("layer") == "power" and e.get("supply_node") == pid
                     and e.get("outlet") is not None}
            for e in topo["edges"]:
                if (e.get("layer") != "power" or e.get("supply_node") != pid
                        or e.get("outlet") is None):
                    continue
                o = e["outlet"]
                want = old_type.get(o)
                if new_type.get(o) == want:
                    continue                          # index still means the same thing
                free = next((x["index"] for x in fresh
                             if x["type"] == want and x["index"] not in taken), None)
                if free is None:
                    print(f"  !! {d['name']}: no free {want} on {model} for the cord to "
                          f"{byid[e['load_node']]['name']} — left on outlet {o}")
                    continue
                taken.discard(o)
                taken.add(free)
                remapped.append((d["name"], byid[e["load_node"]]["name"], want, o, free))
                e["outlet"] = free

            changed[f"{room_family(room):5s} {d['model_name']}  ->  {model}"] += 1
            d["vendor"] = vendor
            d["model_name"] = model
            d["outlets"] = fresh

    total = sum(changed.values())
    if not dry_run and total:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    verb = "Would re-SKU" if dry_run else "Re-SKU'd"
    print(f"\n{verb} {total} PDU(s) so every rack's A/B pair matches."
          f" {'(dry run)' if dry_run else (f'Wrote {p}' if total else 'No change')}\n")
    for k, c in sorted(changed.items()):
        print(f"  {c:4d}  {k}")
    if remapped:
        print(f"\n  Re-seated {len(remapped)} cord(s) whose outlet type changed with the "
              f"layout:")
        for nm, load, ty, o, f in remapped[:6]:
            print(f"    {nm:22s} {load:22s} {ty} outlet {o} -> {f}")
        if len(remapped) > 6:
            print(f"    ... and {len(remapped)-6} more")
    if skipped:
        print(f"\n  Left alone — not the A/B class this tool matches:")
        for k, c in sorted(skipped.items()):
            print(f"  {c:4d}  {k}")
    print()
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
