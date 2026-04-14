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
from typing import List, Optional, Tuple

from core.device_manager import Device, DeviceType, Vendor, SERVER_OS_INFO
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
HR_DEVICE    = "1.3.6.1.2.1.25"
HR_STORAGE   = "1.3.6.1.2.1.25.2"
HR_PROC      = "1.3.6.1.2.1.25.3"
HR_LOAD      = "1.3.6.1.2.1.25.3.3.1.2"   # hrProcessorLoad
HR_SW_INST   = "1.3.6.1.2.1.25.6.3.1"     # hrSWInstalledTable

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
    def __init__(self, output_dir: str = "datasets/snmp"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir: Optional[str] = None   # lazily resolved from snmpsim.confdir

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

        neighbor_tuples = self._build_neighbor_tuples(device, topology)
        _NETWORK_TYPES = (DeviceType.ROUTER, DeviceType.SWITCH,
                          DeviceType.FIREWALL, DeviceType.LOAD_BALANCER)
        if device.device_type in _NETWORK_TYPES:
            entries += generate_lldp_entries(device, neighbor_tuples)
            if device.vendor == Vendor.CISCO_SYSTEMS:
                entries += generate_cdp_entries(device, neighbor_tuples)
        else:
            # Pure servers only appear in their neighbors' LLDP tables.
            # Generate LLDP only for peer server connections so topology
            # discovery can find server-to-server links.
            peer_tuples = [
                (n, lp, rp) for n, lp, rp in neighbor_tuples
                if n.device_type not in _NETWORK_TYPES
            ]
            if peer_tuples:
                entries += generate_lldp_entries(device, peer_tuples)

        if device.device_type == DeviceType.SWITCH:
            neighbor_port_tuples = self._build_switch_port_tuples(device, topology)
            entries += generate_mac_table(device, neighbor_port_tuples)
            entries += generate_stp_entries(device)

        if device.device_type == DeviceType.SERVER:
            entries += self._server_entries(device)

        # Sort and write
        # Output layout:  datasets/snmp/<device_ip>.snmprec
        # SNMPSim routes community "<device_ip>" → this file, so set the
        # SNMP community string in openDCIM/your NMS to the device's IP address.
        entries = _sort_oids(entries)
        filepath = str(self.output_dir / f"{device.ip_address}.snmprec")
        self._write_file(filepath, entries)
        return filepath

    def patch_metrics(self, device: Device) -> str:
        """
        Patch only the dynamic metric OIDs in an existing .snmprec file.

        Called on every StateStore tick instead of generate_device() so that
        LLDP / CDP / MAC / STP data (which never changes at runtime) is left
        untouched.  Falls back to a no-op if the file does not exist — the
        full generate_device() pass at dataset generation time is the
        authoritative write.

        No topology reference is needed because only per-device counters and
        uptime are updated.
        """
        filepath = self.output_dir / f"{device.ip_address}.snmprec"
        if not filepath.exists():
            return str(filepath)

        # Build the set of OIDs whose values change each tick.
        updates: dict = {}

        # System uptime (centiseconds)
        updates[f"{SYSTEM_BASE}.3.0"] = ("43", str(device.sys_uptime))
        updates[f"{SYSTEM_BASE}.8.0"] = ("43", str(device.sys_uptime))

        # Interface counters
        ifx_base = "1.3.6.1.2.1.31.1.1.1"
        for iface in device.interfaces:
            i = iface.index
            in_pkts  = iface.in_octets  // 1500
            out_pkts = iface.out_octets // 1500
            updates[f"{IF_TABLE}.10.{i}"] = ("41", str(iface.in_octets))
            updates[f"{IF_TABLE}.11.{i}"] = ("41", str(in_pkts))
            updates[f"{IF_TABLE}.13.{i}"] = ("41", str(iface.in_errors // 10))
            updates[f"{IF_TABLE}.14.{i}"] = ("41", str(iface.in_errors))
            updates[f"{IF_TABLE}.16.{i}"] = ("41", str(iface.out_octets))
            updates[f"{IF_TABLE}.17.{i}"] = ("41", str(out_pkts))
            updates[f"{IF_TABLE}.19.{i}"] = ("41", str(iface.out_errors // 10))
            updates[f"{IF_TABLE}.20.{i}"] = ("41", str(iface.out_errors))
            updates[f"{ifx_base}.6.{i}"]  = ("44", str(iface.in_octets  * 4))
            updates[f"{ifx_base}.10.{i}"] = ("44", str(iface.out_octets * 4))

        # Server-only: HR-MIB storage + processor load, UCD CPU/MEM/disk
        if device.device_type == DeviceType.SERVER:
            mem_total_kb  = device.memory_total // 1024
            mem_used_kb   = device.memory_used  // 1024
            mem_free      = device.memory_total - device.memory_used
            disk_total_kb = device.disk_total   // 1024
            disk_used_kb  = device.disk_used    // 1024
            disk_avail_kb = (device.disk_total  - device.disk_used) // 1024
            disk_pct = int(device.disk_used * 100 / device.disk_total) if device.disk_total else 0

            updates[f"{HR_STORAGE}.2.1.5.1"] = ("2",  str(mem_total_kb))
            updates[f"{HR_STORAGE}.2.1.6.1"] = ("2",  str(mem_used_kb))
            updates[f"{HR_STORAGE}.2.1.5.2"] = ("2",  str(disk_total_kb // 4))
            updates[f"{HR_STORAGE}.2.1.6.2"] = ("2",  str(disk_used_kb  // 4))

            cpu_user   = device.cpu_usage
            cpu_system = random.randint(2, 20)
            cpu_idle   = max(1, 100 - cpu_user - cpu_system)
            updates[f"{UCD_CPU}.1.0"]  = ("2",  str(cpu_user))
            updates[f"{UCD_CPU}.2.0"]  = ("2",  str(cpu_system))
            updates[f"{UCD_CPU}.4.0"]  = ("2",  str(cpu_idle))
            updates[f"{UCD_MEM}.4.0"]  = ("42", str(device.memory_total // 1024))
            updates[f"{UCD_MEM}.5.0"]  = ("42", str(mem_free // 1024))
            updates[f"{UCD_MEM}.11.0"] = ("42", str(device.memory_used  // 1024))
            updates[f"{UCD_DISK}.5.1"] = ("2",  str(disk_pct))
            updates[f"{UCD_DISK}.6.1"] = ("2",  str(disk_total_kb))
            updates[f"{UCD_DISK}.7.1"] = ("2",  str(disk_avail_kb))

        # Read existing file, replace matching OID lines, write back.
        try:
            lines = filepath.read_text(encoding="utf-8").splitlines()
            patched = []
            for line in lines:
                sep = line.find("|")
                if sep != -1:
                    oid = line[:sep]
                    if oid in updates:
                        typ, val = updates[oid]
                        patched.append(f"{oid}|{typ}|{val}")
                        continue
                patched.append(line)
            filepath.write_text("\n".join(patched), encoding="utf-8")
        except OSError:
            return str(filepath)  # file temporarily locked — skip this tick

        # Immediately rebuild the dbm.dumb index so SNMPSim finds a fresh
        # index and skips its internal O(n²) rebuild on the next request.
        self._reindex(str(filepath))

        return str(filepath)

    # ------------------------------------------------------------------ #
    #  System OIDs                                                         #
    # ------------------------------------------------------------------ #

    def _system_entries(self, device: Device) -> List[OidEntry]:
        return [
            _oid_entry(f"{SYSTEM_BASE}.1.0", "4", device.sys_descr),
            _oid_entry(f"{SYSTEM_BASE}.2.0", "6", device.sys_oid),  # sysObjectID
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

        # HOST-RESOURCES-MIB hrSWInstalled — OS entry (index 1 = operating system)
        os_name, os_ver = SERVER_OS_INFO.get(device.vendor, ("Linux", "5.15"))
        os_full = f"{os_name} {os_ver}"
        entries += [
            _oid_entry(f"{HR_SW_INST}.1.1", "2",  "1"),          # hrSWInstalledIndex
            _oid_entry(f"{HR_SW_INST}.2.1", "4",  os_full),      # hrSWInstalledName
            _oid_entry(f"{HR_SW_INST}.3.1", "6",  "0.0"),        # hrSWInstalledID
            _oid_entry(f"{HR_SW_INST}.4.1", "2",  "2"),          # hrSWInstalledType (2=operatingSystem)
            _oid_entry(f"{HR_SW_INST}.5.1", "4",  "2024-1-1,0:0:0.0"),  # hrSWInstalledDate
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

    # ------------------------------------------------------------------ #
    #  Fast dbm.dumb index writer (prevents SNMPSim internal rebuild)     #
    # ------------------------------------------------------------------ #

    def _get_cache_dir(self) -> Optional[str]:
        """Return SNMPSim's index cache directory, or None if unavailable."""
        if self._cache_dir is not None:
            return self._cache_dir
        try:
            from snmpsim import confdir as _confdir
            self._cache_dir = _confdir.cache
            return self._cache_dir
        except Exception:
            return None

    def _db_path(self, snmprec_path: str, cache_dir: str) -> str:
        """Replicate SNMPSim's index path computation."""
        p = snmprec_path[: snmprec_path.rindex(".")]   # strip extension
        p += ".dbm"
        p = os.path.splitdrive(p)[1].replace(os.sep, "_")
        return os.path.join(cache_dir, p)

    def _reindex(self, snmprec_path: str) -> None:
        """
        Write a fresh dbm.dumb index (.dat + .dir) for *snmprec_path* in a
        single O(n) pass so SNMPSim's freshness check passes and it skips its
        own slow internal rebuild.

        dbm.dumb format (matches CPython's dbm/dumb.py exactly):
          .dat  – raw UTF-8 value bytes, concatenated
          .dir  – text lines: "%r, %r\\n" % (key_str, (pos, siz))
                  where key_str is the OID as a Latin-1 string
                  (first char must be ' or " so whichdb() recognises the file)
        """
        cache_dir = self._get_cache_dir()
        if cache_dir is None:
            return

        os.makedirs(cache_dir, exist_ok=True)
        db_base  = self._db_path(snmprec_path, cache_dir)
        dat_path = db_base + ".dat"
        dir_path = db_base + ".dir"

        entries: list = []   # [(key_bytes, val_bytes)]
        offset      = 0
        prev_offset = -1

        try:
            with open(snmprec_path, "rb") as fh:
                for raw_line in fh:
                    line    = raw_line.decode("utf-8", errors="replace")
                    stripped = line.rstrip("\r\n")
                    if not stripped or stripped.startswith("#"):
                        offset += len(raw_line)
                        continue

                    parts = stripped.split("|", 2)
                    if len(parts) < 3:
                        offset += len(raw_line)
                        continue

                    oid, tag, _val = parts
                    is_subtree = 1 if tag.startswith(":") else 0
                    val_str = "%d,%d,%d" % (offset, is_subtree, prev_offset)

                    entries.append((oid.encode("utf-8"), val_str.encode("utf-8")))

                    if is_subtree:
                        prev_offset = offset
                    else:
                        prev_offset = -1

                    offset += len(raw_line)

            # "last" sentinel — matches snmpsim's db["last"] entry
            last_val = "%d,%d,%d" % (offset, 0, prev_offset)
            entries.append((b"last", last_val.encode("utf-8")))
        except Exception:
            return

        dat_io: bytearray = bytearray()
        dir_lines: list   = []

        for key_b, val_b in entries:
            pos = len(dat_io)
            siz = len(val_b)
            dat_io += val_b
            key_str = key_b.decode("latin-1")
            dir_lines.append("%r, %r\n" % (key_str, (pos, siz)))

        try:
            import tempfile
            snmprec_sec = int(os.stat(snmprec_path)[8])
            future = snmprec_sec + 1

            # Write to temp files then atomically rename over the live index
            # files so SNMPSim never reads a partially-written index mid-walk.
            with tempfile.NamedTemporaryFile("wb", dir=cache_dir, delete=False) as f:
                f.write(dat_io)
                tmp_dat = f.name
            with tempfile.NamedTemporaryFile("w", dir=cache_dir,
                                             encoding="latin-1", delete=False) as f:
                f.write("".join(dir_lines))
                tmp_dir = f.name

            os.utime(tmp_dat, (future, future))
            os.utime(tmp_dir, (future, future))
            os.replace(tmp_dat, dat_path)   # atomic on both Windows and POSIX
            os.replace(tmp_dir, dir_path)
        except Exception:
            pass  # non-fatal — SNMPSim will rebuild on next request

    def _write_file(self, filepath: str, entries: List[OidEntry]):
        lines = [f"{oid}|{typ}|{val}\n" for oid, typ, val in entries]
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)
