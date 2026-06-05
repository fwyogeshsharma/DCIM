"""One-shot topology augmentation: add facility + cooling meters and CRAH loads
to dual_dc_enterprise.json so DCIM can compute PUE = facility / IT.

Per datacenter it adds:
  * 1 Mechanical Panel (rpp, passive)        fed from the generator
  * 4 CRAH units (crah, passive cooling load) fed from the Mechanical Panel
  * 1 cooling EV2 meter   on the mechanical panel  (sums the CRAH draw)
  * 1 facility EV2 meter   on the generator feed     (sums IT + cooling)

Meter kW is derived from downstream power_draw_w at runtime (see bacnet router),
so all meters are plain EV2-42 here — the facility meter reads higher simply
because more load flows through the generator. PUE ~1.4. Re-runnable: skips if
already added.
"""
import json
import uuid
from pathlib import Path

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"

# (generator id, oob switch id, two free oob ports, base x/y, floor)
DCS = {
    "DC1": dict(gen="08a89a19", oob="e69c9a56", ports=(18, 19), x=1000, y=2380, floor="1"),
    "DC2": dict(gen="4b0671f3", oob="09e66792", ports=(22, 23), x=2640, y=2380, floor="2"),
}

def main():
    d = json.loads(TOPO.read_text())
    nodes, edges = d["nodes"], d["edges"]
    byid = {n["id"]: n["device"] for n in nodes}
    byname = {n["device"]["name"]: n for n in nodes}

    if any(n["device"]["name"].startswith("EV2-FAC-") for n in nodes):
        print("Already augmented — nothing to do.")
        return

    used_ids = set(byid)
    def new_id():
        while True:
            i = uuid.uuid4().hex[:8]
            if i not in used_ids:
                used_ids.add(i); return i

    used_ips = {n["device"].get("mgmt_ip", "") for n in nodes}
    ip_pool = (f"192.168.4.{h}" for h in range(196, 255))
    def new_ip():
        for ip in ip_pool:
            if ip not in used_ips:
                used_ips.add(ip); return ip
        raise RuntimeError("out of mgmt IPs")

    import copy, random
    meter_tmpl = copy.deepcopy(byname["EV2-DC1-RPP01"]["device"])
    mech_tmpl  = copy.deepcopy(byname["RPP-DC1-A1"]["device"])

    def mk_node(tmpl, name, x, y, dc, floor, **over):
        dev = copy.deepcopy(tmpl)
        nid = new_id()
        dev["id"] = nid
        dev["name"] = name
        dev["datacenter"] = dc
        dev["floor"] = floor
        # fresh MAC on the single management interface
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

    summary = []
    for dc, c in DCS.items():
        bx, by = c["x"], c["y"]
        # Mechanical panel (passive)
        mech = mk_node(mech_tmpl, f"MECH-{dc}", bx, by, dc, c["floor"],
                       vendor="APC by Schneider Electric", model_name="APC Galaxy RPP 160A",
                       room="Mechanical Room", mgmt_ip="", ip_address="")
        power_edge(c["gen"], mech)
        # CRAH units (passive cooling load)
        for k in range(1, 5):
            crah = mk_node(mech_tmpl, f"CRAH-{dc}-{k}", bx + 140, by - 90 + k * 60, dc, c["floor"],
                           device_type="crah", vendor="Vertiv (Liebert)",
                           model_name="Vertiv Liebert PCW 100kW", room="Server Hall",
                           mgmt_ip="", ip_address="")
            power_edge(mech, crah)
        # Cooling meter on the mechanical panel
        cool = mk_node(meter_tmpl, f"EV2-COOL-{dc}", bx, by - 100, dc, c["floor"],
                       model_name="Verdigris EV2-42", room="Mechanical Room",
                       mgmt_ip=(cip := new_ip()), snmp_community=cip, ip_address="",
                       monitored_panel=mech)
        power_edge(cool, mech)
        mgmt_edge(cool, c["oob"], c["ports"][0])
        # Facility meter on the generator (building feed)
        fac = mk_node(meter_tmpl, f"EV2-FAC-{dc}", bx - 200, by + 90, dc, c["floor"],
                      model_name="Verdigris EV2-42", room="Generator Room",
                      mgmt_ip=(fip := new_ip()), snmp_community=fip, ip_address="",
                      monitored_panel=c["gen"])
        power_edge(fac, c["gen"])
        mgmt_edge(fac, c["oob"], c["ports"][1])
        summary.append(f"{dc}: MECH+4xCRAH, cool={cip}, fac={fip}")

    TOPO.write_text(json.dumps(d, indent=2))
    print("Added per DC:")
    for s in summary: print("  ", s)
    print(f"nodes -> {len(nodes)}, edges -> {len(edges)}")

if __name__ == "__main__":
    main()
