"""Wire the condenser-water plant sensors into the COOLING layer.

CWS / CWR / CTB (condenser-water supply/return temp, cooling-tower basin temp) had
only management + power links, so they never appeared when the canvas is switched to
the cooling layer — even though they instrument the condenser loop. The chilled-water
sensors (CHWS/CHWR/FLOW) already carry cooling edges; this mirrors that so the
condenser sensors show up alongside the loop they monitor.

Placement matches where the transmitters physically sit:
  CWS, CWR  -> each chiller  (condenser supply/return headers at the chiller condenser)
  CTB       -> each tower    (basin temperature)

Purely a visualisation wiring — the cooling MODEL reads plant unit states, not these
sensor edges, so nothing about the physics changes. Backs up to <file>.precwsens.bak.
"""
import json
import sys

TOPO = "topologies/dual_dc_enterprise.json"


def _dc(name: str) -> str:
    return "DC1" if "-DC1-" in name else "DC2" if "-DC2-" in name else "?"


def main(path: str = TOPO) -> None:
    with open(path, encoding="utf-8") as f:
        topo = json.load(f)

    name_id = {n["device"]["name"]: n["id"] for n in topo["nodes"]}
    id_name = {v: k for k, v in name_id.items()}

    def names_of(prefix_fn, dc):
        return [nm for nm in name_id
                if _dc(nm) == dc and prefix_fn(nm)]

    # existing cooling edges as a set of frozenset pairs, to avoid duplicates
    existing = {frozenset((e["src"], e["dst"]))
                for e in topo["edges"] if e.get("layer") == "cooling"}

    added = []
    for dc in ("DC1", "DC2"):
        chillers = names_of(lambda n: n.startswith("CHL"), dc)
        towers = names_of(lambda n: n[:2] == "CT" and len(n) > 2 and n[2].isdigit(), dc)
        sensor_targets = {
            f"CWS-{dc}-CP": chillers,   # condenser supply temp — at the chillers
            f"CWR-{dc}-CP": chillers,   # condenser return temp — at the chillers
            f"CTB-{dc}-CP": towers,     # cooling-tower basin temp — at the towers
        }
        for sensor, targets in sensor_targets.items():
            sid = name_id.get(sensor)
            if sid is None:
                continue
            for t in targets:
                tid = name_id.get(t)
                if tid is None or frozenset((sid, tid)) in existing:
                    continue
                topo["edges"].append({
                    "src": sid, "dst": tid,
                    "src_iface": None, "dst_iface": None,
                    "layer": "cooling",
                })
                existing.add(frozenset((sid, tid)))
                added.append((sensor, t))

    if not added:
        print("Condenser sensors already wired into the cooling layer — nothing to do.")
        return

    with open(path + ".precwsens.bak", "w", encoding="utf-8") as f:
        json.dump(json.load(open(path, encoding="utf-8")), f)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topo, f)

    print(f"Added {len(added)} cooling edge(s):")
    for s, t in added:
        print(f"  {s} <-> {t}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else TOPO)
