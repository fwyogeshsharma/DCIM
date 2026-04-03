"""
SNMPRec Generator - Creates .snmprec dataset files for SNMPSim.

Format per line: OID|TYPE_CODE|VALUE
Type codes:
  2  = Integer32
  4  = OctetString (UTF-8)
  4x = OctetString (hex)
  5  = Null
  6  = OID
  41 = Counter32
  42 = Gauge32
  43 = TimeTicks
  44 = Counter64
  64 = IPAddress
  65 = NetworkAddress
  67 = TimeTicks (alternate)
  70 = Opaque
"""
from __future__ import annotations
import os
import random
from pathlib import Path
from typing import List, Tuple

from core.device_manager import Device, DeviceType, Vendor
from core.lldp_generator import generate_lldp_entries, generate_cdp_entries
from core.mac_table_generator import generate_mac_table, generate_stp_entries
from core.topology_engine import TopologyEngine


OidEntry = Tuple[str, str, str]

# RFC1213 / SNMPv2-MIB
SYSTEM_BASE = "1.3.6.1.2.1.1"
IF_BASE     = "1.3.6.1.2.1.2"
IF_TABLE    = "1.3.6.1.2.1.2.2.1"
IP_BASE     = "1.3.6.1.2.1.4"

# HOST-RESOURCES-MIB (servers)
HR_DEVICE   = "1.3.6.1.2.1.25"
HR_STORAGE  = "1.3.6.1.2.1.25.2"
HR_PROC     = "1.3.6.1.2.1.25.3"
HR_LOAD     = "1.3.6.1.2.1.25.3.3.1.2"  # hrProcessorLoad

# UCD-SNMP-MIB (Linux)
UCD_BASE    = "1.3.6.1.4.1.2021"
UCD_CPU     = f"{UCD_BASE}.11"
UCD_MEM     = f"{UCD_BASE}.4"
UCD_DISK    = f"{UCD_BASE}.9.1"


def _sort_oids(entries: List[OidEntry]) -> List[OidEntry]:
    """Sort OID entries numerically."""
    def oid_key(entry):
        try:
            return tuple(int(x) for x in entry[0].split("."))
        except ValueError:
            return (0,)
    return sorted(entries, key=oid_key)


def _oid_entry(oid: str, typ: str, val: str) -> OidEntry:
    return (oid, typ, str(val))


