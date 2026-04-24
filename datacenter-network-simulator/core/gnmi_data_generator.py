"""
gNMI Data Generator — produces OpenConfig JSON-IETF documents for switches and routers.

Each device gets one file:  datasets/gnmi/<ip>.gnmi.json

The JSON structure mirrors real OpenConfig YANG paths so that any gNMI client
(gnmic, Telegraf, custom collectors) can query the simulator and get
semantically correct responses.

Switches generate:
    openconfig-interfaces, openconfig-lldp,
    openconfig-network-instance (VLANs + FDB),
    openconfig-system

Routers generate:
    openconfig-interfaces, openconfig-lldp,
    openconfig-network-instance (BGP + OSPF + AFT/routes),
    openconfig-system
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from core.device_manager import Device
    from core.topology_engine import TopologyEngine

from core.device_manager import DeviceType

# ── OpenConfig speed strings ──────────────────────────────────────────────────
_SPEED_MAP = {
    100_000_000:     "SPEED_100MB",
    1_000_000_000:   "SPEED_1GB",
    10_000_000_000:  "SPEED_10GB",
    25_000_000_000:  "SPEED_25GB",
    40_000_000_000:  "SPEED_40GB",
    100_000_000_000: "SPEED_100GB",
}

# Per-device autonomous system numbers (deterministic per device id)
def _as_number(device_id: str) -> int:
    return 65000 + (int(device_id[:4], 16) % 1000)


class GNMIDataGenerator:
    """Generate and save OpenConfig JSON data files for gNMI simulation."""

    def __init__(self, output_dir: str = "datasets/gnmi"):
        self.output_dir = str(Path(output_dir).resolve())
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def generate_device(self, device: "Device", topology: "TopologyEngine") -> Optional[str]:
        """
        Generate a .gnmi.json file for *device* and return the file path.
        Returns None if device type is not supported (e.g. server).
        """
        if device.device_type not in (DeviceType.ROUTER, DeviceType.SWITCH):
            return None

        data = self._build_document(device, topology)
        path = os.path.join(self.output_dir, f"{device.ip_address}.gnmi.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return path

    def regenerate(self, device: "Device", topology: "TopologyEngine") -> Optional[dict]:
        """
        Regenerate in-memory data (and re-save) for a single device.
        Returns the data dict so the gNMI server can hot-reload it.
        """
        if device.device_type not in (DeviceType.ROUTER, DeviceType.SWITCH):
            return None
        data = self._build_document(device, topology)
        path = os.path.join(self.output_dir, f"{device.ip_address}.gnmi.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data

    # ------------------------------------------------------------------ #
    #  Document builder                                                    #
    # ------------------------------------------------------------------ #

    def _build_document(self, device: "Device", topology: "TopologyEngine") -> dict:
        """Assemble the full OpenConfig document for one device."""
        doc: dict = {
            "target":      device.ip_address,
            "device_type": device.device_type.value,
        }

        doc["openconfig-interfaces:interfaces"]  = self._build_interfaces(device, topology)
        doc["openconfig-lldp:lldp"]              = self._build_lldp(device, topology)
        doc["openconfig-system:system"]          = self._build_system(device)
        doc["openconfig-platform:components"]    = self._build_platform(device)

        ni = self._build_network_instance(device, topology)
        doc["openconfig-network-instance:network-instances"] = ni

        return doc

    # ------------------------------------------------------------------ #
    #  openconfig-interfaces                                               #
    # ------------------------------------------------------------------ #

    def _build_interfaces(self, device: "Device", topology: "TopologyEngine") -> dict:
        iface_list = []
        for iface in device.interfaces:
            oper = "UP" if iface.oper_status == 1 else "DOWN"
            speed_str = _SPEED_MAP.get(iface.speed, "SPEED_1GB")
            in_pkts  = iface.in_octets  // 1500
            out_pkts = iface.out_octets // 1500

            entry = {
                "name": iface.name,
                "config": {
                    "name": iface.name,
                    "type": "iana-if-type:ethernetCsmacd",
                    "enabled": True,
                },
                "state": {
                    "name":         iface.name,
                    "type":         "iana-if-type:ethernetCsmacd",
                    "mtu":          1500,
                    "enabled":      True,
                    "admin-status": "UP",
                    "oper-status":  oper,
                    "counters": {
                        "in-octets":        str(iface.in_octets),
                        "out-octets":       str(iface.out_octets),
                        "in-unicast-pkts":  str(in_pkts),
                        "out-unicast-pkts": str(out_pkts),
                        "in-errors":        str(iface.in_errors),
                        "out-errors":       str(iface.out_errors),
                        "in-discards":      str(iface.in_discards),
                        "out-discards":     str(iface.out_discards),
                        "last-clear":       "1970-01-01T00:00:00Z",
                    },
                },
                "ethernet": {
                    "state": {
                        "mac-address": iface.mac_address,
                        "port-speed":  speed_str,
                        "duplex-mode": "FULL",
                        "hw-mac-address": iface.mac_address,
                    }
                },
            }

            # Add IP address on the first interface (management)
            if iface.index == 1:
                entry["subinterfaces"] = {
                    "subinterface": [{
                        "index": 0,
                        "config": {"index": 0, "enabled": True},
                        "state":  {"index": 0, "admin-status": "UP", "oper-status": oper},
                        "openconfig-if-ip:ipv4": {
                            "config": {"enabled": True, "mtu": 1500},
                            "addresses": {
                                "address": [{
                                    "ip": device.ip_address,
                                    "config": {"ip": device.ip_address, "prefix-length": 24},
                                    "state":  {"ip": device.ip_address, "prefix-length": 24,
                                               "origin": "STATIC"},
                                }]
                            },
                        },
                    }]
                }

            iface_list.append(entry)

        return {"interface": iface_list}

    # ------------------------------------------------------------------ #
    #  openconfig-lldp                                                     #
    # ------------------------------------------------------------------ #

    def _build_lldp(self, device: "Device", topology: "TopologyEngine") -> dict:
        """Build LLDP neighbor data from topology edges."""
        lldp_ifaces = []

        neighbors = topology.get_neighbors(device.id) if hasattr(topology, "get_neighbors") else []
        for neighbor in neighbors:
            # Find which local interface connects to this neighbor
            edge_data = topology.graph.edges.get((device.id, neighbor.id)) or \
                        topology.graph.edges.get((neighbor.id, device.id))
            if not edge_data:
                continue

            if edge_data.get("src_node") == device.id:
                local_iface_idx  = edge_data.get("src_iface", 0)
                remote_iface_idx = edge_data.get("dst_iface", 0)
            else:
                local_iface_idx  = edge_data.get("dst_iface", 0)
                remote_iface_idx = edge_data.get("src_iface", 0)

            local_iface  = device.interfaces[local_iface_idx]  if local_iface_idx  < len(device.interfaces)   else None
            remote_iface = neighbor.interfaces[remote_iface_idx] if remote_iface_idx < len(neighbor.interfaces) else None

            if not local_iface:
                continue

            neighbor_entry = {
                "id": f"{neighbor.ip_address}-1",
                "config": {"id": f"{neighbor.ip_address}-1"},
                "state": {
                    "id":                   f"{neighbor.ip_address}-1",
                    "chassis-id":           neighbor.interfaces[0].mac_address if neighbor.interfaces else "00:00:00:00:00:00",
                    "chassis-id-type":      "MAC_ADDRESS",
                    "port-id":              remote_iface.name if remote_iface else "eth0",
                    "port-id-type":         "INTERFACE_NAME",
                    "port-description":     remote_iface.name if remote_iface else "eth0",
                    "system-name":          neighbor.name,
                    "system-description":   neighbor.sys_descr,
                    "management-address":   neighbor.ip_address,
                    "management-address-type": "IPV4",
                },
            }

            lldp_ifaces.append({
                "name":   local_iface.name,
                "config": {"name": local_iface.name, "enabled": True},
                "state":  {"name": local_iface.name, "enabled": True},
                "neighbors": {"neighbor": [neighbor_entry]},
            })

        return {
            "config": {"enabled": True, "hello-timer": 30, "hold-multiplier": 4},
            "state":  {"enabled": True, "hello-timer": 30, "hold-multiplier": 4},
            "interfaces": {"interface": lldp_ifaces},
        }

    # ------------------------------------------------------------------ #
    #  openconfig-platform (temperature)                                  #
    # ------------------------------------------------------------------ #

    def _build_platform(self, device: "Device") -> dict:
        """
        Build openconfig-platform:components with CHASSIS and CPU temperature.

        alarm-threshold: CHASSIS 55 °C, CPU 80 °C
        alarm-status is True when instant >= threshold.
        """
        def _temp_state(instant: float, threshold: float) -> dict:
            lo   = round(max(18.0, instant - random.uniform(3, 8)), 1)
            hi   = round(min(99.0, instant + random.uniform(2, 8)), 1)
            return {
                "instant":         instant,
                "avg":             round((lo + hi) / 2, 1),
                "min":             lo,
                "max":             hi,
                "alarm-status":    instant >= threshold,
                "alarm-severity":  "openconfig-alarm-types:WARNING",
                "alarm-threshold": threshold,
            }

        chassis_temp = round(device.inlet_temp, 1)
        cpu_temp     = round(device.cpu_temp, 1)

        components = [
            {
                "name":   "CHASSIS",
                "config": {"name": "CHASSIS"},
                "state": {
                    "name":        "CHASSIS",
                    "type":        "openconfig-platform-types:CHASSIS",
                    "description": f"{device.vendor.value} Chassis",
                    "temperature": _temp_state(chassis_temp, 55.0),
                },
            },
            {
                "name":   "CPU",
                "config": {"name": "CPU"},
                "state": {
                    "name":        "CPU",
                    "type":        "openconfig-platform-types:CPU",
                    "description": "Routing / Management CPU",
                    "temperature": _temp_state(cpu_temp, 80.0),
                },
            },
        ]

        return {"component": components}

    # ------------------------------------------------------------------ #
    #  openconfig-system                                                   #
    # ------------------------------------------------------------------ #

    def _build_system(self, device: "Device") -> dict:
        uptime_ns = device.sys_uptime * 10_000_000  # centiseconds → nanoseconds approx
        cpu_entries = [{
            "index": "ALL",
            "state": {
                "index": "ALL",
                "total": {"instant": device.cpu_usage, "avg": device.cpu_usage,
                          "min": max(0, device.cpu_usage - 10),
                          "max": min(100, device.cpu_usage + 10)},
            },
        }]

        return {
            "config":   {
                "hostname":    device.name,
                "domain-name": "lab.local",
            },
            "state":    {
                "hostname":        device.name,
                "domain-name":     "lab.local",
                "boot-time":       int(time.time() * 1e9) - uptime_ns,
                "uptime":          device.sys_uptime,
                "software-version": device.os_version,
                "os-name":         device.os_name,
            },
            "memory": {
                "state": {
                    "physical":    str(device.memory_total),
                    "reserved":    str(device.memory_used),
                    "free":        str(device.memory_total - device.memory_used),
                    "utilized":    round(device.memory_used / max(1, device.memory_total) * 100, 1),
                }
            },
            "cpus": {"cpu": cpu_entries},
        }

    # ------------------------------------------------------------------ #
    #  openconfig-network-instance                                         #
    # ------------------------------------------------------------------ #

    def _build_network_instance(self, device: "Device", topology: "TopologyEngine") -> dict:
        ni_entry: dict = {
            "name":   "DEFAULT",
            "config": {"name": "DEFAULT", "type": "DEFAULT_INSTANCE"},
            "state":  {"name": "DEFAULT", "type": "DEFAULT_INSTANCE", "enabled": True},
        }

        if device.device_type == DeviceType.SWITCH:
            ni_entry["vlans"]    = self._build_vlans(device)
            ni_entry["fdb"]      = self._build_fdb(device, topology)
        elif device.device_type == DeviceType.ROUTER:
            ni_entry["protocols"] = self._build_protocols(device, topology)
            ni_entry["afts"]      = self._build_aft(device, topology)

        return {"network-instance": [ni_entry]}

    # ---- VLANs (switches) ------------------------------------------------

    def _build_vlans(self, device: "Device") -> dict:
        # Simulate VLANs 1–10 for every switch
        vlans = []
        vlan_names = {
            1: "default", 2: "management", 3: "servers",
            4: "storage", 5: "dmz", 6: "voice", 7: "iot",
            8: "backup", 9: "monitoring", 10: "quarantine",
        }
        for vid in range(1, 11):
            vlans.append({
                "vlan-id": vid,
                "config": {"vlan-id": vid, "name": vlan_names.get(vid, f"vlan{vid}"),
                           "status": "ACTIVE"},
                "state":  {"vlan-id": vid, "name": vlan_names.get(vid, f"vlan{vid}"),
                           "status": "ACTIVE"},
            })
        return {"vlan": vlans}

    # ---- FDB / MAC table (switches) --------------------------------------

    def _build_fdb(self, device: "Device", topology: "TopologyEngine") -> dict:
        entries = []
        # One FDB entry per connected interface
        for iface in device.interfaces:
            if iface.connected_to_device:
                neighbor = None
                try:
                    from core.device_manager import DeviceManager  # avoid circular at module level
                except ImportError:
                    pass
                # Use MAC from interface
                entries.append({
                    "mac-address": iface.mac_address,
                    "vlan":        1,
                    "config": {
                        "mac-address": iface.mac_address,
                        "vlan":        1,
                    },
                    "state": {
                        "mac-address": iface.mac_address,
                        "vlan":        1,
                        "entry-type":  "DYNAMIC",
                        "age":         random.randint(10, 300),
                    },
                    "interface": {
                        "interface-ref": {
                            "config": {"interface": iface.name, "subinterface": 0},
                            "state":  {"interface": iface.name, "subinterface": 0},
                        }
                    },
                })

        # Add a few static entries so the table is non-trivial
        for i in range(min(5, len(device.interfaces))):
            mac = ":".join(f"{random.randint(0, 255):02x}" for _ in range(6))
            entries.append({
                "mac-address": mac,
                "vlan":        random.randint(1, 10),
                "config": {"mac-address": mac, "vlan": 1},
                "state":  {
                    "mac-address": mac,
                    "vlan":        random.randint(1, 10),
                    "entry-type":  "DYNAMIC",
                    "age":         random.randint(30, 600),
                },
                "interface": {
                    "interface-ref": {
                        "config": {"interface": device.interfaces[i].name, "subinterface": 0},
                        "state":  {"interface": device.interfaces[i].name, "subinterface": 0},
                    }
                },
            })

        return {
            "config": {"mac-aging-time": 300, "mac-learning": True},
            "state":  {"mac-aging-time": 300, "mac-learning": True},
            "mac-table": {"entries": {"entry": entries}},
        }

    # ---- Routing protocols (routers) -------------------------------------

    def _build_protocols(self, device: "Device", topology: "TopologyEngine") -> dict:
        protocols = []
        neighbors = topology.get_neighbors(device.id) if hasattr(topology, "get_neighbors") else []
        router_neighbors = [n for n in neighbors if n.device_type == DeviceType.ROUTER]

        # BGP
        bgp_neighbors = []
        for neighbor in router_neighbors:
            bgp_neighbors.append({
                "neighbor-address": neighbor.ip_address,
                "config": {
                    "neighbor-address": neighbor.ip_address,
                    "peer-as":          _as_number(neighbor.id),
                    "enabled":          True,
                },
                "state": {
                    "neighbor-address":     neighbor.ip_address,
                    "peer-as":              _as_number(neighbor.id),
                    "local-as":             _as_number(device.id),
                    "session-state":        "ESTABLISHED",
                    "established-transitions": random.randint(1, 5),
                    "messages": {
                        "received": {"UPDATE": random.randint(100, 5000), "KEEPALIVE": random.randint(1000, 50000)},
                        "sent":     {"UPDATE": random.randint(100, 5000), "KEEPALIVE": random.randint(1000, 50000)},
                    },
                },
                "timers": {
                    "config": {"hold-time": 90, "keepalive-interval": 30},
                    "state":  {"hold-time": 90, "keepalive-interval": 30,
                               "negotiated-hold-time": 90},
                },
            })

        protocols.append({
            "identifier": "BGP",
            "name":       "BGP",
            "config": {"identifier": "BGP", "name": "BGP", "enabled": True},
            "state":  {"identifier": "BGP", "name": "BGP", "enabled": True},
            "bgp": {
                "global": {
                    "config": {"as": _as_number(device.id), "router-id": device.ip_address},
                    "state":  {"as": _as_number(device.id), "router-id": device.ip_address,
                               "total-paths": len(bgp_neighbors) * 5,
                               "total-prefixes": len(bgp_neighbors) * 5},
                },
                "neighbors": {"neighbor": bgp_neighbors},
            },
        })

        # OSPF
        ospf_ifaces = []
        for neighbor in router_neighbors:
            edge_data = topology.graph.edges.get((device.id, neighbor.id)) or \
                        topology.graph.edges.get((neighbor.id, device.id))
            if not edge_data:
                continue
            if edge_data.get("src_node") == device.id:
                local_iface_idx = edge_data.get("src_iface", 0)
            else:
                local_iface_idx = edge_data.get("dst_iface", 0)
            local_iface = device.interfaces[local_iface_idx] if local_iface_idx < len(device.interfaces) else None
            if not local_iface:
                continue

            ospf_ifaces.append({
                "id":     local_iface.name,
                "config": {"id": local_iface.name, "network-type": "POINT_TO_POINT",
                           "enabled": True, "metric": 10},
                "state":  {"id": local_iface.name, "network-type": "POINT_TO_POINT",
                           "enabled": True, "metric": 10},
                "neighbors": {
                    "neighbor": [{
                        "neighbor-id": neighbor.ip_address,
                        "config": {"neighbor-id": neighbor.ip_address},
                        "state":  {
                            "neighbor-id":     neighbor.ip_address,
                            "neighbor-address": neighbor.ip_address,
                            "state":           "FULL",
                            "adjacency-state": "FULL",
                        },
                    }]
                },
            })

        protocols.append({
            "identifier": "OSPF",
            "name":       "OSPF",
            "config": {"identifier": "OSPF", "name": "OSPF", "enabled": True},
            "state":  {"identifier": "OSPF", "name": "OSPF", "enabled": True},
            "ospfv2": {
                "global": {
                    "config": {"router-id": device.ip_address},
                    "state":  {"router-id": device.ip_address},
                },
                "areas": {
                    "area": [{
                        "identifier": "0.0.0.0",
                        "config": {"identifier": "0.0.0.0"},
                        "state":  {"identifier": "0.0.0.0"},
                        "interfaces": {"interface": ospf_ifaces},
                    }]
                },
            },
        })

        return {"protocol": protocols}

    # ---- Abstract Forwarding Table (routers) -----------------------------

    def _build_aft(self, device: "Device", topology: "TopologyEngine") -> dict:
        """Build a simulated IPv4 routing table from topology neighbors."""
        neighbors = topology.get_neighbors(device.id) if hasattr(topology, "get_neighbors") else []
        entries = []

        # Default route
        entries.append({
            "prefix": "0.0.0.0/0",
            "config": {"prefix": "0.0.0.0/0"},
            "state":  {
                "prefix":          "0.0.0.0/0",
                "origin-protocol": "STATIC",
                "metric":          1,
            },
        })

        # One /24 route per neighbor
        for neighbor in neighbors:
            parts = neighbor.ip_address.split(".")
            if len(parts) == 4:
                subnet = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
            else:
                continue

            protocol = "OSPF" if neighbor.device_type == DeviceType.ROUTER else "STATIC"
            entries.append({
                "prefix": subnet,
                "config": {"prefix": subnet},
                "state":  {
                    "prefix":          subnet,
                    "origin-protocol": protocol,
                    "metric":          random.randint(1, 100),
                },
                "next-hops": {
                    "next-hop": [{
                        "index":  str(neighbor.interfaces[0].index) if neighbor.interfaces else "1",
                        "config": {"index": "1"},
                        "state":  {
                            "index":          "1",
                            "ip-address":     neighbor.ip_address,
                            "weight":         1,
                            "recurse":        False,
                        },
                    }]
                },
            })

        return {
            "ipv4-unicast": {
                "ipv4-entry": entries,
            }
        }