"""Set every device's ``datacenter_city`` to its DC's majority city (doc 32 §9.1, C2).

The shipped topology had ten DC2 rack sensors labelled "Chicago" (DC2 is New
York). They published ``sysLocation … Chicago … DC2``, and the store's weather
derivation took the first device per DC, so DC2 weather was right only by
insertion-order accident.

Dry run by default; ``--write`` saves. Idempotent: a second run changes
nothing. Only the ``datacenter_city`` field of dissenting devices is touched,
and the write refuses to proceed unless the file round-trips byte-for-byte
through ``json`` first, so unrelated content cannot be reformatted.

After writing: SNMP Stop -> Regenerate -> Start (sysLocation is part of the
dataset fingerprint), then let the platform re-import or rediscover the ten
sensors' location.

Usage:
    python tools/fix_dc_city_labels.py [topologies/dual_dc_enterprise.json] [--write]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "topologies" / "dual_dc_enterprise.json"


def _dump(doc) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def majority_cities(nodes) -> dict:
    """{dc: city} by majority; ties go to the city seen first."""
    votes: dict = {}
    first: dict = {}
    for n in nodes:
        dv = n.get("device") or {}
        dc, city = dv.get("datacenter"), dv.get("datacenter_city")
        if not dc or not city:
            continue
        votes.setdefault(dc, Counter())[city] += 1
        seen = first.setdefault(dc, [])
        if city not in seen:
            seen.append(city)
    return {dc: max(c, key=lambda k: (c[k], -first[dc].index(k))) for dc, c in votes.items()}


def plan(doc) -> list:
    """[(node_index, name, dc, old_city, new_city)] for each dissenter."""
    nodes = doc.get("nodes") or []
    maj = majority_cities(nodes)
    out = []
    for i, n in enumerate(nodes):
        dv = n.get("device") or {}
        dc, city = dv.get("datacenter"), dv.get("datacenter_city")
        if dc in maj and city and city != maj[dc]:
            out.append((i, dv.get("name"), dc, city, maj[dc]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default=str(DEFAULT))
    ap.add_argument("--write", action="store_true", help="save the change (default: dry run)")
    a = ap.parse_args(argv)
    path = Path(a.path)
    data = path.read_bytes().decode("utf-8")
    eol = "\r\n" if "\r\n" in data else "\n"   # keep the working copy's line endings
    raw = data.replace("\r\n", "\n")
    doc = json.loads(raw)
    changes = plan(doc)
    for _i, name, dc, old, new in changes:
        print(f"  {name:<28} {dc}: {old} -> {new}")
    print(f"{len(changes)} device(s) disagree with their DC majority.")
    if not changes:
        return 0
    if not a.write:
        print("Dry run. Re-run with --write to save.")
        return 0
    if _dump(doc) != raw:
        print("REFUSING: the file does not round-trip through json byte-for-byte, so "
              "writing it would reformat unrelated content.", file=sys.stderr)
        return 2
    for i, _name, _dc, _old, new in changes:
        doc["nodes"][i]["device"]["datacenter_city"] = new
    path.write_bytes(_dump(doc).replace("\n", eol).encode("utf-8"))
    print(f"Wrote {path}. Next: SNMP Stop -> Regenerate -> Start.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
