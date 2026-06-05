"""Promote CRAH air handlers from passive loads to fully monitored devices:
assign each a management IP and an OOB management edge so it is SNMP-reachable
(and BACnet-served by the controller). CRAH sit in the data hall, so they land
on the data-hall access OOB switches — NOT the chiller-plant OOB.

Power/topology unchanged: CRAH keep their power_draw_w and MECH-panel feed, so
the EV2-COOL meter still aggregates them and PUE is unaffected. Idempotent.
"""
import json
from pathlib import Path

TOPO = Path(__file__).resolve().parent.parent / "topologies" / "dual_dc_enterprise.json"

def main():
    d = json.loads(TOPO.read_text())
    nodes, edges = d["nodes"], d["edges"]
    byid = {n["id"]: n["device"] for n in nodes}

    crah = [n for n in nodes if n["device"]["device_type"] == "crah"]
    crah_ids = {n["id"] for n in crah}

    # idempotent: drop any existing CRAH management edges, then re-create
    edges[:] = [e for e in edges
                if not (e["layer"] == "management" and e["src"] in crah_ids)]

    used_ips = {n["device"].get("mgmt_ip", "") for n in nodes}
    ip_iter = (f"192.168.4.{h}" for h in range(232, 255))
    def new_ip():
        for ip in ip_iter:
            if ip not in used_ips:
                used_ips.add(ip); return ip
        raise RuntimeError("out of mgmt IPs")

    # Free ports on the data-hall ACCESS OOB switches only. CRAH are server-hall
    # end devices, so they attach to leaf/access switches (OOB-SW-*) — never the
    # aggregation OOB-CORE and never the chiller-plant OOB-PLANT.
    def free_ports(dc):
        out = []
        for n in nodes:
            dev = n["device"]
            if dev["device_type"] != "oob_switch" or dev.get("datacenter") != dc:
                continue
            if not dev["name"].startswith("OOB-SW-"):
                continue
            nid = n["id"]; used = set()
            for e in edges:
                if e["src"] == nid: used.add(e["src_iface"])
                if e["dst"] == nid: used.add(e["dst_iface"])
            cap = dev.get("interface_count", 48)
            for p in range(1, min(cap, 48) + 1):
                if p not in used:
                    out.append((nid, p))
        return out

    ports = {"DC1": free_ports("DC1"), "DC2": free_ports("DC2")}
    counts = {}
    for n in crah:
        dev = n["device"]; dc = dev.get("datacenter", "DC1")
        if not dev.get("mgmt_ip"):
            ip = new_ip()
            dev["mgmt_ip"] = ip
            dev["snmp_community"] = ip
        else:
            dev["snmp_community"] = dev["mgmt_ip"]
        dev["ip_address"] = ""
        oob_id, port = ports[dc].pop(0)
        edges.append({"src": n["id"], "dst": oob_id, "src_iface": 0,
                      "dst_iface": port, "layer": "management"})
        counts[dc] = counts.get(dc, 0) + 1

    TOPO.write_text(json.dumps(d, indent=2))
    for dc, c in counts.items():
        print(f"  {dc}: {c} CRAH now monitored (mgmt IP + data-hall OOB edge)")
    print(f"total CRAH promoted: {len(crah)}")

if __name__ == "__main__":
    main()
