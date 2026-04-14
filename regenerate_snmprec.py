"""
Regenerate .snmprec files from a topology JSON so LLDP/CDP data
reflects the actual connections.

Steps:
  1. Load topology JSON
  2. Auto-assign unique interface indices to every edge
     (sequential per device, 0-based into interfaces list)
  3. Update interface connected_to_device / connected_to_iface fields
  4. Save updated topology JSON
  5. Regenerate all snmprec files via SNMPRecGenerator

Usage:
  python regenerate_snmprec.py <topology_json>

Example:
  python regenerate_snmprec.py topologies/large_hyperscale_pod.json
"""

import json
import sys
import os

# Run from project root
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from core.topology_engine import TopologyEngine
from core.snmprec_generator import SNMPRecGenerator


def assign_interface_indices(data: dict) -> dict:
    """
    Walk every edge and assign sequential src_iface / dst_iface per device.
    Also updates interface connected_to_device / connected_to_iface on both ends.
    """
    # Map device_id → device dict for fast lookup
    devices = {n["device"]["id"]: n["device"] for n in data["nodes"]}

    # Track next available interface index per device (0-based)
    next_iface: dict[str, int] = {dev_id: 0 for dev_id in devices}

    for edge in data["edges"]:
        src_id = edge["src"]
        dst_id = edge["dst"]
        src_dev = devices[src_id]
        dst_dev = devices[dst_id]

        # Pick next free interface slot on each end
        si = next_iface[src_id]
        di = next_iface[dst_id]

        # Clamp to available interfaces
        src_ifaces = src_dev.get("interfaces", [])
        dst_ifaces = dst_dev.get("interfaces", [])
        si = si % len(src_ifaces) if src_ifaces else 0
        di = di % len(dst_ifaces) if dst_ifaces else 0

        edge["src_iface"] = si
        edge["dst_iface"] = di

        # Patch interface connected_to fields
        if si < len(src_ifaces):
            src_ifaces[si]["connected_to_device"] = dst_id
            src_ifaces[si]["connected_to_iface"]  = di
        if di < len(dst_ifaces):
            dst_ifaces[di]["connected_to_device"] = src_id
            dst_ifaces[di]["connected_to_iface"]  = si

        next_iface[src_id] += 1
        next_iface[dst_id] += 1

    return data


def main():
    topo_path = sys.argv[1] if len(sys.argv) > 1 else "topologies/large_hyperscale_pod.json"

    print(f"Loading topology: {topo_path}")
    with open(topo_path, encoding="utf-8") as f:
        data = json.load(f)

    nodes = len(data.get("nodes", []))
    edges = len(data.get("edges", []))
    print(f"  {nodes} devices, {edges} edges")

    print("Assigning interface indices to edges ...")
    data = assign_interface_indices(data)

    # Save updated topology JSON
    with open(topo_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved updated topology: {topo_path}")

    # Load into TopologyEngine and regenerate snmprec files
    print("Loading into TopologyEngine ...")
    topo = TopologyEngine()
    topo.from_dict(data)

    print("Regenerating snmprec files ...")
    gen = SNMPRecGenerator(output_dir="datasets/snmp")
    files = gen.generate_all(topo)
    print(f"  Generated {len(files)} snmprec files")

    # Quick sanity check: show LLDP neighbor count for first router
    for dev in topo.get_all_devices():
        from core.device_manager import DeviceType
        if dev.device_type in (DeviceType.ROUTER, DeviceType.SWITCH):
            neighbors = topo.get_neighbors(dev.id)
            if neighbors:
                print(f"  {dev.name} ({dev.ip_address}): {len(neighbors)} LLDP neighbor(s): "
                      + ", ".join(n.name for n in neighbors))
            break

    print("Done.")


if __name__ == "__main__":
    main()