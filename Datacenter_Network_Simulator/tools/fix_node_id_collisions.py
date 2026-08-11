#!/usr/bin/env python3
"""Repair node-identity damage in a topology JSON: id collisions and split ids.

THE INVARIANT. A node is ``{"id": ..., "device": {"id": ..., ...}}`` and those two
ids are the SAME string for every node in a healthy file. Edges reference that id.

WHO IS ACTUALLY HURT BY BREAKING IT: not the app. TopologyEngine.from_dict ignores
the wrapper id entirely — it builds the Device from the ``device`` object and keys
the graph by ``device.id`` — and to_dict then writes that key back out, so the
running simulator is correct and a save from it HEALS the file. A damaged file is
therefore always the work of a script that appended a node by hand.

Everything that reads the JSON *without* the app pays instead, and every one of
them keys by the wrapper id, because that is the field the edges are supposed to
use:

  * DUPLICATE node id — a {id: node} index silently keeps only the last entry, so
    one device disappears from that consumer's view: the power sizer misses its
    load, an --from-file export omits it, the floor plan never draws it.
  * SPLIT id (node.id != device.id) — edges written against device.id name a node
    the index does not have. They read as dangling; the cords and links they
    describe are dropped from the walk.

Both arrived together here from one bad clone. A new sensor for Server Hall A rack
R2-03 in DC2 was appended carrying the R2-02 sensor's wrapper id and a fresh
device.id, with its two edges written against the device.id. In the file that made
660 nodes hold 659 unique ids, hid SEN1-DC2-HA-R2-02 from every file consumer, and
left the new sensor's own power cord dangling — while the live estate showed both
sensors, correctly cabled, the whole time. tools/fix_power_skus.py did not merely
misread it: indexing straight into the node map, it raised KeyError and could not
run against the estate at all.

THE REPAIR is always the same and needs no guesswork: give the node its own
device.id as its wrapper id. That id is already unique (it is what the clone
generated), it is what the node's own edges reference, it is what the app itself
would write on its next save, and handing it back frees the colliding id for its
rightful owner — whose edges then find it again.

Refuses to invent an id. A node with a duplicate wrapper id and NO distinct
device.id is reported, not patched: there is nothing to tell which of the two
devices the surviving edges belong to, and a guess would silently re-cable the
estate.

Usage:  python -m tools.fix_node_id_collisions [path/to/topology.json] [--dry-run]
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

DEFAULT = Path("topologies/dual_dc_enterprise.json")


def audit(data: dict) -> dict:
    """{'split': [...], 'dupes': {...}, 'unfixable': [...]} for a topology dict."""
    nodes = data.get("nodes", [])
    counts = collections.Counter(n.get("id") for n in nodes)
    dupes = {k: v for k, v in counts.items() if v > 1}
    split, unfixable = [], []
    for n in nodes:
        did = (n.get("device") or {}).get("id")
        if did and did != n.get("id"):
            split.append(n)
        elif n.get("id") in dupes and not did:
            unfixable.append(n)
    return {"split": split, "dupes": dupes, "unfixable": unfixable}


def repair(data: dict) -> list[dict]:
    """Rewrite each split node's wrapper id to its device.id. Mutates *data*;
    returns change records. Edges are NOT touched — the whole point is that they
    already name the right id and were only ever pointing at a node that had
    filed itself under someone else's."""
    changes = []
    for n in audit(data)["split"]:
        old, new = n["id"], n["device"]["id"]
        n["id"] = new
        changes.append({"name": n["device"].get("name"), "from": old, "to": new})
    return changes


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in argv[1:]
    path = Path(args[0]) if args else DEFAULT
    if not path.exists():
        print(f"topology not found: {path}", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))

    before = audit(data)
    node_ids = {n["id"] for n in data.get("nodes", [])}
    orphan_edges = [e for e in data.get("edges", [])
                    if e.get("src") not in node_ids or e.get("dst") not in node_ids]
    print(f"{len(data.get('nodes', []))} nodes, "
          f"{len(node_ids)} unique ids, {len(orphan_edges)} edge(s) naming no node")
    for n in before["split"]:
        print(f"  split id: {n['device'].get('name')} filed under {n['id']!r} "
              f"but is {n['device']['id']!r}")
    for nid, cnt in sorted(before["dupes"].items()):
        who = [x["device"].get("name") for x in data["nodes"] if x["id"] == nid]
        print(f"  collision: {nid!r} claimed by {cnt} nodes — {', '.join(who)}")
    for n in before["unfixable"]:
        print(f"  ! {n['device'].get('name')} shares id {n['id']!r} and carries no "
              f"device.id of its own — cannot be repaired automatically",
              file=sys.stderr)

    changes = repair(data)
    for c in changes:
        print(f"  ~ {c['name']}: node id {c['from']!r} -> {c['to']!r}")

    after = audit(data)
    node_ids = {n["id"] for n in data.get("nodes", [])}
    still = [e for e in data.get("edges", [])
             if e.get("src") not in node_ids or e.get("dst") not in node_ids]
    print(f"\nafter: {len(node_ids)} unique ids, {len(after['dupes'])} collision(s), "
          f"{len(still)} dangling edge(s)")

    if changes and not dry:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"written to {path}")
    elif changes:
        print("--dry-run: not written")
    else:
        print("(nothing to repair)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
