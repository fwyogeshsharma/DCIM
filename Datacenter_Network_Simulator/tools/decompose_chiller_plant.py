"""Replace each datacenter's lumped chiller device with a realistic, discrete,
SNMP+BACnet-monitored chilled-water plant hanging off the PLANT panel, with its
own plant-room OOB management switch.

Per DC it builds:
  * 1 plant OOB switch  OOB-PLANT-<dc>   (aggregates plant mgmt, uplinks to core OOB)
  * 2 chillers          CHILLER-<dc>-1/2      (Carrier)        fed from PLANT-<dc>
  * 3 CHW pumps         CHWP-<dc>-1/2/3       (Grundfos) N+1   fed from PLANT-<dc>
  * 2 condenser pumps   CWP-<dc>-1/2          (Grundfos)       fed from PLANT-<dc>
  * 2 cooling towers    CT-<dc>-1/2           (BAC)            fed from PLANT-<dc>
  * 2 control valves    VLV-<dc>-CHW/CW       (Belimo)         instrumentation
  * 3 plant sensors     SENS-<dc>-CHWS/CHWR/FLOW               instrumentation

Management topology is physically faithful: every plant device's mgmt port lands
on the local OOB-PLANT-<dc> switch (same mechanical room), and that switch
uplinks once to the data-hall OOB-CORE-<dc>. No plant cabling is trunked
directly into data-hall access switches.

Idempotent: removes any previously-generated plant nodes/edges (incl. the plant
OOB switch) first, then rebuilds. Run tools/populate_power_draw.py afterwards.
"""
import json
import uuid
import copy
import random
from pathlib import Path

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"

PREFIXES = ("CHILLER-", "CHWP-", "CWP-", "CT-", "VLV-", "SENS-", "OOB-PLANT-")

def plant_devices(dc):
    return [
        (f"CHILLER-{dc}-1", "chiller", "Carrier", "Carrier 30XW 500kW"),
        (f"CHILLER-{dc}-2", "chiller", "Carrier", "Carrier 30XW 500kW"),
        (f"CHWP-{dc}-1", "pump", "Grundfos", "Grundfos NB 65-200"),
        (f"CHWP-{dc}-2", "pump", "Grundfos", "Grundfos NB 65-200"),
        (f"CHWP-{dc}-3", "pump", "Grundfos", "Grundfos NB 65-200"),
        (f"CWP-{dc}-1", "pump", "Grundfos", "Grundfos TP 100-360"),
        (f"CWP-{dc}-2", "pump", "Grundfos", "Grundfos TP 100-360"),
        (f"CT-{dc}-1", "cooling_tower", "Baltimore Aircoil Company", "BAC PT2 Series"),
        (f"CT-{dc}-2", "cooling_tower", "Baltimore Aircoil Company", "BAC PT2 Series"),
        (f"VLV-{dc}-CHW", "valve", "Belimo", "Belimo PR..A-BAC"),
        (f"VLV-{dc}-CW", "valve", "Belimo", "Belimo PR..A-BAC"),
        (f"SENS-{dc}-CHWS", "sensor", "Vertiv (Liebert)", "Plant CHW Supply Temp"),
        (f"SENS-{dc}-CHWR", "sensor", "Vertiv (Liebert)", "Plant CHW Return Temp"),
        (f"SENS-{dc}-FLOW", "sensor", "Vertiv (Liebert)", "Plant CHW Flow Meter"),
    ]

POWER_LOADS = {"chiller", "pump", "cooling_tower"}

# (core OOB switch id, free uplink port on it, base x/y, floor)
DC_META = {
    "DC1": dict(core_oob="64d55984", core_port=8, x=1300, y=2560, floor="1"),
    "DC2": dict(core_oob="431a7dbd", core_port=7, x=2940, y=2560, floor="2"),
}
PLANT_OOB_UPLINK_PORT = 48   # port on OOB-PLANT-<dc> used for the core uplink

