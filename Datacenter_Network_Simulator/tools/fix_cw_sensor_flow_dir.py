"""Correct the flow DIRECTION of the condenser-water sensor cooling edges.

These sensors proxy the condenser headers, which flow one way each:
  CWS  supply  (cooled: header -> chillers)   -> OUTWARD  (leave as-is)
  CWR  return  (hot: chillers -> header)       -> INWARD   (device -> sensor)
  CTB  basin   (collects the cooled return)    -> INWARD   (tower  -> sensor)

The cooling layer animates flow along src->dst, so a return/collection sensor must
be the DST. This swaps src/dst on the CWR/CTB edges so they read as inward flow,
matching a real header. CWS (supply) is already outward and untouched.

Backs up to <file>.precwdir.bak.
"""
import json
import sys

TOPO = "topologies/dual_dc_enterprise.json"


def main(path: str = TOPO) -> None:
    with open(path, encoding="utf-8") as f:
        topo = json.load(f)
    id_name = {n["id"]: n["device"]["name"] for n in topo["nodes"]}

    def is_inward_sensor(nm: str) -> bool:
        # return / collection instruments — flow goes INTO them
        return nm.startswith("CWR") or nm.startswith("CTB")

    swapped = []
    for e in topo["edges"]:
        if e.get("layer") != "cooling":
            continue
        a, b = id_name.get(e["src"], ""), id_name.get(e["dst"], "")
        # sensor is currently the SRC (outward) but should be the DST (inward) → swap
        if is_inward_sensor(a) and not is_inward_sensor(b):
            e["src"], e["dst"] = e["dst"], e["src"]
            e["src_iface"], e["dst_iface"] = e.get("dst_iface"), e.get("src_iface")
            swapped.append((b, a))   # now device -> sensor

    if not swapped:
        print("No CWR/CTB cooling edges needed a direction fix.")
        return

    with open(path + ".precwdir.bak", "w", encoding="utf-8") as f:
        json.dump(json.load(open(path, encoding="utf-8")), f)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topo, f)

    print(f"Reversed {len(swapped)} edge(s) to inward flow (device -> sensor):")
    for dev, sens in swapped:
        print(f"  {dev} -> {sens}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else TOPO)
