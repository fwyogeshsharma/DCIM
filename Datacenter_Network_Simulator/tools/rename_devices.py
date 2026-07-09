#!/usr/bin/env python3
"""Rename every device to one consistent scheme — TYPE+index FIRST, then location.

  Rack rooms (Server Hall A/B, Network Room):
      <CODE>-DC<n>-<ROOM>-R<row>-<rack:02d>
  Facility rooms (Central Plant, Roof, UPS/Generator/Mechanical Room):
      <CODE>-DC<n>-<ROOM>

<CODE> = a short type token (+ an A/B side or a numeric index for uniqueness),
e.g. SRV05, SP1, PDUA, RPPA, CHWS, CWP1. Because CODE leads, every role token is
the FIRST '-'-segment; the runtime parsers were updated to read it there:
  * spine 'SP' / leaf 'LF'            (fleet _is_spine/_is_leaf: leading segment)
  * pump 'CHWP' / 'CWP'               (device_state_store CW-pump loop: 'CWP' in name)
  * sensor 'CHWS'/'CHWR'/'CWS'/'CWR'  (fleet CRAH CHW header: leading segment)
  * RPP 'RPPA'/'RPPB'                 (fleet _hall_infra A/B-side: leading segment)
The mechanical RPP that powers CRAHs is found by room (Mechanical Room), not name.

Room codes: HA HB CP NR UR GR MR RF.  Idempotent-ish (re-running re-derives).

Usage:
    python tools/rename_devices.py topologies/dual_dc_enterprise.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOM_CODE = {
    "Server Hall A": "HA", "Server Hall B": "HB", "Central Plant": "CP",
    "Network Room": "NR", "UPS Room": "UR", "Generator Room": "GR",
    "Mechanical Room": "MR", "Roof": "RF",
}
RACK_ROOMS = {"Server Hall A", "Server Hall B", "Network Room"}


def side(up: str) -> str:
    m = re.search(r"-([AB])\d*$", up) or re.search(r"([AB])\d*$", up)
    return m.group(1) if m else ""


def code_and_fixed(dv, old: str):
    """(token, fixed) — fixed=True means the token is unique on its own (a side
    or role) and needs no numeric index unless it collides."""
    t = dv["device_type"]
    up = (old or "").upper()
    if t == "server":        return "SRV", False
    if t == "switch":
        if "-SP" in up:      return "SP", False
        if "-LF" in up:      return "LF", False
        if "CORE" in up:     return "COR", False
        return "SW", False
    if t == "oob_switch":    return ("OOBC", False) if "OOB-CORE" in up else ("OOB", False)
    if t == "pdu":
        s = side(up);        return ("PDU" + s, bool(s))
    if t == "rpp":
        s = side(up);        return ("RPP" + s, bool(s))
    if t == "energy_monitor": return "EV2", False
    if t == "sensor":
        for tok in ("CHWS", "CHWR", "CWS", "CWR", "FLOW", "CTB"):
            if tok in up:    return tok, True
        if "LEAK" in up:     return "LEAK", False
        return "SEN", False
    if t == "cdu":           return "CDU", False
    if t == "pump":          return ("CHWP" if "CHWP" in up else "CWP"), False
    if t == "valve":         return ("VCHW" if "CHW" in up else "VCW"), True
    if t == "generator":     return "GEN", False
    if t == "ups":
        s = side(up);        return ("UPS" + s, bool(s))
    if t == "chiller":       return "CHL", False
    if t == "cooling_tower": return "CT", False
    if t == "firewall":      return "FW", False
    if t == "router":        return "RTR", False
    if t == "load_balancer": return "LB", False
    if t == "crah":          return "CRAH", False
    return t[:3].upper(), False


def main(path: str) -> int:
    p = Path(path)
    topo = json.loads(p.read_text(encoding="utf-8-sig"))
    nodes = topo["nodes"]

    # Group devices by (scope, token) to assign per-group indices deterministically.
    groups = defaultdict(list)
    meta = {}
    for n in nodes:
        dv = n["device"]
        dc = dv["datacenter"]; room = dv.get("room") or ""
        rc = ROOM_CODE.get(room, re.sub(r"[^A-Z]", "", room.upper())[:2] or "XX")
        tok, fixed = code_and_fixed(dv, dv.get("name") or "")
        if room in RACK_ROOMS:
            loc = f"{dc}-{rc}-R{dv.get('rack_row') or 0}-{int(dv.get('rack_num') or 0):02d}"
            scope = (dc, room, dv.get("rack_row"), dv.get("rack_num"))
        else:
            loc = f"{dc}-{rc}"
            scope = (dc, room)
        key = (scope, tok)
        groups[key].append(n)
        meta[n["id"]] = (loc, tok, fixed)

    # Assign names. Fixed single-member groups get no number; everything else is
    # numbered, zero-padded to the group's digit width.
    used = set()
    changed = 0
    for key, members in groups.items():
        _scope, tok = key
        width = len(str(len(members)))
        for i, n in enumerate(members, start=1):
            loc, tok, fixed = meta[n["id"]]
            code = tok if (fixed and len(members) == 1) else f"{tok}{i:0{width}d}"
            nm = f"{code}-{loc}"
            while nm in used:                       # last-resort collision guard
                i += 1; code = f"{tok}{i:0{width}d}"; nm = f"{code}-{loc}"
            used.add(nm)
            if n["device"].get("name") != nm:
                changed += 1
            n["device"]["name"] = nm

    p.write_text(json.dumps(topo, indent=2), encoding="utf-8")
    print(f"Renamed {changed}/{len(nodes)} devices. Wrote {p}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    sys.exit(main(sys.argv[1]))