class SNMPRecGenerator:
    def __init__(self, output_dir: str = "datasets"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def generate_all(self, topology: TopologyEngine) -> List[str]:
        """Generate .snmprec file for every device in the topology."""
        generated = []
        for device in topology.get_all_devices():
            filepath = self.generate_device(device, topology)
            generated.append(filepath)
        return generated

    def generate_device(self, device: Device, topology: TopologyEngine) -> str:
        entries: List[OidEntry] = []

        entries += self._system_entries(device)
        entries += self._interface_entries(device)
        entries += self._ip_entries(device)
        entries += self._snmp_entries(device)

        if device.device_type in (DeviceType.ROUTER, DeviceType.SWITCH):
            neighbors = topology.get_neighbors(device.id)
            neighbor_tuples = self._build_neighbor_tuples(device, topology)
            entries += generate_lldp_entries(device, neighbor_tuples)
            if device.vendor == Vendor.CISCO_SYSTEMS:
                entries += generate_cdp_entries(device, neighbor_tuples)

        if device.device_type == DeviceType.SWITCH:
            neighbor_port_tuples = self._build_switch_port_tuples(device, topology)
            entries += generate_mac_table(device, neighbor_port_tuples)
            entries += generate_stp_entries(device)

        if device.device_type == DeviceType.SERVER:
            entries += self._server_entries(device)

        # Sort and write
        # Output layout:  datasets/<device_ip>.snmprec
        # SNMPSim routes community "<device_ip>" → this file, so set the
        # SNMP community string in openDCIM/your NMS to the device's IP address.
        entries = _sort_oids(entries)
        filepath = str(self.output_dir / f"{device.ip_address}.snmprec")
        self._write_file(filepath, entries)
        return filepath

    # ------------------------------------------------------------------ #
    #  System OIDs                                                         #
    # ------------------------------------------------------------------ #

    def _system_entries(self, device: Device) -> List[OidEntry]:
        return [
            _oid_entry(f"{SYSTEM_BASE}.1.0", "4", device.sys_descr),
            _oid_entry(f"{SYSTEM_BASE}.2.0", "6", "1.3.6.1.6.3.10.3.1.1"),  # sysObjectID
            _oid_entry(f"{SYSTEM_BASE}.3.0", "43", str(device.sys_uptime)),
            _oid_entry(f"{SYSTEM_BASE}.4.0", "4", f"admin@{device.name.lower()}.example.com"),
            _oid_entry(f"{SYSTEM_BASE}.5.0", "4", device.name),
            _oid_entry(f"{SYSTEM_BASE}.6.0", "4", "Network Lab"),
            _oid_entry(f"{SYSTEM_BASE}.7.0", "2", "72"),  # sysServices
            _oid_entry(f"{SYSTEM_BASE}.8.0", "43", str(device.sys_uptime)),  # hrSystemUptime
            # SNMPv2-MIB::sysORLastChange
            _oid_entry("1.3.6.1.2.1.1.9.1.2.1", "6", device.sys_oid),
            _oid_entry("1.3.6.1.2.1.1.9.1.3.1", "4", device.vendor.value),
            _oid_entry("1.3.6.1.2.1.1.9.1.4.1", "43", "0"),
        ]

    # ------------------------------------------------------------------ #
    #  Interface OIDs (IF-MIB)                                            #
    # ------------------------------------------------------------------ #

    def _interface_entries(self, device: Device) -> List[OidEntry]:
        entries: List[OidEntry] = [
            _oid_entry(f"{IF_BASE}.1.0", "2", str(len(device.interfaces))),
        ]

        for iface in device.interfaces:
            i = iface.index
            entries += [
                _oid_entry(f"{IF_TABLE}.1.{i}",  "2",  str(i)),            # ifIndex
                _oid_entry(f"{IF_TABLE}.2.{i}",  "4",  iface.name),         # ifDescr
                _oid_entry(f"{IF_TABLE}.3.{i}",  "2",  "6"),                # ifType (ethernetCsmacd)
                _oid_entry(f"{IF_TABLE}.4.{i}",  "2",  "1500"),             # ifMtu
                _oid_entry(f"{IF_TABLE}.5.{i}",  "66", str(iface.speed)),   # ifSpeed (Gauge32)
                _oid_entry(f"{IF_TABLE}.6.{i}",  "4x", iface.mac_address.replace(":", "")),  # ifPhysAddress
                _oid_entry(f"{IF_TABLE}.7.{i}",  "2",  "1"),                # ifAdminStatus (1=up)
                _oid_entry(f"{IF_TABLE}.8.{i}",  "2",  str(2 if iface.connected_to_device is None else iface.oper_status)), # ifOperStatus
                _oid_entry(f"{IF_TABLE}.9.{i}",  "43", str(device.sys_uptime)), # ifLastChange
                _oid_entry(f"{IF_TABLE}.10.{i}", "41", str(iface.in_octets)),   # ifInOctets Counter32
                _oid_entry(f"{IF_TABLE}.11.{i}", "41", str(random.randint(0,9999))),  # ifInUcastPkts
                _oid_entry(f"{IF_TABLE}.12.{i}", "41", "0"),                # ifInNUcastPkts
                _oid_entry(f"{IF_TABLE}.13.{i}", "41", str(iface.in_errors // 10)), # ifInDiscards
                _oid_entry(f"{IF_TABLE}.14.{i}", "41", str(iface.in_errors)),       # ifInErrors
                _oid_entry(f"{IF_TABLE}.15.{i}", "41", "0"),                # ifInUnknownProtos
                _oid_entry(f"{IF_TABLE}.16.{i}", "41", str(iface.out_octets)),      # ifOutOctets
                _oid_entry(f"{IF_TABLE}.17.{i}", "41", str(random.randint(0,9999))), # ifOutUcastPkts
                _oid_entry(f"{IF_TABLE}.18.{i}", "41", "0"),                # ifOutNUcastPkts
                _oid_entry(f"{IF_TABLE}.19.{i}", "41", str(iface.out_errors // 10)), # ifOutDiscards
                _oid_entry(f"{IF_TABLE}.20.{i}", "41", str(iface.out_errors)),       # ifOutErrors
                _oid_entry(f"{IF_TABLE}.21.{i}", "66", str(iface.speed)),   # ifOutQLen
                _oid_entry(f"{IF_TABLE}.22.{i}", "6",  "0.0"),              # ifSpecific
            ]

        # IF-MIB extensions (ifXTable)
        ifx_base = "1.3.6.1.2.1.31.1.1.1"
        for iface in device.interfaces:
            i = iface.index
            entries += [
                _oid_entry(f"{ifx_base}.1.{i}",  "4",  iface.name),          # ifName
                _oid_entry(f"{ifx_base}.6.{i}",  "44", str(iface.in_octets * 4)),  # ifHCInOctets
                _oid_entry(f"{ifx_base}.10.{i}", "44", str(iface.out_octets * 4)), # ifHCOutOctets
                _oid_entry(f"{ifx_base}.15.{i}", "66", "1000"),               # ifHighSpeed (Mbps)
                _oid_entry(f"{ifx_base}.18.{i}", "4",  iface.name),           # ifAlias
            ]

        return entries

    # ------------------------------------------------------------------ #
    #  IP / Routing OIDs                                                   #
    # ------------------------------------------------------------------ #

    def _ip_entries(self, device: Device) -> List[OidEntry]:
        entries: List[OidEntry] = [
            _oid_entry(f"{IP_BASE}.1.0", "2", "1"),   # ipForwarding (1=forwarding for routers)
            _oid_entry(f"{IP_BASE}.2.0", "2", "64"),  # ipDefaultTTL
            _oid_entry(f"{IP_BASE}.3.0", "41", str(random.randint(1000, 99999))),  # ipInReceives
            _oid_entry(f"{IP_BASE}.5.0", "41", "0"),  # ipInDiscards
            _oid_entry(f"{IP_BASE}.7.0", "41", str(random.randint(1000, 99999))),  # ipInDelivers
            _oid_entry(f"{IP_BASE}.10.0","41", str(random.randint(1000, 99999))),  # ipOutRequests
        ]

        # ipAddrTable
        ip_addr_base = "1.3.6.1.2.1.4.20.1"
        ip = device.ip_address
        entries += [
            _oid_entry(f"{ip_addr_base}.1.{ip}", "64", ip),               # ipAdEntAddr
            _oid_entry(f"{ip_addr_base}.2.{ip}", "2",  "1"),              # ipAdEntIfIndex
            _oid_entry(f"{ip_addr_base}.3.{ip}", "64", "255.255.255.0"),  # ipAdEntNetMask
            _oid_entry(f"{ip_addr_base}.4.{ip}", "2",  "1"),              # ipAdEntBcastAddr
            _oid_entry(f"{ip_addr_base}.5.{ip}", "2",  "65535"),          # ipAdEntReasmMaxSize
        ]

        return entries

    # ------------------------------------------------------------------ #
    #  SNMP Community / Agent OIDs                                         #
    # ------------------------------------------------------------------ #

    def _snmp_entries(self, device: Device) -> List[OidEntry]:
        snmp_base = "1.3.6.1.2.1.11"
        return [
            _oid_entry(f"{snmp_base}.1.0",  "41", str(random.randint(100, 99999))),  # snmpInPkts
            _oid_entry(f"{snmp_base}.2.0",  "41", str(random.randint(100, 99999))),  # snmpOutPkts
            _oid_entry(f"{snmp_base}.3.0",  "41", "0"),   # snmpInBadVersions
            _oid_entry(f"{snmp_base}.4.0",  "41", "0"),   # snmpInBadCommunityNames
            _oid_entry(f"{snmp_base}.5.0",  "41", "0"),   # snmpInBadCommunityUses
            _oid_entry(f"{snmp_base}.30.0", "2",  "1"),   # snmpEnableAuthenTraps
        ]

    # ------------------------------------------------------------------ #
    #  Server-specific OIDs                                                #
    # ------------------------------------------------------------------ #

    def _server_entries(self, device: Device) -> List[OidEntry]:
        entries: List[OidEntry] = []

        # HOST-RESOURCES-MIB hrStorage
        # Entry 1: Physical Memory
        mem_total_kb = device.memory_total // 1024
        mem_used_kb  = device.memory_used  // 1024
        entries += [
            _oid_entry(f"{HR_STORAGE}.2.1.1.1",  "2",  "1"),                      # hrStorageIndex
            _oid_entry(f"{HR_STORAGE}.2.1.2.1",  "6",  "1.3.6.1.2.1.25.2.1.2"), # hrStorageType RAM
            _oid_entry(f"{HR_STORAGE}.2.1.3.1",  "4",  "Physical Memory"),        # hrStorageDescr
            _oid_entry(f"{HR_STORAGE}.2.1.4.1",  "2",  "1024"),                   # hrStorageAllocationUnits (1KB)
            _oid_entry(f"{HR_STORAGE}.2.1.5.1",  "2",  str(mem_total_kb)),        # hrStorageSize
            _oid_entry(f"{HR_STORAGE}.2.1.6.1",  "2",  str(mem_used_kb)),         # hrStorageUsed
        ]

        # Entry 2: Disk
        disk_total_kb = device.disk_total // 1024
        disk_used_kb  = device.disk_used  // 1024
        entries += [
            _oid_entry(f"{HR_STORAGE}.2.1.1.2",  "2",  "2"),
            _oid_entry(f"{HR_STORAGE}.2.1.2.2",  "6",  "1.3.6.1.2.1.25.2.1.4"), # hrStorageType Fixed
            _oid_entry(f"{HR_STORAGE}.2.1.3.2",  "4",  "/"),
            _oid_entry(f"{HR_STORAGE}.2.1.4.2",  "2",  "4096"),
            _oid_entry(f"{HR_STORAGE}.2.1.5.2",  "2",  str(disk_total_kb // 4)),  # in 4KB units
            _oid_entry(f"{HR_STORAGE}.2.1.6.2",  "2",  str(disk_used_kb  // 4)),
        ]

        # hrProcessorLoad (CPU usage per logical CPU)
        cpu_count = random.choice([2, 4, 8, 16])
        for cpu_i in range(1, cpu_count + 1):
            cpu_load = max(1, device.cpu_usage + random.randint(-10, 10))
            entries.append(_oid_entry(f"{HR_LOAD}.{cpu_i}", "2", str(min(100, cpu_load))))

        # UCD-SNMP-MIB (commonly used on Linux)
        cpu_user   = device.cpu_usage
        cpu_system = random.randint(2, 20)
        cpu_idle   = max(1, 100 - cpu_user - cpu_system)
        entries += [
            _oid_entry(f"{UCD_CPU}.1.0",  "2",  str(cpu_user)),
            _oid_entry(f"{UCD_CPU}.2.0",  "2",  str(cpu_system)),
            _oid_entry(f"{UCD_CPU}.3.0",  "2",  str(random.randint(0,5))),   # nice
            _oid_entry(f"{UCD_CPU}.4.0",  "2",  str(cpu_idle)),
            _oid_entry(f"{UCD_CPU}.8.0",  "2",  str(random.randint(0,5))),   # interrupt
            _oid_entry(f"{UCD_CPU}.9.0",  "2",  str(random.randint(0,3))),   # softirq
            _oid_entry(f"{UCD_CPU}.11.0", "4",  "systemStats"),
        ]

        # Memory UCD
        mem_total_bytes = device.memory_total
        mem_free        = device.memory_total - device.memory_used
        entries += [
            _oid_entry(f"{UCD_MEM}.1.0",  "2", "1"),
            _oid_entry(f"{UCD_MEM}.4.0",  "42", str(mem_total_bytes // 1024)),   # memTotalReal
            _oid_entry(f"{UCD_MEM}.5.0",  "42", str(mem_free        // 1024)),   # memAvailReal
            _oid_entry(f"{UCD_MEM}.6.0",  "42", str(mem_total_bytes // 2048)),   # memTotalFree
            _oid_entry(f"{UCD_MEM}.11.0", "42", str(device.memory_used // 1024)),# memCached
            _oid_entry(f"{UCD_MEM}.12.0", "42", str(random.randint(100,2000))),  # memBuffer
            _oid_entry(f"{UCD_MEM}.14.0", "4",  "memory"),
        ]

        # Disk usage via UCD
        disk_pct = int(device.disk_used * 100 / device.disk_total) if device.disk_total else 0
        disk_total_kb2 = device.disk_total // 1024
        disk_avail_kb  = (device.disk_total - device.disk_used) // 1024
        entries += [
            _oid_entry(f"{UCD_DISK}.1.1",  "2",  "1"),
            _oid_entry(f"{UCD_DISK}.2.1",  "4",  "/"),
            _oid_entry(f"{UCD_DISK}.5.1",  "2",  str(disk_pct)),
            _oid_entry(f"{UCD_DISK}.6.1",  "2",  str(disk_total_kb2)),
            _oid_entry(f"{UCD_DISK}.7.1",  "2",  str(disk_avail_kb)),
            _oid_entry(f"{UCD_DISK}.8.1",  "2",  "80"),   # dskMinPercent threshold
            _oid_entry(f"{UCD_DISK}.9.1",  "2",  "0"),    # dskErrorFlag
            _oid_entry(f"{UCD_DISK}.10.1", "4",  ""),     # dskErrorMsg
        ]

        return entries

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _build_neighbor_tuples(self, device: Device, topology: TopologyEngine):
        """Build (neighbor, local_port_idx, remote_port_idx) list."""
        result = []
        for neighbor in topology.get_neighbors(device.id):
            edge = topology.graph.edges[device.id, neighbor.id]
            # Edge stores the original src/dst node IDs so we can recover direction
            if edge.get("src_node") == device.id:
                local_port  = edge.get("src_iface", 0)
                remote_port = edge.get("dst_iface", 0)
            else:
                local_port  = edge.get("dst_iface", 0)
                remote_port = edge.get("src_iface", 0)
            result.append((neighbor, local_port, remote_port))
        return result

    def _build_switch_port_tuples(self, device: Device, topology: TopologyEngine):
        """Build (neighbor_device, port_index) list for MAC table."""
        result = []
        for neighbor in topology.get_neighbors(device.id):
            edge = topology.graph.edges[device.id, neighbor.id]
            if edge.get("src_node") == device.id:
                port_idx = edge.get("src_iface", 0)
            else:
                port_idx = edge.get("dst_iface", 0)
            result.append((neighbor, port_idx))
        return result

    def _write_file(self, filepath: str, entries: List[OidEntry]):
        lines = [f"{oid}|{typ}|{val}\n" for oid, typ, val in entries]
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)
