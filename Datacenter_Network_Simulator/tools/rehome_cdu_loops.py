#!/usr/bin/env python3
"""Make every coolant loop rack-local, as an in-rack CDU requires.

THE DEFECT

The curated topology cabled its cold-plate loops HALL-WIDE: 18 of 108 loops run from
a server in one cabinet to a CDU in another. Every CDU in this topology is a CoolIT
CHx80 — a 4U unit bolted INTO the rack it serves, feeding that rack's manifold. The
cold-plate hoses land on UQD pairs inside the cabinet; they do not cross the aisle.
So those 18 loops describe plumbing that cannot be built.

That also makes the model self-inconsistent: core.device_manager.cdu_serves_own_rack_only
now scopes an in-rack CDU to its own cabinet, and the Add-Device picker enforces it.
New builds are rack-local while the seeded ones are not.

WHAT THIS DOES

Two cases, decided by whether the server's OWN rack has a CDU:

  rack HAS a CDU   -> RE-HOME. The loop is moved to the local unit: both directional
                      cooling edges are repointed, subject to that manifold still
                      having a free UQD pair (cdu_manifold_ports).

  rack has NO CDU  -> UNPLUMB AND RE-SKU. The loop is removed, and the server is
                      returned to an air-cooled SKU of the same vendor and height.
                      It cannot simply be left unplumbed: a direct-to-chip server
                      has cold plates where an air-cooled one has heatsinks, so with
                      no coolant it has no cooling path at all. A DLC part number
                      sitting off-loop is not a hybrid rack, it is a dead server.
                      power_draw_w follows the air SKU's nameplate back down.

Hybrid racks — air-cooled servers sharing a cabinet with liquid ones — are untouched
and remain correct; that is what R2-01's 7 air + 11 liquid already is.

Idempotent: a topology whose loops are all rack-local is reported and left alone.

Usage:
    python tools/rehome_cdu_loops.py topologies/dual_dc_enterprise.json --dry-run
    python tools/rehome_cdu_loops.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.device_manager import (DeviceType, cdu_manifold_ports,               # noqa: E402
                                 cdu_serves_own_rack_only, nameplate_power_w)
from core.device_models import MODEL_U_HEIGHT, is_liquid_cooled               # noqa: E402

# DLC SKU -> the air-cooled box it becomes when its rack has no CDU. Same vendor and
# the SAME rack height in every case, so nothing moves and no elevation changes; the
# vendor match also keeps the BMC port name (iDRAC/IPMI) correct without touching it.
TO_AIR = {
    "Dell PowerEdge R660 DLC":       "Dell PowerEdge R640",
    "Dell PowerEdge R760 DLC":       "Dell PowerEdge R750",
    "Supermicro SYS-121H-TNR LCC":   "Supermicro SYS-120U-TNR",
    "Supermicro SYS-221H-TNR LCC":   "Supermicro SYS-220U-TNR",
    "HPE ProLiant DL380a Gen11 DLC": "HPE ProLiant DL560 Gen10",
}


def _rack_of(dev: dict) -> tuple:
    return (dev.get("datacenter") or "", dev.get("room") or "",
            str(dev.get("floor") or ""), dev.get("rack_row") or 0,
            dev.get("rack_num") or 0)


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    devs = {n["id"]: n["device"] for n in topo["nodes"]}

    for old, new in TO_AIR.items():
        if MODEL_U_HEIGHT.get(new) != MODEL_U_HEIGHT.get(old):
            print(f"ERROR: {new!r} is {MODEL_U_HEIGHT.get(new)}U but replaces a "
                  f"{MODEL_U_HEIGHT.get(old)}U {old!r} — heights must match.")
            return 1
        if is_liquid_cooled(new):
            print(f"ERROR: {new!r} is itself a DLC SKU — it cannot be the air fallback.")
            return 1

    # CDU per rack, and how full each manifold already is.
    cdu_by_rack: dict = {}
    for did, d in devs.items():
        if d.get("device_type") == DeviceType.CDU.value:
            cdu_by_rack.setdefault(_rack_of(d), []).append(did)
    loop_of: dict = defaultdict(set)          # cdu id -> {server id}
    for e in topo["edges"]:
        if e.get("layer") != "cooling":
            continue
        for a, b in ((e["src"], e["dst"]), (e["dst"], e["src"])):
            if (devs.get(a, {}).get("device_type") == DeviceType.CDU.value
                    and devs.get(b, {}).get("device_type") == DeviceType.SERVER.value):
                loop_of[a].add(b)

    # The cross-rack pairs, and where each one should go instead.
    rehome, unplumb = {}, []                  # {server: (old_cdu, new_cdu)}, [server]
    for cid, members in loop_of.items():
        cdu = devs[cid]
        if not cdu_serves_own_rack_only(cdu.get("model_name") or ""):
            continue                          # a row/facility skid may serve the hall
        # Snapshot: the loop sets are mutated below as pairs are reserved.
        for sid in sorted(members):
            if _rack_of(devs[sid]) == _rack_of(cdu):
                continue                      # already local
            local = cdu_by_rack.get(_rack_of(devs[sid]), [])
            target = None
            for lid in local:
                ports = cdu_manifold_ports(devs[lid].get("model_name") or "")
                if not ports or len(loop_of[lid]) < ports:
                    target = lid
                    break
            if target is None:
                unplumb.append(sid)
            else:
                rehome[sid] = (cid, target)
                loop_of[target].add(sid)      # reserve the pair as we go
                loop_of[cid].discard(sid)

    if not rehome and not unplumb:
        print("\nEvery coolant loop is already rack-local — nothing to do.\n")
        return 0

    # Rewrite the edges: drop every cooling edge that is being moved or removed, then
    # append the re-homed pairs. Both directions each time — supply AND return.
    moved, dropped = set(rehome), set(unplumb)
    kept = []
    for e in topo["edges"]:
        if e.get("layer") == "cooling":
            a, b = e["src"], e["dst"]
            srv = (a if devs.get(a, {}).get("device_type") == DeviceType.SERVER.value
                   else b if devs.get(b, {}).get("device_type") == DeviceType.SERVER.value
                   else None)
            if srv in moved or srv in dropped:
                continue
        kept.append(e)
    for sid, (_old, new_cid) in rehome.items():
        kept.append({"src": new_cid, "dst": sid, "src_iface": None,
                     "dst_iface": None, "layer": "cooling"})
        kept.append({"src": sid, "dst": new_cid, "src_iface": None,
                     "dst_iface": None, "layer": "cooling"})
    topo["edges"] = kept

    # Unplumbed servers go back to air.
    resku = Counter()
    for sid in unplumb:
        d = devs[sid]
        old = d.get("model_name") or ""
        new = TO_AIR.get(old)
        if new is None:
            print(f"  !! {d['name']}: {old!r} has no air fallback — left DLC and "
                  f"UNPLUMBED, which is not a runnable server. Add a TO_AIR entry.")
            continue
        d["model_name"] = new
        d["power_draw_w"] = nameplate_power_w(DeviceType.SERVER, new)
        resku[f"{old}  ->  {new}"] += 1

    if not dry_run:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")

    verb = "Would re-home" if dry_run else "Re-homed"
    print(f"\n{verb} {len(rehome)} cross-rack loop(s) to the server's own rack CDU.")
    by_target = Counter(devs[t]["name"] for _o, t in rehome.values())
    for name, c in sorted(by_target.items()):
        print(f"   {c:3d}  -> {name}")
    print(f"\n{'Would unplumb' if dry_run else 'Unplumbed'} {len(unplumb)} server(s) "
          f"in racks with no CDU, and returned them to air-cooled SKUs:")
    for k, c in sorted(resku.items()):
        print(f"   {c:3d}  {k}")
    print(f"\n{'(dry run)' if dry_run else f'Wrote {p}'}\n")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
