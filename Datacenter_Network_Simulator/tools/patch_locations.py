"""
tools/patch_locations.py
------------------------
Bulk-assign physical location fields to every device in topology JSON files.

Usage:
    python tools/patch_locations.py                   # patch all topologies
    python tools/patch_locations.py topologies/large_4dc_enterprise.json

Location fields added to each device:
    datacenter       e.g. "DC1"
    datacenter_city  e.g. "Chicago"
    rack_row         int  e.g. 4
    rack_num         int  e.g. 12
    rack_unit        int  e.g. 14  (1-U slot within the rack)

Rules are topology-aware:
  • large_4dc_enterprise       → DC1-DC4 detected from name prefix
  • large_datacenter_3tier     → single DC, row by tier
  • large_datacenter_spine_leaf→ single DC, row by role
  • large_enterprise_wan       → HQ / SiteX detected from name prefix
  • large_hyperscale_pod       → single DC, row by pod role
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path


# ── helpers ───────────────────────────────────────────────────────────────────

class _RackPacker:
    """Assigns (row, rack, unit) slots sequentially, filling a rack before
    moving to the next, filling a row before moving to the next."""

    def __init__(self, rack_units: int = 42, racks_per_row: int = 10,
                 start_row: int = 1):
        self.rack_units    = rack_units
        self.racks_per_row = racks_per_row
        self._unit = 1
        self._rack = 1
        self._row  = start_row

    def next(self) -> tuple[int, int, int]:
        row, rack, unit = self._row, self._rack, self._unit
        self._unit += 1
        if self._unit > self.rack_units:
            self._unit = 1
            self._rack += 1
            if self._rack > self.racks_per_row:
                self._rack = 1
                self._row  += 1
        return row, rack, unit

    def reset(self):
        self._unit = self._rack = self._row = 1


def _prefix(name: str) -> str:
    m = re.match(r'^([A-Za-z0-9]+)-', name)
    return m.group(1).upper() if m else ""


def _apply_location(dev: dict, datacenter: str, city: str,
                    row: int, rack: int, unit: int, floor: str = ""):
    dev["datacenter"]      = datacenter
    dev["datacenter_city"] = city
    # Derive floor from row band (rows 1-4 → Floor 1, 5-8 → Floor 2, etc.)
    # unless an explicit floor is provided.
    dev["floor"]           = floor if floor else str((row - 1) // 4 + 1)
    dev["rack_row"]        = row
    dev["rack_num"]        = rack
    dev["rack_unit"]       = unit


# ── per-topology rules ────────────────────────────────────────────────────────

def _patch_4dc_enterprise(nodes: list):
    DC_CITIES = {
        "DC1": ("DC1", "New York"),
        "DC2": ("DC2", "Chicago"),
        "DC3": ("DC3", "Dallas"),
        "DC4": ("DC4", "Los Angeles"),
    }
    packers: dict[str, _RackPacker] = {k: _RackPacker() for k in DC_CITIES}

    for node in nodes:
        dev  = node["device"]
        pfx  = _prefix(dev["name"])
        dc, city = DC_CITIES.get(pfx, ("DC1", "New York"))
        packer   = packers.get(pfx, packers["DC1"])
        row, rack, unit = packer.next()
        _apply_location(dev, dc, city, row, rack, unit)


def _role_priority(name: str) -> int:
    """Lower = higher in rack (row 1 = core layer)."""
    n = name.upper()
    if any(x in n for x in ("BORDER", "CORE", "SPINE", "CLUSTER", "GATEWAY", "GW", "FW", "LB")):
        return 0
    if any(x in n for x in ("AGG", "DIST", "LEAF", "FABRIC", "POD")):
        return 1
    if any(x in n for x in ("ACCESS", "TOR", "MGMT", "OOB")):
        return 2
    return 3  # servers / endpoints


def _patch_single_dc(nodes: list, datacenter: str, city: str):
    sorted_nodes = sorted(nodes, key=lambda n: _role_priority(n["device"]["name"]))
    # Each tier (priority 0-3) starts in its own row band so slots never overlap.
    #   priority 0 (core/spine)   → rows 1-2
    #   priority 1 (agg/leaf)     → rows 3-4
    #   priority 2 (access/TOR)   → rows 5-6
    #   priority 3 (servers)      → rows 7+
    _START_ROW = {0: 1, 1: 3, 2: 5, 3: 7}
    packers: dict[int, _RackPacker] = {}
    for node in sorted_nodes:
        dev      = node["device"]
        priority = _role_priority(dev["name"])
        if priority not in packers:
            packers[priority] = _RackPacker(
                rack_units=42, racks_per_row=8,
                start_row=_START_ROW.get(priority, 1))
        row, rack, unit = packers[priority].next()
        _apply_location(dev, datacenter, city, row, rack, unit)


def _patch_enterprise_wan(nodes: list):
    SITE_MAP = {
        "HQ":    ("HQ",     "New York"),
        "SITEA": ("Site-A", "Chicago"),
        "SITEB": ("Site-B", "Dallas"),
        "SITEC": ("Site-C", "Los Angeles"),
        "ISP1":  ("ISP1",   "New York"),
        "ISP2":  ("ISP2",   "Chicago"),
    }
    packers: dict[str, _RackPacker] = {}

    for node in nodes:
        dev  = node["device"]
        raw  = _prefix(dev["name"])
        # normalise SiteA → SITEA
        key  = re.sub(r'SITE([A-Z])', r'SITE\1', raw.upper())
        dc, city = SITE_MAP.get(key, ("HQ", "New York"))
        if dc not in packers:
            packers[dc] = _RackPacker()
        row, rack, unit = packers[dc].next()
        _apply_location(dev, dc, city, row, rack, unit)


def _patch_dual_hyperscale(nodes: list):
    """Two DCs detected by datacenter field already set; apply rack positions per DC."""
    dc_configs = {
        "DC1": ("DC1", "Seattle"),
        "DC2": ("DC2", "Portland"),
    }
    dc_nodes: dict[str, list] = {"DC1": [], "DC2": []}
    for node in nodes:
        dc = node["device"].get("datacenter", "DC1")
        dc_nodes.setdefault(dc, []).append(node)

    for dc_key, dc_node_list in dc_nodes.items():
        dc_name, city = dc_configs.get(dc_key, (dc_key, "Unknown"))
        _patch_single_dc(dc_node_list, dc_name, city)


# ── dispatch ──────────────────────────────────────────────────────────────────

_TOPOLOGY_RULES = {
    "large_4dc_enterprise":          ("4dc",    None,         None),
    "large_datacenter_3tier":        ("single", "DC1",        "Chicago"),
    "large_datacenter_spine_leaf":   ("single", "DC1",        "Chicago"),
    "large_enterprise_wan":          ("wan",    None,         None),
    "large_hyperscale_pod":          ("single", "DC1",        "Seattle"),
    "dual_hyperscale_pod":           ("dual",   None,         None),
}


def patch_file(path: Path) -> int:
    stem = path.stem
    rule, dc, city = _TOPOLOGY_RULES.get(stem, ("single", "DC1", "Chicago"))

    data  = json.loads(path.read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])

    if rule == "4dc":
        _patch_4dc_enterprise(nodes)
    elif rule == "wan":
        _patch_enterprise_wan(nodes)
    elif rule == "dual":
        _patch_dual_hyperscale(nodes)
    else:
        _patch_single_dc(nodes, dc, city)

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  patched {len(nodes):>4} devices  ->  {path.name}")
    return len(nodes)


def main():
    root = Path(__file__).resolve().parent.parent

    if len(sys.argv) > 1:
        targets = [Path(a) for a in sys.argv[1:]]
    else:
        targets = sorted((root / "topologies").glob("*.json"))

    total = 0
    for p in targets:
        if not p.exists():
            print(f"  WARNING: {p} not found, skipping.")
            continue
        total += patch_file(p)

    print(f"\nDone — {total} devices patched across {len(targets)} file(s).")
    print("Re-generate the SNMP dataset in the simulator to apply the changes.")


if __name__ == "__main__":
    main()