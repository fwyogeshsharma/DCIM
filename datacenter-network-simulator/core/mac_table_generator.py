"""
MAC Address Table Generator for switch devices.
Produces BRIDGE-MIB (RFC 1493) entries.
"""
from __future__ import annotations
import random
from typing import List, Tuple
from core.device_manager import Device, DeviceType


# BRIDGE-MIB OIDs
DOT1D_BASE = "1.3.6.1.2.1.17"
DOT1D_TP_FDB_ADDRESS = f"{DOT1D_BASE}.4.3.1.1"   # dot1dTpFdbAddress
DOT1D_TP_FDB_PORT    = f"{DOT1D_BASE}.4.3.1.2"   # dot1dTpFdbPort
DOT1D_TP_FDB_STATUS  = f"{DOT1D_BASE}.4.3.1.3"   # dot1dTpFdbStatus (3=learned)
DOT1D_BASE_PORT_IFINDEX = f"{DOT1D_BASE}.1.4.1.2" # dot1dBasePortIfIndex

# Q-BRIDGE VLAN table
DOT1Q_BASE = "1.3.6.1.2.1.17.7.1"
DOT1Q_VLAN_FDB = f"{DOT1Q_BASE}.2.2.1"


def _mac_to_oid_suffix(mac: str) -> str:
    """Convert AA:BB:CC:DD:EE:FF → 170.187.204.221.238.255"""
    return ".".join(str(int(b, 16)) for b in mac.split(":"))


def _mac_to_hex_str(mac: str) -> str:
    """Convert AA:BB:CC:DD:EE:FF → hex byte string for snmprec."""
    return mac.replace(":", "")


def generate_mac_table(switch: Device, neighbors: List[Tuple[Device, int]]) -> List[Tuple[str, str, str]]:
    """
    Generate MAC address table entries for a switch.

    neighbors: list of (connected_device, port_index)
    Returns list of (oid, type_code, value) tuples.
    """
    entries: List[Tuple[str, str, str]] = []

    for neighbor_device, port_idx in neighbors:
        port_num = port_idx + 1
        # Add MAC for each interface of the neighbor
        for iface in neighbor_device.interfaces:
            mac = iface.mac_address
            oid_suffix = _mac_to_oid_suffix(mac)

            # dot1dTpFdbAddress
            entries.append((f"{DOT1D_TP_FDB_ADDRESS}.{oid_suffix}", "4x", _mac_to_hex_str(mac)))

            # dot1dTpFdbPort
            entries.append((f"{DOT1D_TP_FDB_PORT}.{oid_suffix}", "2", str(port_num)))

            # dot1dTpFdbStatus (3 = learned)
            entries.append((f"{DOT1D_TP_FDB_STATUS}.{oid_suffix}", "2", "3"))

    # Add the switch's own MACs too
    for iface in switch.interfaces:
        mac = iface.mac_address
        oid_suffix = _mac_to_oid_suffix(mac)
        entries.append((f"{DOT1D_TP_FDB_ADDRESS}.{oid_suffix}", "4x", _mac_to_hex_str(mac)))
        entries.append((f"{DOT1D_TP_FDB_PORT}.{oid_suffix}", "2", str(iface.index)))
        entries.append((f"{DOT1D_TP_FDB_STATUS}.{oid_suffix}", "2", "4"))  # 4=self

    # dot1dBasePortIfIndex mapping
    for iface in switch.interfaces:
        entries.append((f"{DOT1D_BASE_PORT_IFINDEX}.{iface.index}", "2", str(iface.index)))

    # VLAN table - VLAN 1 default
    for iface in switch.interfaces:
        entries.append((f"{DOT1Q_VLAN_FDB}.1.1.{iface.index}", "2", "1"))   # vlan 1
        entries.append((f"{DOT1Q_VLAN_FDB}.2.1.{iface.index}", "2", str(iface.index)))

    return entries


def generate_stp_entries(switch: Device) -> List[Tuple[str, str, str]]:
    """Generate Spanning Tree Protocol entries (BRIDGE-MIB)."""
    entries: List[Tuple[str, str, str]] = []
    stp_base = "1.3.6.1.2.1.17.2"

    # dot1dStpProtocolSpecification (3=ieee8021d)
    entries.append((f"{stp_base}.1.0", "2", "3"))

    # dot1dStpPriority (32768 default)
    entries.append((f"{stp_base}.2.0", "2", "32768"))

    # dot1dStpTimeSinceTopologyChange
    entries.append((f"{stp_base}.3.0", "67", str(random.randint(100, 9999999))))

    # dot1dStpTopChanges
    entries.append((f"{stp_base}.4.0", "41", str(random.randint(1, 50))))

    # dot1dStpDesignatedRoot
    root_mac = ":".join(f"{random.randint(0,255):02x}" for _ in range(6))
    entries.append((f"{stp_base}.5.0", "4x", root_mac.replace(":", "")))

    # dot1dStpRootCost
    entries.append((f"{stp_base}.6.0", "2", "0"))

    # dot1dStpRootPort
    entries.append((f"{stp_base}.7.0", "2", "0"))

    # dot1dStpMaxAge
    entries.append((f"{stp_base}.8.0", "2", "2000"))

    # dot1dStpHelloTime
    entries.append((f"{stp_base}.9.0", "2", "200"))

    # dot1dStpHoldTime
    entries.append((f"{stp_base}.10.0", "2", "100"))

    # dot1dStpForwardDelay
    entries.append((f"{stp_base}.11.0", "2", "1500"))

    # Per-port STP state
    stp_port_base = "1.3.6.1.2.1.17.2.15.1"
    for iface in switch.interfaces:
        p = iface.index
        entries.append((f"{stp_port_base}.1.{p}", "2", str(p)))
        entries.append((f"{stp_port_base}.3.{p}", "2", "5"))   # forwarding
        entries.append((f"{stp_port_base}.4.{p}", "2", "32768"))
        entries.append((f"{stp_port_base}.7.{p}", "2", "19"))   # path cost

    return entries