def main():
    d = json.loads(TOPO.read_text())
    nodes, edges = d["nodes"], d["edges"]

    # 1. strip previously generated plant nodes + their edges
    doomed = {n["id"] for n in nodes
              if any(n["device"]["name"].startswith(p) for p in PREFIXES)}
    nodes[:] = [n for n in nodes if n["id"] not in doomed]
    edges[:] = [e for e in edges if e["src"] not in doomed and e["dst"] not in doomed]

    byname = {n["device"]["name"]: n for n in nodes}

    used_ids = {n["id"] for n in nodes}
    def new_id():
        while True:
            i = uuid.uuid4().hex[:8]
            if i not in used_ids:
                used_ids.add(i); return i

    used_ips = {n["device"].get("mgmt_ip", "") for n in nodes}
    ip_iter = (f"192.168.4.{h}" for h in range(202, 255))
    def new_ip():
        for ip in ip_iter:
            if ip not in used_ips:
                used_ips.add(ip); return ip
        raise RuntimeError("out of mgmt IPs")

    sens_tmpl = copy.deepcopy(next(n["device"] for n in nodes
                                   if n["device"]["device_type"] == "sensor"))
    oob_tmpl = copy.deepcopy(next(n["device"] for n in nodes
                                  if n["device"]["device_type"] == "oob_switch"))

    def _fresh_macs(dev):
        for itf in dev.get("interfaces", []):
            itf["mac_address"] = ":".join(f"{random.randint(0,255):02x}" for _ in range(6))
            itf["connected_to_device"] = None
            itf["connected_to_iface"] = None

    def mk_oob(dc, x, y, floor, core_oob, core_port):
        dev = copy.deepcopy(oob_tmpl)
        nid = new_id(); ip = new_ip()
        dev.update(
            id=nid, name=f"OOB-PLANT-{dc}", device_type="oob_switch",
            datacenter=dc, floor=floor, room="Chiller Plant",
            mgmt_ip=ip, snmp_community=ip, ip_address="",
            sys_location_override="", sys_contact="",
        )
        dev.pop("monitored_panel", None)
        _fresh_macs(dev)
        nodes.append({"id": nid, "position": {"x": x, "y": y}, "device": dev})
        # uplink to the data-hall core OOB switch
        edges.append({"src": nid, "dst": core_oob,
                      "src_iface": PLANT_OOB_UPLINK_PORT, "dst_iface": core_port,
                      "layer": "management"})
        return nid

    def mk_device(name, dtype, vendor, model, dc, floor, x, y, oob_id, oob_port):
        dev = copy.deepcopy(sens_tmpl)
        nid = new_id(); ip = new_ip()
        dev.update(
            id=nid, name=name, device_type=dtype, vendor=vendor, model_name=model,
            datacenter=dc, floor=floor, room="Chiller Plant",
            mgmt_ip=ip, snmp_community=ip, ip_address="",
            sys_location_override="", sys_contact="",
        )
        dev.pop("monitored_panel", None)
        _fresh_macs(dev)
        nodes.append({"id": nid, "position": {"x": x, "y": y}, "device": dev})
        edges.append({"src": nid, "dst": oob_id, "src_iface": 0,
                      "dst_iface": oob_port, "layer": "management"})
        return nid

    for dc, meta in DC_META.items():
        plant = byname.get(f"PLANT-{dc}")
        if plant is None:
            raise RuntimeError(f"PLANT-{dc} not found -- run add_chiller_plant.py first")
        plant_id = plant["id"]
        bx, by = meta["x"], meta["y"]
        oob_id = mk_oob(dc, bx - 80, by - 80, meta["floor"], meta["core_oob"], meta["core_port"])
        # Power the plant OOB switch from the chiller-plant switchboard (PLANT),
        # the only power source in this building. (True 2N would need a second
        # in-building board; not modelled — MECH is in the server hall, so
        # feeding from it would be a cross-building run.)
        edges.append({"src": plant_id, "dst": oob_id, "src_iface": 0, "dst_iface": 0, "layer": "power"})
        for i, (name, dtype, vendor, model) in enumerate(plant_devices(dc)):
            nid = mk_device(name, dtype, vendor, model, dc, meta["floor"],
                            bx + (i % 4) * 60, by + (i // 4) * 70, oob_id, i + 1)
            if dtype in POWER_LOADS:
                edges.append({"src": plant_id, "dst": nid, "src_iface": 0,
                              "dst_iface": 0, "layer": "power"})
        print(f"  {dc}: OOB-PLANT-{dc} (uplink -> core) + 14 plant devices on it")

    TOPO.write_text(json.dumps(d, indent=2))
    print(f"Removed {len(doomed)} old plant nodes; nodes -> {len(nodes)}, edges -> {len(edges)}")

if __name__ == "__main__":
    main()
