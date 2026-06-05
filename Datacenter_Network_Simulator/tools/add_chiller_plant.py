"""Topology augmentation: add a metered chiller plant per datacenter.

In a chilled-water cooling system the chiller plant (compressors + condenser-
water pumps + cooling-tower fans) is the dominant electrical consumer; the CRAH
units in the data hall are just the air-side fans. This adds, per DC:

  * 1 Plant panel (rpp, passive)           fed from the generator (house power)
  * 2 chillers (chiller, passive load)     fed from the Plant panel
  * 1 chiller-plant EV2 meter              on the Plant panel  (scope = cooling)

The facility meter on the generator already sums everything downstream, so the
new chiller load flows into it automatically. Run tools/populate_power_draw.py
afterwards to (re)assign watts. Re-runnable: skips if already added.
"""
import json
import uuid
import copy
import random
from pathlib import Path

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"

# (generator id, oob switch id, free oob port, mgmt ip, base x/y, floor)
DCS = {
    "DC1": dict(gen="08a89a19", oob="e69c9a56", port=20, ip="192.168.4.200", x=1000, y=2600, floor="1"),
    "DC2": dict(gen="4b0671f3", oob="09e66792", port=24, ip="192.168.4.201", x=2640, y=2600, floor="2"),
}

def main():
    d = json.loads(TOPO.read_text())
    nodes, edges = d["nodes"], d["edges"]
    byname = {n["device"]["name"]: n for n in nodes}

    if any(n["device"]["name"].startswith("EV2-CHILLER-") for n in nodes):
        print("Chiller plant already present — nothing to do.")
        return

    used_ids = {n["id"] for n in nodes}
    def new_id():
        while True:
            i = uuid.uuid4().hex[:8]
            if i not in used_ids:
                used_ids.add(i); return i

    meter_tmpl = copy.deepcopy(byname["EV2-DC1-RPP01"]["device"])
    mech_tmpl  = copy.deepcopy(byname["RPP-DC1-A1"]["device"])

    def mk_node(tmpl, name, x, y, dc, floor, **over):
        dev = copy.deepcopy(tmpl)
        nid = new_id()
        dev["id"] = nid
        dev["name"] = name
        dev["datacenter"] = dc
        dev["floor"] = floor
        for itf in dev.get("interfaces", []):
            itf["mac_address"] = ":".join(f"{random.randint(0,255):02x}" for _ in range(6))
            itf["connected_to_device"] = None
            itf["connected_to_iface"] = None
        dev.pop("monitored_panel", None)
        dev.update(over)
        nodes.append({"id": nid, "position": {"x": x, "y": y}, "device": dev})
        return nid

    def power_edge(src, dst):
        edges.append({"src": src, "dst": dst, "src_iface": 0, "dst_iface": 0, "layer": "power"})
    def mgmt_edge(meter, oob, port):
        edges.append({"src": meter, "dst": oob, "src_iface": 0, "dst_iface": port, "layer": "management"})

    for dc, c in DCS.items():
        bx, by = c["x"], c["y"]
        plant = mk_node(mech_tmpl, f"PLANT-{dc}", bx, by, dc, c["floor"],
                        vendor="APC by Schneider Electric", model_name="Schneider PanelBoard 400A",
                        room="Chiller Plant", mgmt_ip="", ip_address="")
        power_edge(c["gen"], plant)
        for k in range(1, 3):
            ch = mk_node(mech_tmpl, f"CHILLER-{dc}-{k}", bx + 140, by - 40 + k * 80, dc, c["floor"],
                         device_type="chiller", vendor="Vertiv (Liebert)",
                         model_name="Vertiv Liebert AFC 500kW", room="Chiller Plant",
                         mgmt_ip="", ip_address="")
            power_edge(plant, ch)
        meter = mk_node(meter_tmpl, f"EV2-CHILLER-{dc}", bx - 180, by + 80, dc, c["floor"],
                        model_name="Verdigris EV2-42", room="Chiller Plant",
                        mgmt_ip=c["ip"], snmp_community=c["ip"], ip_address="",
                        monitored_panel=plant)
        power_edge(meter, plant)
        mgmt_edge(meter, c["oob"], c["port"])
        print(f"{dc}: PLANT + 2x CHILLER, meter EV2-CHILLER-{dc} @ {c['ip']}")

    TOPO.write_text(json.dumps(d, indent=2))
    print(f"nodes -> {len(nodes)}, edges -> {len(edges)}")

if __name__ == "__main__":
    main()
