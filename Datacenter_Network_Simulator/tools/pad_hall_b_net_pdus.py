#!/usr/bin/env python3
"""Mirror Hall A's network-row PDU layout into Hall B, per DC.

promote_hall_b_pod.py gave Hall B one A/B network-PDU pair powering all its
spines + OOB. Hall A distributes its network gear across more pairs (DC1: 3 pairs
= SP1,2 | SP3,4 | all OOB; DC2: 2 pairs = all spines | all OOB). This replaces
Hall B's single pair with a clone of Hall A's exact pair count + load grouping,
so the two halls' network racks match — fed by Hall B's own RPP-A2/B2.

Device mapping is positional: Hall A spine[i] -> Hall B spine[i], Hall A
access-OOB[i] -> Hall B access-OOB[i]. Hall A's OOB-CORE has no Hall B twin (one
per DC) so it is skipped.

Idempotent enough to re-run after a fresh promote. Run export_dcim_floorplan.py
afterwards.

Usage:
    python tools/pad_hall_b_net_pdus.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import hall_geometry as geo  # noqa: E402


def next_ip(seed, used):
    if not seed:
        return ""
    a, b, c, d = (int(x) for x in seed.split("."))
    for _ in range(65535):
        d += 1
        if d > 254:
            d = 1; c += 1
        ip = f"{a}.{b}.{c}.{d}"
        if ip not in used:
            used.add(ip); return ip
    return ""


def promote_dc(topo, dc, log):
    nodes = topo["nodes"]
    edges = topo["edges"]
    nm = {n["id"]: n["device"].get("name") for n in nodes}
    id_of = {v: k for k, v in nm.items()}

    def sel(room, dtype, pred=None, row=None):
        out = []
        for n in nodes:
            d = n["device"]
            if d["datacenter"] != dc or d.get("room") != room or d["device_type"] != dtype:
                continue
            if row is not None and (d.get("rack_row") or 0) != row:
                continue
            if pred and not pred(d):
                continue
            out.append(n)
        return out

    HA, HB = "Server Hall A", "Server Hall B"
    ha_net_pdus = sorted(sel(HA, "pdu", row=1), key=lambda n: n["device"].get("name"))
    if not ha_net_pdus:
        log(f"{dc}: no Hall A network PDUs — skipped"); return
    hb_net_pdus = sel(HB, "pdu", row=1)

    # Positional device map: Hall A network gear -> Hall B twin.
    ha_sp = sorted(sel(HA, "switch", lambda d: "-SP" in (d.get("name") or "")), key=lambda n: n["device"]["name"])
    hb_sp = sorted(sel(HB, "switch", lambda d: "-SP" in (d.get("name") or "")), key=lambda n: n["device"]["name"])
    ha_ob = sorted(sel(HA, "oob_switch", lambda d: "OOB-SW" in (d.get("name") or "")), key=lambda n: n["device"]["name"])
    hb_ob = sorted(sel(HB, "oob_switch", lambda d: "OOB-SW" in (d.get("name") or "")), key=lambda n: n["device"]["name"])
    dmap = {}
    for a, b in zip(ha_sp, hb_sp):
        dmap[a["id"]] = b["id"]
    for a, b in zip(ha_ob, hb_ob):
        dmap[a["id"]] = b["id"]

    hb_rpp = {}
    for n in sel(HB, "rpp"):
        s = "A" if "-A" in (n["device"].get("name") or "") else "B"
        hb_rpp[s] = n["id"]
    if "A" not in hb_rpp or "B" not in hb_rpp:
        log(f"{dc}: Hall B missing RPP-A/B — skipped"); return

    used_ids = {n["id"] for n in nodes}
    used_prod = {n["device"].get("ip_address") for n in nodes if n["device"].get("ip_address")}
    used_mgmt = {n["device"].get("mgmt_ip") for n in nodes if n["device"].get("mgmt_ip")}

    # Drop Hall B's existing network PDUs + every power edge touching them.
    drop = {n["id"] for n in hb_net_pdus}
    nodes[:] = [n for n in nodes if n["id"] not in drop]
    edges[:] = [e for e in edges if e["src"] not in drop and e["dst"] not in drop]

    # Hall B's floor (clones must NOT inherit Hall A's floor, or they split into a
    # phantom rack_id and mis-render).
    hb_floor = next((n["device"].get("floor") for n in nodes
                     if n["device"]["datacenter"] == dc
                     and n["device"].get("room") == HB
                     and n["device"]["device_type"] != "pdu"), None)

    # Clone each Hall A network PDU into Hall B with remapped feed + loads.
    added = 0
    for ha_pdu in ha_net_pdus:
        ha_id = ha_pdu["id"]
        side = "A" if (ha_pdu["device"].get("name") or "").endswith("-A") else "B"
        # loads this Hall A PDU powers, mapped to Hall B twins (skip OOB-CORE)
        loads = [dmap[e["dst"]] for e in edges
                 if e["src"] == ha_id and e.get("layer") == "power" and e["dst"] in dmap]
        if not loads:
            continue
        new = copy.deepcopy(ha_pdu)
        nid = uuid.uuid4().hex[:8]
        while nid in used_ids:
            nid = uuid.uuid4().hex[:8]
        used_ids.add(nid)
        dv = new["device"]
        rack = new["device"].get("rack_num") or 1
        new["id"] = nid; dv["id"] = nid
        dv["name"] = (ha_pdu["device"]["name"] or "").replace("-SHA-", "-SHB-")
        dv["room"] = HB
        if hb_floor is not None:
            dv["floor"] = hb_floor
        dv["floor_x"] = round(geo.rack_x(rack), 4)
        dv["floor_y"] = round(geo.row_y(1), 4)
        if dv.get("ip_address"):
            dv["ip_address"] = next_ip(ha_pdu["device"].get("ip_address") or "", used_prod)
        if dv.get("mgmt_ip"):
            dv["mgmt_ip"] = next_ip(ha_pdu["device"].get("mgmt_ip") or "", used_mgmt)
            dv["snmp_community"] = dv["mgmt_ip"]
        nodes.append(new)
        # feed from Hall B RPP of the same side; power each mapped load
        edges.append({"src": hb_rpp[side], "dst": nid, "src_iface": 0, "dst_iface": 0,
                      "broken": False, "layer": "power"})
        for lid in loads:
            edges.append({"src": nid, "dst": lid, "src_iface": 0, "dst_iface": 0,
                          "broken": False, "layer": "power"})
        added += 1

    log(f"{dc}: Hall B network PDUs {len(hb_net_pdus)} -> {added} (mirrors Hall A's {len(ha_net_pdus)})")


def main(path):
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    msgs = []
    for dc in sorted({n["device"]["datacenter"] for n in topo["nodes"]}):
        promote_dc(topo, dc, lambda m: msgs.append(m))
    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print("\n".join(msgs))
    print(f"\nWrote {p}\nNext: python tools/export_dcim_floorplan.py {path} "
          f"{path.replace('.json','_floorplan.json')}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
