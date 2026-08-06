#!/usr/bin/env python3
"""Re-SKU the servers on a CDU cold-plate loop to SKUs that actually ship with DLC.

THE DEFECT

The curated topology plumbs 108 servers into its in-rack CoolIT CHx80 CDUs — a
cooling edge each way, supply and return — but every one of them is an AIR-COOLED
SKU: SR650 V2, DL380 Gen11, R7525, DL360 Gen10, R640. That cannot be built. Direct
liquid cooling is not a field option you bolt onto a shipped server: the cold plates
sit on the CPU/GPU packages in place of the heatsinks, and the UQD quick-disconnects
that mate with the rack manifold are chassis hardware. Vendors sell DLC as a
DIFFERENT PART NUMBER (Supermicro's -LCC suffix is exactly this), which is why
DeviceModel.liquid_cooled is a per-SKU fact and why no air SKU carries it.

Left alone, the topology claims a build that no vendor could deliver, and the
Add-Device LINKS section (which gates its CDU picker on is_liquid_cooled) refuses to
create the very thing the seed already shows.

WHAT THIS DOES

Every server with a cooling edge to a CDU is re-SKU'd to a real DLC machine of the
SAME RACK HEIGHT, so nothing moves and no rack elevation changes:

    1U  ->  Dell PowerEdge R660 DLC   /  Supermicro SYS-121H-TNR LCC
    2U  ->  Dell PowerEdge R760 DLC   /  Supermicro SYS-221H-TNR LCC

Vendor is KEPT where that vendor has a DLC SKU at that height (Dell, Supermicro).
The HPE and Lenovo loop servers move to the Supermicro LCC platform, because their
real DLC lines do not offer an equivalent: HPE's facility-DLC is the Cray XD / ProLiant
Compute XD line (multi-node chassis, not a 1U/2U rack server), and Lenovo's Neptune
DWC is the SD650 tray, which mounts in a DW612S enclosure and so has no rack height
of its own. Inventing "HPE DL380 Gen11 DLC" would trade one fiction for another.
Standardising a liquid hall on one platform is also what operators actually do — the
manifold, UQD couplings and CDU integration are qualified per server platform.

WHAT CHANGES PER SERVER

    model_name    the DLC SKU
    vendor        only when the platform moves (HPE/Lenovo -> Supermicro)
    BMC port name renamed to the new vendor's controller (iLO/XCC -> IPMI) when the
                  vendor changes. The port INDEX is untouched, so the management edge
                  that lands on it stays valid — only its label follows the hardware.
    power_draw_w  re-derived from the new SKU's nameplate. DLC exists to carry a
                  higher-TDP part, so a loop server drawing its old air-cooled figure
                  would understate every PDU/RPP/UPS above it.

WHAT THIS DELIBERATELY DOES NOT TOUCH

    interfaces    Count and indices stay as they are. A server's OOB edge lands on
                  its LAST port (the BMC); regenerating ports from the new SKU would
                  move it and leave that management edge pointing at a port that no
                  longer exists. Same reasoning as resku_tall_servers.py.
    psus/outlets  Every DLC replacement stays under C13_CONTINUOUS_W, so the cord
                  type does not change and the power edges keep their C13 outlets.
                  The tool REFUSES to re-SKU a server whose new nameplate would cross
                  that line rather than silently invalidate a cord.
    rack_unit     Nothing moves — heights are matched exactly.
    cooling edges The loops themselves are already correct; this fixes the hardware
                  hanging off them, not the plumbing.

Idempotent: a loop whose servers are all DLC already is reported and left untouched.

Usage:
    python tools/resku_dlc_loop_servers.py topologies/dual_dc_enterprise.json --dry-run
    python tools/resku_dlc_loop_servers.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.device_manager import (BMC_PORT_NAME, C13_CONTINUOUS_W, DeviceType,   # noqa: E402
                                 Vendor, nameplate_power_w)
from core.device_models import MODEL_U_HEIGHT, is_liquid_cooled                 # noqa: E402
from core.rack_capacity import SERVER_U_HEIGHT                                  # noqa: E402

# (vendor kept where that vendor really ships DLC at this height; see the docstring
# for why HPE and Lenovo move to the Supermicro LCC platform instead.)
RESKU = {
    # ── 1U ──
    "Dell PowerEdge R640":          "Dell PowerEdge R660 DLC",
    "HPE ProLiant DL360 Gen10":     "Supermicro SYS-121H-TNR LCC",
    "Lenovo ThinkSystem SR630 V2":  "Supermicro SYS-121H-TNR LCC",
    "Supermicro SYS-120U-TNR":      "Supermicro SYS-121H-TNR LCC",
    # ── 2U ──
    "Dell PowerEdge R740":          "Dell PowerEdge R760 DLC",
    "Dell PowerEdge R750":          "Dell PowerEdge R760 DLC",
    "Dell PowerEdge R7525":         "Dell PowerEdge R760 DLC",
    "HPE ProLiant DL380 Gen10":     "Supermicro SYS-221H-TNR LCC",
    "HPE ProLiant DL380 Gen11":     "Supermicro SYS-221H-TNR LCC",
    "Lenovo ThinkSystem SR650 V2":  "Supermicro SYS-221H-TNR LCC",
    "Supermicro SYS-220U-TNR":      "Supermicro SYS-221H-TNR LCC",
}

# model_name -> the Vendor the SKU belongs to, for the vendor/BMC rewrite.
VENDOR_OF = {
    "Dell PowerEdge R660 DLC":     Vendor.DELL,
    "Dell PowerEdge R760 DLC":     Vendor.DELL,
    "Supermicro SYS-121H-TNR LCC": Vendor.SUPERMICRO,
    "Supermicro SYS-221H-TNR LCC": Vendor.SUPERMICRO,
}

# Every BMC product name any server vendor here ships, lowercased — used to find the
# port to rename without depending on its index or its role field.
_BMC_NAMES = {n.lower() for n in BMC_PORT_NAME.values()}


def _loop_server_ids(topo: dict) -> set:
    """Ids of servers carrying a cooling edge to a CDU — the same membership test
    DeviceStateStore._cdu_loop_servers uses, read straight off the edges."""
    devs = {n["id"]: n["device"] for n in topo["nodes"]}
    out = set()
    for e in topo["edges"]:
        if e.get("layer") != "cooling":
            continue
        for a, b in ((e["src"], e["dst"]), (e["dst"], e["src"])):
            da, db = devs.get(a), devs.get(b)
            if not da or not db:
                continue
            if da.get("device_type") == "cdu" and db.get("device_type") == "server":
                out.add(b)
    return out


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))

    # Guard the map before touching anything: a replacement that is a different
    # height would run into its neighbour, and one that is not actually a DLC SKU
    # would leave the loop exactly as wrong as it was.
    for old, new in RESKU.items():
        oh = MODEL_U_HEIGHT.get(old, SERVER_U_HEIGHT)
        nh = MODEL_U_HEIGHT.get(new)
        if nh != oh:
            print(f"ERROR: {new!r} is {nh}U but replaces a {oh}U {old!r} — heights "
                  f"must match or servers overlap. Fix RESKU.")
            return 1
        if not is_liquid_cooled(new):
            print(f"ERROR: {new!r} is not marked liquid_cooled in device_models.py — "
                  f"re-SKUing to it would leave the loop just as unbuildable.")
            return 1
        w = nameplate_power_w(DeviceType.SERVER, new)
        if w > C13_CONTINUOUS_W:
            print(f"ERROR: {new!r} draws {w} W, over the {C13_CONTINUOUS_W} W C13 "
                  f"limit — its cords would have to become C19 and every power edge "
                  f"re-terminated. Pick a lower-draw SKU or do the re-cording first.")
            return 1

    loop = _loop_server_ids(topo)
    if not loop:
        print("\nNo server sits on a CDU cooling loop — nothing to do.\n")
        return 0

    done, moved, skipped = Counter(), Counter(), Counter()
    for n in topo["nodes"]:
        if n["id"] not in loop:
            continue
        d = n["device"]
        old = d.get("model_name") or ""
        if is_liquid_cooled(old):
            skipped["already DLC"] += 1
            continue
        new = RESKU.get(old)
        if new is None:
            print(f"  !! {d['name']}: {old!r} is on a CDU loop but has no RESKU "
                  f"entry — left as-is (still unbuildable)")
            skipped["unmapped"] += 1
            continue

        d["model_name"] = new
        d["power_draw_w"] = nameplate_power_w(DeviceType.SERVER, new)

        new_vendor = VENDOR_OF[new]
        if d.get("vendor") != new_vendor.value:
            d["vendor"] = new_vendor.value
            # Follow the vendor on the BMC port's LABEL only — index untouched, so
            # the management edge that terminates there stays pointed at a real port.
            bmc_name = BMC_PORT_NAME[new_vendor]
            for itf in d.get("interfaces") or []:
                if (itf.get("name") or "").strip().lower() in _BMC_NAMES:
                    itf["name"] = bmc_name
                    break
            moved[f"{old.split()[0]}  ->  {new_vendor.value}"] += 1

        done[f"{old}  ->  {new}"] += 1

    if not dry_run and done:
        p.write_text(json.dumps(topo, indent=2), encoding="utf-8")

    total = sum(done.values())
    verb = "Would re-SKU" if dry_run else "Re-SKU'd"
    print(f"\n{verb} {total} of {len(loop)} CDU-loop server(s) to a DLC SKU."
          f" {'(dry run)' if dry_run else (f'Wrote {p}' if done else 'No change')}\n")
    for k, c in sorted(done.items()):
        print(f"  {c:4d}  {k}")
    if moved:
        print("\n  Platform moves (no DLC SKU exists at that height from the original "
              "vendor):")
        for k, c in sorted(moved.items()):
            print(f"  {c:4d}  {k}")
    if skipped:
        print("\n  Skipped: " + ", ".join(f"{c} {k}" for k, c in sorted(skipped.items())))
    if total:
        print(f"\n  Every CDU loop now carries DLC hardware, heights are unchanged so "
              f"nothing was re-seated, and power_draw_w follows the new nameplates.")
    print()
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], dry_run="--dry-run" in sys.argv))
