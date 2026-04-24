"""
LLDP / CDP Neighbor Table Generator.
Produces OID entries for LLDP-MIB and Cisco CDP MIB.
"""
from __future__ import annotations
from typing import List, Tuple
from core.device_manager import Device


# LLDP-MIB base OIDs
LLDP_BASE = "1.0.8802.1.1.2.1"
LLDP_LOCAL_PORT_DESCR = f"{LLDP_BASE}.3.7.1.3"
LLDP_REM_TABLE = f"{LLDP_BASE}.4.1.1"

# CDP (Cisco) OID base  (CISCO-CDP-MIB)
CDP_BASE = "1.3.6.1.4.1.9.9.23.1.2.1.1"


def _encode_mac_as_oid_suffix(mac: str) -> str:
    """Convert AA:BB:CC:DD:EE:FF → 170.187.204.221.238.255"""
    return ".".join(str(int(b, 16)) for b in mac.split(":"))


def generate_lldp_entries(device: Device, neighbors: List[Tuple[Device, int, int]]) -> List[Tuple[str, str, str]]:
    """
    neighbors: list of (neighbor_device, local_port_index, remote_port_index)
    Returns list of (oid, type_code, value) tuples.
    """
    entries: List[Tuple[str, str, str]] = []

    for time_mark_idx, (neighbor, local_port_idx, remote_port_idx) in enumerate(neighbors, start=1):
        tm = 0  # timeMark
        lport = local_port_idx

        # lldpRemChassisIdSubtype (5 = network address)
        entries.append((f"{LLDP_REM_TABLE}.4.{tm}.{lport}.{time_mark_idx}", "2", "5"))

        # lldpRemChassisId - encode neighbor IP as OID-ready bytes
        ip_bytes = neighbor.ip_address.split(".")
        chassis_id = ".".join(ip_bytes)
        entries.append((f"{LLDP_REM_TABLE}.5.{tm}.{lport}.{time_mark_idx}", "4", neighbor.ip_address))

        # lldpRemPortIdSubtype (5 = interface name)
        entries.append((f"{LLDP_REM_TABLE}.6.{tm}.{lport}.{time_mark_idx}", "2", "5"))

        # lldpRemPortId
        if remote_port_idx < len(neighbor.interfaces):
            rport_name = neighbor.interfaces[remote_port_idx].name
        else:
            rport_name = f"port{remote_port_idx}"
        entries.append((f"{LLDP_REM_TABLE}.7.{tm}.{lport}.{time_mark_idx}", "4", rport_name))

        # lldpRemPortDesc
        entries.append((f"{LLDP_REM_TABLE}.8.{tm}.{lport}.{time_mark_idx}", "4", rport_name))

        # lldpRemSysName
        entries.append((f"{LLDP_REM_TABLE}.9.{tm}.{lport}.{time_mark_idx}", "4", neighbor.name))

        # lldpRemSysDesc
        entries.append((f"{LLDP_REM_TABLE}.10.{tm}.{lport}.{time_mark_idx}", "4", neighbor.sys_descr[:64]))

        # lldpRemSysCapSupported (bridge + router = 0x14)
        entries.append((f"{LLDP_REM_TABLE}.11.{tm}.{lport}.{time_mark_idx}", "4", "\x00\x14"))

        # lldpRemSysCapEnabled
        entries.append((f"{LLDP_REM_TABLE}.12.{tm}.{lport}.{time_mark_idx}", "4", "\x00\x14"))

    return entries


def generate_cdp_entries(device: Device, neighbors: List[Tuple[Device, int, int]]) -> List[Tuple[str, str, str]]:
    """
    CDP neighbor table for Cisco devices.
    Returns list of (oid, type_code, value) tuples.
    """
    entries: List[Tuple[str, str, str]] = []

    for idx, (neighbor, local_port_idx, remote_port_idx) in enumerate(neighbors, start=1):
        lport = local_port_idx + 1

        # cdpCacheDeviceId
        entries.append((f"{CDP_BASE}.6.{lport}.{idx}", "4", neighbor.name))

        # cdpCacheAddressType (1=ip)
        entries.append((f"{CDP_BASE}.3.{lport}.{idx}", "2", "1"))

        # cdpCacheAddress - IP as octet string
        entries.append((f"{CDP_BASE}.4.{lport}.{idx}", "4", neighbor.ip_address))

        # cdpCacheVersion
        entries.append((f"{CDP_BASE}.5.{lport}.{idx}", "4", neighbor.sys_descr[:64]))

        # cdpCacheDevicePort
        if remote_port_idx < len(neighbor.interfaces):
            rport = neighbor.interfaces[remote_port_idx].name
        else:
            rport = f"port{remote_port_idx}"
        entries.append((f"{CDP_BASE}.7.{lport}.{idx}", "4", rport))

        # cdpCachePlatform
        entries.append((f"{CDP_BASE}.8.{lport}.{idx}", "4", neighbor.vendor.value))

        # cdpCacheCapabilities
        entries.append((f"{CDP_BASE}.9.{lport}.{idx}", "4", "\x00\x00\x00\x08"))

        # cdpCacheNativeVLAN
        entries.append((f"{CDP_BASE}.11.{lport}.{idx}", "2", "1"))

    return entries
