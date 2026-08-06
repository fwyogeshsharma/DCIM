"""Remove the cross-DC out-of-band management interconnect (OOBR-DC1 <-> OOBR-DC2).

These two edges were the only links spanning the two datacenters — the OOB management
WAN between the sites. This drops them so each DC's OOB plane is self-contained.

Backs up the topology to <file>.preoobwan.bak, then rewrites it in place.
"""
import json
import sys

TOPO = "topologies/dual_dc_enterprise.json"


def _dc(name: str) -> str:
    return "DC1" if "-DC1-" in name else "DC2" if "-DC2-" in name else "?"


def main(path: str = TOPO) -> None:
    with open(path, encoding="utf-8") as f:
        topo = json.load(f)

    id_name = {n["id"]: n["device"]["name"] for n in topo["nodes"]}

    def is_oobr_cross_dc(e: dict) -> bool:
        a, b = id_name.get(e["src"], ""), id_name.get(e["dst"], "")
        return (a.startswith("OOBR") and b.startswith("OOBR")
                and _dc(a) in ("DC1", "DC2") and _dc(b) in ("DC1", "DC2")
                and _dc(a) != _dc(b))

    before = len(topo["edges"])
    removed = [e for e in topo["edges"] if is_oobr_cross_dc(e)]
    topo["edges"] = [e for e in topo["edges"] if not is_oobr_cross_dc(e)]

    if not removed:
        print("No cross-DC OOBR links found — nothing to remove.")
        return

    with open(path + ".preoobwan.bak", "w", encoding="utf-8") as f:
        json.dump(json.load(open(path, encoding="utf-8")), f)  # verbatim backup
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topo, f)

    print(f"Removed {len(removed)} cross-DC OOB WAN link(s) "
          f"({before} -> {len(topo['edges'])} edges):")
    for e in removed:
        print(f"  {id_name.get(e['src'])} <-> {id_name.get(e['dst'])} "
              f"(layer={e.get('layer')})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else TOPO)
