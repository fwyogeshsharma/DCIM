#!/usr/bin/env python3
"""Give every server a realistic port fit-out derived from its VENDOR and MODEL.

Servers had a flat 2 ports each and (mostly) no model. Real rack servers ship with
a vendor/model-specific set of data NICs (onboard LOM / OCP) PLUS a dedicated
baseboard-management-controller port (iDRAC / iLO / XCC / IPMI / IMM / CIMC). This:

  1. Assigns each server a real model from its vendor's line-up (round-robin;
     existing valid models are kept).
  2. Rebuilds its interfaces from that model's profile — N data NICs of the model's
     speed + 1 dedicated BMC port (1 GbE) — and sets interface_count / groups.
  3. Re-wires the edges: the data uplink (production -> ToR) lands on data NIC 0;
     the management edge (-> OOB) lands on the BMC port. Extra data NICs stay open
     (spare / future dual-homing).

Profiles follow the common default fit-out: quad-1G-LOM boxes carry 4x1G, modern
dual-port-OCP boxes carry 2x25G (Supermicro/blades 2x10G). Every server also gets
its vendor's dedicated BMC port. Not modelled: add-in PCIe NICs (BTO-specific).

No positions change -> no layout_canvas / floorplan re-export. Idempotent-ish:
re-running reassigns the same model (deterministic) and rebuilds identically.

Usage:
    python tools/set_server_ports.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

G1 = "Gigabit Ethernet (1 Gbps)"
G10 = "10 Gigabit Ethernet (10 Gbps)"
G25 = "25 Gigabit Ethernet (25 Gbps)"
SPEED = {G1: 1_000_000_000, G10: 10_000_000_000, G25: 25_000_000_000}

MODELS_BY_VENDOR = {
    "Dell Technologies": ["Dell PowerEdge R640", "Dell PowerEdge R740",
                          "Dell PowerEdge R750", "Dell PowerEdge R940", "Dell PowerEdge R7525"],
    "Hewlett Packard Enterprise": ["HPE ProLiant DL360 Gen10", "HPE ProLiant DL380 Gen10",
                                   "HPE ProLiant DL380 Gen11", "HPE ProLiant DL560 Gen10"],
    "Lenovo": ["Lenovo ThinkSystem SR630 V2", "Lenovo ThinkSystem SR650 V2",
               "Lenovo ThinkSystem SR860 V2"],
    "Supermicro": ["Supermicro SYS-120U-TNR", "Supermicro SYS-220U-TNR", "Supermicro AS-4124GS-TNR"],
    "IBM": ["IBM Power System S922", "IBM System x3850 X6", "IBM FlexSystem x240 M5"],
    "Cisco Systems": ["Cisco UCS C220 M6", "Cisco UCS C240 M6", "Cisco UCS B200 M6"],
}

# model -> (data NIC count, data NIC type). BMC (1G dedicated) is added on top.
PROFILE = {
    "Dell PowerEdge R640": (4, G1), "Dell PowerEdge R740": (4, G1),
    "Dell PowerEdge R750": (2, G25), "Dell PowerEdge R940": (4, G1), "Dell PowerEdge R7525": (2, G25),
    "HPE ProLiant DL360 Gen10": (4, G1), "HPE ProLiant DL380 Gen10": (4, G1),
    "HPE ProLiant DL380 Gen11": (2, G25), "HPE ProLiant DL560 Gen10": (4, G1),
    "Lenovo ThinkSystem SR630 V2": (2, G25), "Lenovo ThinkSystem SR650 V2": (2, G25),
    "Lenovo ThinkSystem SR860 V2": (4, G1),
    "Supermicro SYS-120U-TNR": (2, G10), "Supermicro SYS-220U-TNR": (2, G10),
    "Supermicro AS-4124GS-TNR": (2, G25),
    "IBM Power System S922": (4, G1), "IBM System x3850 X6": (4, G1),
    "IBM FlexSystem x240 M5": (2, G10),
    "Cisco UCS C220 M6": (2, G25), "Cisco UCS C240 M6": (2, G25), "Cisco UCS B200 M6": (2, G25),
}

BMC = {  # vendor -> dedicated lights-out controller port name
    "Dell Technologies": "iDRAC", "Hewlett Packard Enterprise": "iLO", "Lenovo": "XCC",
    "Supermicro": "IPMI", "IBM": "IMM", "Cisco Systems": "CIMC",
}


def _mac(sid: str, idx: int) -> str:
    h = hashlib.md5(f"{sid}:{idx}".encode()).digest()
    b = bytearray(h[:6])
    b[0] = (b[0] & 0xFE) | 0x02          # locally-administered, unicast
    return ":".join(f"{x:02x}" for x in b)


def _iface(sid: str, idx: int, name: str, itype: str) -> dict:
    return {
        "index": idx, "name": name, "speed": SPEED[itype], "oper_status": 1,
        "in_octets": 0, "out_octets": 0, "in_errors": 0, "out_errors": 0,
        "in_discards": 0, "out_discards": 0, "mac_address": _mac(sid, idx),
        "connected_to_device": None, "connected_to_iface": None,
    }


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes, edges = topo["nodes"], topo["edges"]
    typ = {n["id"]: n["device"]["device_type"] for n in nodes}

    seq: dict = defaultdict(int)   # per-vendor round-robin counter for model assignment
    dist: Counter = Counter()
    done = 0
    for n in nodes:
        if n["device"]["device_type"] != "server":
            continue
        d = n["device"]
        sid = n["id"]
        vendor = d.get("vendor", "")
        catalog = MODELS_BY_VENDOR.get(vendor)
        if not catalog:
            continue                                   # unknown vendor — leave it
        model = d.get("model_name") or ""
        if model not in PROFILE:
            model = catalog[seq[vendor] % len(catalog)]
            seq[vendor] += 1
        data_n, dtype = PROFILE[model]
        bmc = BMC.get(vendor, "BMC")

        # Rebuild interfaces: N data NICs then the dedicated BMC port.
        ifaces = [_iface(sid, i + 1, f"eth1/{i + 1}", dtype) for i in range(data_n)]
        ifaces.append(_iface(sid, data_n + 1, bmc, G1))
        d["interfaces"] = ifaces
        d["interface_count"] = len(ifaces)
        # interface_groups: aggregate by type in first-seen order.
        groups: dict = {}
        for itf in ifaces:
            t = next(k for k, v in SPEED.items() if v == itf["speed"])
            groups[t] = groups.get(t, 0) + 1
        d["interface_groups"] = [{"iface_type": t, "count": c} for t, c in groups.items()]
        d["model_name"] = model

        # Re-point edges: data uplink -> NIC 0; management (BMC) -> the BMC port
        # (list-index data_n). Facility edges (power/cooling) are left as-is.
        bmc_ix = data_n
        for e in edges:
            if sid not in (e["src"], e["dst"]):
                continue
            key = "src_iface" if e["src"] == sid else "dst_iface"
            peer = e["dst"] if e["src"] == sid else e["src"]
            if e.get("layer") == "production" and typ.get(peer) == "switch":
                e[key] = 0
            elif e.get("layer") == "management" and typ.get(peer) == "oob_switch":
                e[key] = bmc_ix

        dist[f"{model} [{data_n}x data + {bmc}]"] += 1
        done += 1

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Set realistic ports on {done} server(s). Wrote {p}\n")
    for m, c in sorted(dist.items()):
        print(f"  {c:4d}  {m}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
