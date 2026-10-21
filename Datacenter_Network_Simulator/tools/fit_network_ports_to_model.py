#!/usr/bin/env python3
"""Fit every network device's ports to what its model actually carries.

The curated topology was built before the model registry knew port fit-outs,
so each switch, router, firewall and balancer was stamped with one flat group
of Gigabit Ethernet: a Nexus 93180YC-FX exported 55 x 1 GbE where the real box
has 48 x 25 GbE SFP28 and 6 x 100 GbE QSFP28, and the DCIM read it back that
way - a leaf whose every server port and every spine uplink said 1 Gb/s.
core/device_models.py has known the truth all along (DEVICE_MODELS), and a
device ADDED through the app already takes it; only the saved topology stands
("a saved topology's interface list is generation") and so kept its guess.

This walks the registry's groups over each device's DATA ports, in order, and
rewrites each port's name and speed IN PLACE. Nothing else moves: the port's
index, its position in the list, its MAC, its counters and its role are kept,
so every edge (src_iface / dst_iface) still lands on the same port, the
dedicated mgmt port stays last and stays 1 GbE, and no re-bind or floor
re-export follows. interface_groups is rewritten to say what was assigned.

Where the saved count and the registry disagree the registry is applied as
far as it goes and the rest is reported, never invented: a box with MORE ports
than its SKU keeps the extras as they were; one with FEWER gets the registry's
leading groups and the trailing group is cut short (a Z9264F-ON saved with 64
data ports gets its 64 x 100 GbE and not the 2 x 10 GbE that were never there).

Names follow the simulator's own convention (core.device_manager.iface_name):
Cisco numbers each speed class from 0 (TwentyFiveGigE0/0.., HundredGigE0/0..);
vendors whose names carry no speed class (Dell eth1/N, Palo Alto and F5 ethN)
number straight through, so two groups never produce the same name on one box.

Idempotent: a second run changes nothing. Datasets must be regenerated after
(ifDescr / ifSpeed / ifHighSpeed come from these) and the DCIM re-imported.

Usage:
    python tools/fit_network_ports_to_model.py topologies/dual_dc_enterprise.json
    python tools/fit_network_ports_to_model.py topologies/dual_dc_enterprise.json --dry-run
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.device_manager import (  # noqa: E402
    IFACE_SPEED, DeviceType, InterfaceType, Vendor, iface_name, model_interface_groups,
)

NET_TYPES = {"switch", "oob_switch", "router", "firewall", "load_balancer"}
# Only a name that carries the speed class (Cisco TwentyFiveGigE0/N, Juniper
# xe-0/0/N, Huawei XGigabitEthernet) can restart at 0 per class, as the
# generator does; every other convention (Dell eth1/N, PAN and F5 ethN) numbers
# straight through, or two classes would produce the same name on one box.
PER_CLASS_INDEX = {Vendor.CISCO_SYSTEMS, Vendor.JUNIPER_NETWORKS, Vendor.HUAWEI}


def refit(d: dict) -> tuple[int, str | None]:
    """Rewrite one device's data ports. Returns (ports changed, note)."""
    try:
        vendor, dtype = Vendor(d.get("vendor") or ""), DeviceType(d["device_type"])
    except ValueError:
        return 0, "vendor or type not in the registry"
    groups = model_interface_groups(dtype, vendor, d.get("model_name") or "")
    if not groups:
        return 0, f"no registry entry for {d.get('model_name') or 'an unnamed model'}"
    ifs = d.get("interfaces") or []
    data = [i for i in ifs if i.get("role") != "mgmt"]
    want = sum(int(g["count"]) for g in groups)
    plan: list[tuple[dict, InterfaceType]] = []
    for g in groups:
        itype = g["iface_type"]
        if isinstance(itype, str):
            itype = InterfaceType(itype)
        for _ in range(int(g["count"])):
            if len(plan) >= len(data):
                break
            plan.append((data[len(plan)], itype))
    changed = 0
    per_class: Counter = Counter()
    running = 0
    for iface, itype in plan:
        k = per_class[itype] if vendor in PER_CLASS_INDEX else running
        name, speed = iface_name(vendor, itype, k), IFACE_SPEED[itype]
        per_class[itype] += 1
        running += 1
        if iface.get("name") != name or int(iface.get("speed") or 0) != speed:
            iface["name"], iface["speed"] = name, speed
            changed += 1
    # the groups as assigned, in registry order, then whatever was left as it was
    assigned: list[dict] = []
    for g in groups:
        itype = g["iface_type"] if not isinstance(g["iface_type"], str) else InterfaceType(g["iface_type"])
        n = per_class[itype] - sum(a["count"] for a in assigned if a["iface_type"] == itype.value)
        if n > 0:
            assigned.append({"iface_type": itype.value, "count": n})
    left = data[len(plan):]
    if left:
        extra: Counter = Counter()
        for i in left:
            label = next((t.value for t, s in IFACE_SPEED.items() if s == int(i.get("speed") or 0)),
                         InterfaceType.GIGABIT_ETHERNET.value)
            extra[label] += 1
        assigned.extend({"iface_type": t, "count": n} for t, n in extra.items())
    d["interface_groups"] = assigned
    d["interface_count"] = len(ifs)
    note = None
    if len(data) < want:
        note = f"registry lists {want} data ports, topology has {len(data)}: trailing group cut short"
    elif len(data) > want:
        note = f"registry lists {want} data ports, topology has {len(data)}: {len(data) - want} kept as they were"
    return changed, note


def main(path: str, dry_run: bool = False) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8"))
    touched = devices = 0
    notes: Counter = Counter()
    skipped: Counter = Counter()
    for node in topo.get("nodes", []):
        d = node.get("device", node)
        if d.get("device_type") not in NET_TYPES:
            continue
        n, note = refit(d)
        model = d.get("model_name") or "?"
        if n == 0 and note and "registry" in note and "lists" not in note:
            skipped[f"{model}: {note}"] += 1
            continue
        if n:
            touched += n
            devices += 1
        if note:
            notes[f"{model}: {note}"] += 1
    print(f"{'would change' if dry_run else 'changed'} {touched} ports on {devices} devices")
    for k, v in sorted(notes.items()):
        print(f"  note x{v}: {k}")
    for k, v in sorted(skipped.items()):
        print(f"  skipped x{v}: {k}")
    if not dry_run and touched:
        # LF, as the file is kept: a platform newline on Windows rewrote every
        # line of a 184k-line file as a change.
        # LF, as the file is kept: a platform newline on Windows rewrote every
        # line of a 184k-line file as a change.
        with p.open("w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(topo, indent=2) + "\n")
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else "topologies/dual_dc_enterprise.json", "--dry-run" in sys.argv))
