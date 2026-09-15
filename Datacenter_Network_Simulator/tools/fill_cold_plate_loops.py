#!/usr/bin/env python3
"""Put a real cold-plate load on a rack that has a CDU bolted into it.

THE DEFECT

Two racks in each site's Hall A hold ONE direct-to-chip server apiece, and a
CoolIT CHx80 sitting in the rack to cool it. The unit is 4U of cabinet space, a
pump set, a brazed-plate exchanger, a facility-water tap and an 18-pair UQD
manifold, and seventeen of those eighteen ports are capped off. Hall B's
equivalent racks carry thirteen each.

Nobody builds that. A CDU is bought for a rack of liquid nodes; one node is a
rounding error against its own pump power. It also made the platform's CDU
figures unreadable - those loops published half a kilowatt on an 80 kW unit,
so every duty, flow and range on them sat at the bottom of its scale where
nothing can be told from anything.

WHAT THIS DOES

For every rack with an in-rack CDU, converts air-cooled servers already in that
cabinet to direct-to-chip SKUs until the loop carries the estate's own standard
liquid-rack population, bounded by the manifold's port count
(device_manager.cdu_manifold_ports).

A conversion is a re-SKU in place, the mirror of what rehome_cdu_loops.py does
in the other direction:

  - the liquid part is chosen at the SAME RACK UNITS as the air part it
    replaces, so no elevation moves and no rack has to be re-planned;
  - vendor is kept where that vendor makes a liquid part. Lenovo, IBM and the
    non-accelerator HPE lines do not, in this registry, so those change vendor -
    which is what actually happens when a floor goes liquid, because the part
    number you can buy decides;
  - power_draw_w follows the new SKU's nameplate UP, and it should: the cold
    plates are there to carry a higher-TDP socket pair than the air part held;
  - both directed cooling edges are added, which is how this topology stores a
    loop.

Racks with no CDU are not touched. A direct-to-chip server with no coolant has
no cooling path at all - see rehome_cdu_loops.py, which exists to clean up
exactly that.

Idempotent: a rack already at target is reported and left alone.

Usage:
    python tools/fill_cold_plate_loops.py topologies/dual_dc_enterprise.json --dry-run
    python tools/fill_cold_plate_loops.py topologies/dual_dc_enterprise.json
    python tools/fill_cold_plate_loops.py topologies/dual_dc_enterprise.json --target 18
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.device_manager import (  # noqa: E402
    DeviceType, cdu_manifold_ports, cdu_serves_own_rack_only, nameplate_power_w,
)
from core.device_models import MODEL_U_HEIGHT, is_liquid_cooled  # noqa: E402

#: Air part -> the liquid part that replaces it, same rack units. Vendor is kept
#: where that vendor sells one; Lenovo, IBM and the 2U HPE lines have no
#: direct-to-chip SKU in this registry, so they land on the part that exists at
#: their height. Changing the badge is the honest answer - a floor does not go
#: liquid by wishing, it goes liquid by buying what is made.
TO_LIQUID = {
    # 1U
    "Dell PowerEdge R640":          "Dell PowerEdge R660 DLC",
    "Supermicro SYS-120U-TNR":      "Supermicro SYS-121H-TNR LCC",
    "HPE ProLiant DL360 Gen10":     "Dell PowerEdge R660 DLC",
    "Lenovo ThinkSystem SR630 V2":  "Supermicro SYS-121H-TNR LCC",
    "Cisco UCS C220 M6":            "Supermicro SYS-121H-TNR LCC",
    # 2U
    "Dell PowerEdge R740":          "Dell PowerEdge R760 DLC",
    "Dell PowerEdge R750":          "Dell PowerEdge R760 DLC",
    "Dell PowerEdge R7525":         "Dell PowerEdge R760 DLC",
    "Supermicro SYS-220U-TNR":      "Supermicro SYS-221H-TNR LCC",
    "HPE ProLiant DL380 Gen10":     "Supermicro SYS-221H-TNR LCC",
    "HPE ProLiant DL380 Gen11":     "Supermicro SYS-221H-TNR LCC",
    "Lenovo ThinkSystem SR650 V2":  "Supermicro SYS-221H-TNR LCC",
    "IBM Power System S922":        "Supermicro SYS-221H-TNR LCC",
    "Cisco UCS C240 M6":            "Supermicro SYS-221H-TNR LCC",
}

#: What a liquid rack holds in this estate, taken from the racks that were built
#: right (Hall B, both sites). Not the manifold's 18: a rack at every port is a
#: rack with nowhere to grow, and no floor is commissioned that way.
DEFAULT_TARGET = 13


def _is_liquid(model: str) -> bool:
    """The registry's own answer, so this cannot drift from the picker that
    gates which SKUs may join a loop."""
    return is_liquid_cooled(model or "")


def _u(model: str) -> int:
    return MODEL_U_HEIGHT.get(model or "", 0)


def _rack_of(dev: dict) -> tuple:
    """The cabinet, keyed exactly as rehome_cdu_loops.py keys it.

    There is no `rack` field on a device; the cabinet is (row, number) within a
    room. Keying on a field that does not exist collapses a whole hall into one
    group, which reads as "every loop is already full" and does nothing.
    """
    return (dev.get("datacenter") or "", dev.get("room") or "",
            str(dev.get("floor") or ""), dev.get("rack_row") or 0,
            dev.get("rack_num") or 0)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in argv
    target = DEFAULT_TARGET
    if "--target" in argv:
        target = int(argv[argv.index("--target") + 1])
    if not args:
        print(__doc__)
        return 2

    p = Path(args[0])
    topo = json.loads(p.read_text(encoding="utf-8"))
    devs = {n["device"]["id"]: n["device"] for n in topo["nodes"]}

    # Which rack each device sits in, and where the CDUs are.
    racks: dict[tuple, list[dict]] = defaultdict(list)
    for d in devs.values():
        racks[_rack_of(d)].append(d)

    # Servers already on a loop, so a re-run does not plumb one twice.
    plumbed: set[str] = set()
    for e in topo["edges"]:
        if e.get("layer") != "cooling":
            continue
        for a, b in ((e["src"], e["dst"]), (e["dst"], e["src"])):
            if devs.get(a, {}).get("device_type") == DeviceType.SERVER.value:
                plumbed.add(a)

    converted: list[tuple[dict, str, str, str]] = []
    skipped: Counter = Counter()

    for key, members in sorted(racks.items()):
        cdus = [d for d in members
                if d.get("device_type") == DeviceType.CDU.value
                and cdu_serves_own_rack_only(d.get("model_name") or "")]
        if not cdus:
            continue
        cdu = cdus[0]
        ports = cdu_manifold_ports(cdu.get("model_name") or "") or target
        want = min(target, ports)

        servers = [d for d in members if d.get("device_type") == DeviceType.SERVER.value]
        liquid = [d for d in servers if _is_liquid(d.get("model_name") or "")]
        if len(liquid) >= want:
            skipped[cdu["name"]] = len(liquid)
            continue

        # Take the biggest air boxes first: the hottest sockets are the ones the
        # cold plates were bought for, and a 2U part converts to a 2U part.
        air = [d for d in servers if not _is_liquid(d.get("model_name") or "")]
        air.sort(key=lambda d: -float(d.get("power_draw_w") or 0))
        for d in air:
            if len(liquid) >= want:
                break
            old = d.get("model_name") or ""
            new = TO_LIQUID.get(old)
            if new is None:
                print(f"  !! {d['name']}: {old!r} has no liquid part at its height "
                      f"- left on air. Add a TO_LIQUID entry.")
                continue
            if _u(old) != _u(new):
                print(f"  !! {d['name']}: {old!r} is {_u(old)}U and "
                      f"{new!r} is {_u(new)}U - would move the elevation, "
                      f"skipped.")
                continue
            d["model_name"] = new
            d["power_draw_w"] = nameplate_power_w(DeviceType.SERVER, new)
            liquid.append(d)
            converted.append((d, old, new, cdu["id"]))

    # Re-rate every liquid node, converted or not.
    #
    # A device carries its own power_draw_w, and that STORED value wins over the
    # registry - so raising the DLC nameplates in device_manager moved nothing
    # that was already racked. Converting Hall A without this left the two halls
    # disagreeing about what the same part number draws: Hall A at 1.4 kW and
    # Hall B at 0.86 kW, same SKU, same rack position, one estate.
    rerated: Counter = Counter()
    for d in devs.values():
        if d.get("device_type") != DeviceType.SERVER.value:
            continue
        model = d.get("model_name") or ""
        if not _is_liquid(model):
            continue
        want_w = nameplate_power_w(DeviceType.SERVER, model)
        if want_w and int(d.get("power_draw_w") or 0) != int(want_w):
            rerated[f"{model}  {d.get('power_draw_w')} -> {want_w} W"] += 1
            d["power_draw_w"] = want_w

    # Both directions, which is how this topology stores a loop.
    for d, _old, _new, cid in converted:
        if d["id"] in plumbed:
            continue
        topo["edges"].append({"src": cid, "dst": d["id"], "src_iface": None,
                              "dst_iface": None, "layer": "cooling"})
        topo["edges"].append({"src": d["id"], "dst": cid, "src_iface": None,
                              "dst_iface": None, "layer": "cooling"})

    changed = bool(converted or rerated)
    if not dry_run and changed:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")

    verb = "Would convert" if dry_run else "Converted"
    print(f"\n{verb} {len(converted)} air server(s) to direct-to-chip, "
          f"target {target} per loop:")
    by_cdu: Counter = Counter()
    kw: dict[str, float] = defaultdict(float)
    for d, old, new, cid in converted:
        by_cdu[devs[cid]["name"]] += 1
        kw[devs[cid]["name"]] += float(d.get("power_draw_w") or 0) / 1000.0
    for name, n in sorted(by_cdu.items()):
        print(f"   {n:3d}  -> {name}   (+{kw[name]:.1f} kW nameplate on the loop)")
    print("\n  by part:")
    for k, c in sorted(Counter(f"{o}  ->  {n}" for _d, o, n, _c in converted).items()):
        print(f"   {c:3d}  {k}")
    if rerated:
        print("\n  re-rated to the registry nameplate (stored value was stale):")
        for k_, c in sorted(rerated.items()):
            print(f"   {c:3d}  {k_}")
    if skipped:
        print(f"\n  already at target, untouched: "
              f"{', '.join(f'{k} ({v})' for k, v in sorted(skipped.items()))}")
    tail = ('(dry run)' if dry_run
            else f'Wrote {p}' if changed else 'Nothing to change')
    print(f'\n{tail}\n')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
