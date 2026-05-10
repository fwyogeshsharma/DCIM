"""
SNMPRec Generator - Creates .snmprec dataset files for SNMPSim.

Format per line: OID|TYPE_CODE|VALUE
Type codes:
  2  = Integer32
  4  = OctetString (UTF-8)
  4x = OctetString (hex)
  5  = Null
  6  = OID
  41 = Counter32    (0x41)
  42 = Gauge32      (0x42)
  44 = Counter64    (0x44 — unused, use 70 for Counter64)
  64 = IPAddress    (0x40)
  65 = NetworkAddress
  67 = TimeTicks    (0x43 — BER APPLICATION tag 3)
  70 = Counter64    (0x46)
  71 = Opaque       (0x47)
"""
from __future__ import annotations
import logging
import os
import random
import threading
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)   # needed so INFO-level direct-write diagnostics appear in app.log

# Per-file write locks prevent concurrent writes from the background
# _sync_snmp thread and the UI-triggered _regenerate_device_live call.
_write_locks: dict = {}
_write_locks_guard = threading.Lock()


def _get_write_lock(path: str) -> threading.Lock:
    with _write_locks_guard:
        if path not in _write_locks:
            _write_locks[path] = threading.Lock()
        return _write_locks[path]


def _replace_with_retry(src: str, dst: str, timeout_ms: int = 200) -> None:
    """Rename src over dst, falling back to in-place writes on Windows.

    On Windows, SNMPSim holds snmprec files open without FILE_SHARE_DELETE,
    so MoveFileExW(MOVEFILE_REPLACE_EXISTING) always fails with WinError 5.
    After a brief retry window we fall back to writing bytes directly into
    the live file.  Two strategies are tried in order:
      1. open(dst, 'wb')  — CREATE_ALWAYS (truncate then write)
      2. open(dst, 'r+b') — OPEN_EXISTING (overwrite then truncate)
    SNMPSim uses Python's default FILE_SHARE_WRITE sharing so at least one
    of these succeeds.  The dbm was pre-built from the same byte content so
    byte offsets remain correct for the updated live file.
    """
    import time
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_rename_exc: Optional[OSError] = None
    while True:
        try:
            os.replace(src, dst)
            return                              # fast path — atomic rename OK
        except PermissionError as exc:
            last_rename_exc = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(0.005)

    log.info("[SNMPRecGen] rename blocked (%s); trying direct write to %s",
             last_rename_exc, os.path.basename(dst))

    try:
        # Capture src mtime BEFORE reading so we can stamp dst with the same
        # mtime after the direct write.  The dbm was built with
        # target_mtime = src_mtime + 1, so dbm_mtime > dst_mtime is preserved.
        src_mtime = int(os.stat(src).st_mtime)
        data = open(src, "rb").read()
    except OSError as e:
        log.warning("[SNMPRecGen] direct write: cannot read tmp file %s: %s", src, e)
        raise last_rename_exc

    write_exc: Optional[OSError] = None
    for mode in ("wb", "r+b"):
        try:
            with open(dst, mode) as fout:
                fout.write(data)
                if mode == "r+b":
                    fout.truncate()
            # Restore src mtime on dst so dbm_mtime (src_mtime+1) > dst_mtime.
            # Without this, dst's mtime is "now" which can exceed dbm_mtime and
            # cause SNMPSim to discard our pre-built index and rebuild its own.
            try:
                os.utime(dst, (src_mtime, src_mtime))
            except OSError:
                pass
            log.info("[SNMPRecGen] direct write (mode=%s) succeeded to %s", mode, os.path.basename(dst))
            try:
                os.unlink(src)
            except OSError:
                pass
            return
        except OSError as e:
            log.info("[SNMPRecGen] direct write (mode=%s) failed for %s: %s", mode, os.path.basename(dst), e)
            write_exc = e

    log.warning("[SNMPRecGen] all write attempts failed for %s — rename: %s; write: %s",
                os.path.basename(dst), last_rename_exc, write_exc)
    raise write_exc or last_rename_exc

from core.device_manager import Device, DeviceType, Vendor, SERVER_OS_INFO
from core.lldp_generator import (generate_lldp_entries, generate_cdp_entries,
                                  LLDP_BASE, CDP_BASE)
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

# Vendor enterprise bases for environmental sensor OIDs
_RARITAN_SENSOR = "1.3.6.1.4.1.13742.6.5.5.3.1"   # Raritan PX2/DPX2 external sensor table
_GEIST_SENSOR   = "1.3.6.1.4.1.21239.5.1"           # Vertiv Geist probe tables
_APC_NETBOTZ    = "1.3.6.1.4.1.318.1.1.10.4.2.2.1"  # APC NetBotz sensor value table


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

    @staticmethod
    def snmp_address(device: Device) -> str:
        """Return the IP address used to reach this device via SNMP.

        In topologies with an OOB management network, SNMP travels through
        the management network (mgmt_ip, 192.168.x.x) not the production
        network.  Devices with no mgmt_ip have no OOB connection and are
        unreachable via SNMP from a DCIM.
        Falls back to ip_address for topologies without a management layer.
        """
        return device.mgmt_ip if device.mgmt_ip else device.ip_address

    def generate_all(self, topology: TopologyEngine) -> List[str]:
        """Generate .snmprec file for every device reachable via SNMP.

        When the topology has an OOB management layer, only devices with a
        mgmt_ip (i.e. connected to an OOB switch) get a dataset file.
        Devices on the production or power network only — with no OOB
        connection — have no management path and are skipped.
        """
        all_devices = list(topology.get_all_devices())
        has_mgmt_layer = any(d.mgmt_ip for d in all_devices)
        generated = []
        skipped = 0
        for device in all_devices:
            if has_mgmt_layer and not device.mgmt_ip:
                skipped += 1
                continue   # no OOB connection → unreachable via SNMP
            filepath = self.generate_device(device, topology)
            generated.append(filepath)
        if skipped:
            import logging as _log
            _log.getLogger(__name__).info(
                "[SNMPRecGen] Skipped %d device(s) with no mgmt_ip "
                "(no OOB connection → not reachable via SNMP)", skipped)
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
            # Production LLDP/CDP — only production-layer neighbors.
            # The management port (OOB link) is a separate NIC and doesn't
            # appear in the production LLDP table.
            prod_tuples = [(n, lp, rp) for n, lp, rp, lyr in neighbor_tuples
                           if lyr == "production"]
            entries += generate_lldp_entries(device, prod_tuples)
            if device.vendor == Vendor.CISCO_SYSTEMS:
                entries += generate_cdp_entries(device, prod_tuples)
        elif device.device_type == DeviceType.OOB_SWITCH:
            # OOB switch LLDP shows every device connected to its management ports.
            mgmt_tuples = [(n, lp, rp) for n, lp, rp, lyr in neighbor_tuples
                           if lyr == "management"]
            if mgmt_tuples:
                entries += generate_lldp_entries(device, mgmt_tuples)
        else:
            # Servers — generate LLDP only for peer server (non-network) production links.
            peer_tuples = [(n, lp, rp) for n, lp, rp, lyr in neighbor_tuples
                           if lyr == "production" and n.device_type not in _NETWORK_TYPES]
            if peer_tuples:
                entries += generate_lldp_entries(device, peer_tuples)

        if device.device_type == DeviceType.SWITCH:
            neighbor_port_tuples = self._build_switch_port_tuples(device, topology)
            entries += generate_mac_table(device, neighbor_port_tuples)
            entries += generate_stp_entries(device)

        if device.device_type == DeviceType.SERVER:
            entries += self._server_entries(device)

        if device.device_type == DeviceType.SENSOR:
            entries += self._sensor_entries(device)

        # Sort and write
        # Output layout:  datasets/snmp/<snmp_addr>.snmprec
        # SNMPSim routes community "<snmp_addr>" → this file.
        # snmp_addr = mgmt_ip when the device has an OOB connection,
        # otherwise ip_address (legacy / no-management-layer topologies).
        # Configure your NMS/DCIM to poll the mgmt_ip with community=mgmt_ip.
        entries = _sort_oids(entries)
        snmp_addr = self.snmp_address(device)
        filepath = str(self.output_dir / f"{snmp_addr}.snmprec")
        self._atomic_write(filepath, entries)
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
        filepath = self.output_dir / f"{self.snmp_address(device)}.snmprec"
        if not filepath.exists():
            return str(filepath)

        # Serialize all reads and writes for this file so that a stale
        # background-thread read cannot overwrite a fresher main-thread write.
        # updates is computed INSIDE the lock so the background thread always
        # reads the current oper_status after break_link() has updated it —
        # computing it outside would let a pre-break read slip past the lock.
        lock = _get_write_lock(str(filepath.resolve()))
        with lock:
            # Build the set of OIDs whose values change each tick.
            updates: dict = {}

            # System uptime (centiseconds)
            updates[f"{SYSTEM_BASE}.3.0"] = ("67", str(device.sys_uptime))
            updates[f"{SYSTEM_BASE}.8.0"] = ("67", str(device.sys_uptime))

            # Interface counters
            ifx_base = "1.3.6.1.2.1.31.1.1.1"
            for iface in device.interfaces:
                i = iface.index
                in_pkts  = iface.in_octets  // 1500
                out_pkts = iface.out_octets // 1500
                oper = 2 if iface.connected_to_device is None else iface.oper_status
                updates[f"{IF_TABLE}.8.{i}"]  = ("2",  str(oper))
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

            # Sensor environmental readings
            if device.device_type == DeviceType.SENSOR:
                inlet_t10   = int(round(device.inlet_temp * 10))
                humid_t10   = int(round(device.humidity   * 10))
                dewpt_t10   = int(round(device.dewpoint   * 10))
                airflow_t10 = int(round(device.airflow    * 10))
                if device.vendor == Vendor.RARITAN:
                    updates[f"{_RARITAN_SENSOR}.4.1.1"] = ("2", str(inlet_t10))
                    updates[f"{_RARITAN_SENSOR}.4.1.2"] = ("2", str(humid_t10))
                elif device.vendor == Vendor.VERTIV:
                    updates[f"{_GEIST_SENSOR}.4.1.4.1"] = ("2", str(inlet_t10))
                    updates[f"{_GEIST_SENSOR}.5.1.4.1"] = ("2", str(humid_t10))
                    updates[f"{_GEIST_SENSOR}.6.1.4.1"] = ("2", str(dewpt_t10))
                elif device.vendor == Vendor.APC:
                    updates[f"{_APC_NETBOTZ}.10.1"] = ("2", str(inlet_t10))
                    updates[f"{_APC_NETBOTZ}.10.2"] = ("2", str(humid_t10 // 10))
                    updates[f"{_APC_NETBOTZ}.10.3"] = ("2", str(airflow_t10))

            # Read existing file, replace matching OID lines.
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
                new_content = "\n".join(patched)
            except OSError as e:
                log.warning("[SNMPRecGen] patch_metrics read failed for %s: %s", filepath, e)
                return str(filepath)

            # Atomic write: temp file → pre-build index → rename.
            #
            # Sequence matters: the index must be in place (with mtime > snmprec
            # mtime) BEFORE the snmprec rename becomes visible to SNMPSim.  That
            # way, when SNMPSim detects the file change it immediately finds a
            # fresh index and skips its own blocking internal rebuild entirely.
            import tempfile as _tempfile
            tmp_snmprec = None
            try:
                with _tempfile.NamedTemporaryFile(
                        "w", dir=str(self.output_dir), suffix=".snmprec.tmp",
                        encoding="utf-8", delete=False) as f:
                    f.write(new_content)
                    tmp_snmprec = f.name

                snmprec_mtime = int(os.stat(tmp_snmprec).st_mtime)

                # Pre-build index reading from the temp file but keyed to the
                # canonical (live) path so SNMPSim's lookup finds it.
                self._reindex(tmp_snmprec,
                              canonical_path=str(filepath.resolve()),
                              target_mtime=snmprec_mtime + 1)

                # Index is fresh; now atomically expose the new snmprec.
                _replace_with_retry(tmp_snmprec, str(filepath))
            except OSError as e:
                log.warning("[SNMPRecGen] patch_metrics write failed for %s: %s", filepath, e)
                if tmp_snmprec:
                    try:
                        os.unlink(tmp_snmprec)
                    except OSError:
                        pass
                return str(filepath)

        return str(filepath)

    def patch_lldp(self, device: Device, topology: TopologyEngine) -> str:
        """
        Patch only the LLDP/CDP neighbor section of an existing .snmprec file.

        Reads the current file, strips all LLDP/CDP OID lines, regenerates
        them from the current topology (broken links excluded), re-sorts, and
        writes atomically.  Uses the same safe OSError-skip pattern as
        patch_metrics — never calls _write_file, so SNMPSim is not disrupted.
        """
        filepath = self.output_dir / f"{self.snmp_address(device)}.snmprec"
        if not filepath.exists():
            return str(filepath)

        lock = _get_write_lock(str(filepath.resolve()))
        with lock:
            try:
                lines = filepath.read_text(encoding="utf-8").splitlines()
            except OSError as e:
                log.warning("[SNMPRecGen] patch_lldp read failed for %s: %s", filepath, e)
                return str(filepath)

            # Strip all existing LLDP (1.0.8802.1.1.2.1.*) and CDP entries
            _lldp_pfx = LLDP_BASE + "."
            _cdp_pfx  = CDP_BASE  + "."
            kept = [l for l in lines
                    if not (l.startswith(_lldp_pfx) or l.startswith(_cdp_pfx))]

            # Regenerate LLDP/CDP entries with broken links excluded
            neighbor_tuples = self._build_neighbor_tuples(device, topology)
            _NETWORK_TYPES = (DeviceType.ROUTER, DeviceType.SWITCH,
                              DeviceType.FIREWALL, DeviceType.LOAD_BALANCER)
            new_entries: List[OidEntry] = []
            if device.device_type in _NETWORK_TYPES:
                prod_tuples = [(n, lp, rp) for n, lp, rp, lyr in neighbor_tuples
                               if lyr == "production"]
                new_entries += generate_lldp_entries(device, prod_tuples)
                if device.vendor == Vendor.CISCO_SYSTEMS:
                    new_entries += generate_cdp_entries(device, prod_tuples)
            elif device.device_type == DeviceType.OOB_SWITCH:
                mgmt_tuples = [(n, lp, rp) for n, lp, rp, lyr in neighbor_tuples
                               if lyr == "management"]
                if mgmt_tuples:
                    new_entries += generate_lldp_entries(device, mgmt_tuples)
            else:
                peer_tuples = [(n, lp, rp) for n, lp, rp, lyr in neighbor_tuples
                               if lyr == "production"
                               and n.device_type not in _NETWORK_TYPES]
                if peer_tuples:
                    new_entries += generate_lldp_entries(device, peer_tuples)

            new_lines = [f"{oid}|{typ}|{val}" for oid, typ, val in new_entries]

            # Merge and sort numerically by OID
            def _oid_key(line: str):
                oid = line.split("|")[0] if "|" in line else line
                try:
                    return tuple(int(x) for x in oid.split("."))
                except ValueError:
                    return (0,)

            all_lines = kept + new_lines
            all_lines.sort(key=_oid_key)
            new_content = "\n".join(all_lines)

            # Atomic write — safe OSError fallback: skip, don't corrupt
            import tempfile as _tempfile
            tmp = None
            try:
                with _tempfile.NamedTemporaryFile(
                        "w", dir=str(self.output_dir), suffix=".snmprec.tmp",
                        encoding="utf-8", delete=False) as f:
                    f.write(new_content)
                    tmp = f.name
                mtime = int(os.stat(tmp).st_mtime)
                self._reindex(tmp, canonical_path=str(filepath.resolve()), target_mtime=mtime + 1)
                _replace_with_retry(tmp, str(filepath))
            except OSError as e:
                log.warning("[SNMPRecGen] patch_lldp write failed for %s: %s", filepath, e)
                if tmp:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
        return str(filepath)

    # ------------------------------------------------------------------ #
    #  System OIDs                                                         #
    # ------------------------------------------------------------------ #

    def _system_entries(self, device: Device) -> List[OidEntry]:
        return [
            _oid_entry(f"{SYSTEM_BASE}.1.0", "4", device.sys_descr),
            _oid_entry(f"{SYSTEM_BASE}.2.0", "6", device.sys_oid),  # sysObjectID
            _oid_entry(f"{SYSTEM_BASE}.3.0", "67", str(device.sys_uptime)),
            _oid_entry(f"{SYSTEM_BASE}.4.0", "4", f"admin@{device.name.lower()}.example.com"),
            _oid_entry(f"{SYSTEM_BASE}.5.0", "4", device.name),
            _oid_entry(f"{SYSTEM_BASE}.6.0", "4", device.sys_location),
            _oid_entry(f"{SYSTEM_BASE}.7.0", "2", "72"),  # sysServices
            _oid_entry(f"{SYSTEM_BASE}.8.0", "67", str(device.sys_uptime)),  # hrSystemUptime
            # SNMPv2-MIB::sysORLastChange
            _oid_entry("1.3.6.1.2.1.1.9.1.2.1", "6", device.sys_oid),
            _oid_entry("1.3.6.1.2.1.1.9.1.3.1", "4", device.vendor.value),
            _oid_entry("1.3.6.1.2.1.1.9.1.4.1", "67", "0"),
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
                _oid_entry(f"{IF_TABLE}.5.{i}",  "66", str(min(iface.speed, 4294967295))),   # ifSpeed (Gauge32, capped per RFC 2863)
                _oid_entry(f"{IF_TABLE}.6.{i}",  "4x", iface.mac_address.replace(":", "")),  # ifPhysAddress
                _oid_entry(f"{IF_TABLE}.7.{i}",  "2",  "1"),                # ifAdminStatus (1=up)
                _oid_entry(f"{IF_TABLE}.8.{i}",  "2",  str(2 if iface.connected_to_device is None else iface.oper_status)), # ifOperStatus
                _oid_entry(f"{IF_TABLE}.9.{i}",  "67", str(device.sys_uptime)), # ifLastChange
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
                _oid_entry(f"{IF_TABLE}.21.{i}", "66", "0"),                  # ifOutQLen (Gauge32)
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

        # ipAddrTable — use mgmt_ip for management-only devices (ip_address is empty)
        ip_addr_base = "1.3.6.1.2.1.4.20.1"
        ip = device.ip_address or device.mgmt_ip
        if ip:
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
    #  Environmental sensor OIDs (vendor-specific MIB branches)           #
    # ------------------------------------------------------------------ #

    def _sensor_entries(self, device: Device) -> List[OidEntry]:
        """Return vendor-specific OIDs for temp, humidity, dewpoint, and airflow."""
        entries: List[OidEntry] = []
        inlet_t10   = int(round(device.inlet_temp * 10))
        humid_t10   = int(round(device.humidity   * 10))
        dewpt_t10   = int(round(device.dewpoint   * 10))
        airflow_t10 = int(round(device.airflow    * 10))

        if device.vendor == Vendor.RARITAN:
            # Raritan PX2/DPX2 external sensor table (RARITAN-PX2-MIB)
            # Two sensor slots: index 1 = temperature, index 2 = humidity
            b = _RARITAN_SENSOR
            entries += [
                _oid_entry(f"{b}.2.1.1", "2",  "1"),              # sensorIndex
                _oid_entry(f"{b}.3.1.1", "2",  "10"),             # sensorType=temperature
                _oid_entry(f"{b}.4.1.1", "2",  str(inlet_t10)),   # value ×10 °C
                _oid_entry(f"{b}.5.1.1", "2",  "4"),              # sensorState=normal
                _oid_entry(f"{b}.2.1.2", "2",  "2"),              # sensorIndex
                _oid_entry(f"{b}.3.1.2", "2",  "11"),             # sensorType=humidity
                _oid_entry(f"{b}.4.1.2", "2",  str(humid_t10)),   # value ×10 %
                _oid_entry(f"{b}.5.1.2", "2",  "4"),              # sensorState=normal
            ]

        elif device.vendor == Vendor.VERTIV:
            # Vertiv Geist GTHD (GEIST-MIB enterprise 21239.5.1)
            # Separate probe tables: .4=temperature, .5=humidity, .6=dewpoint
            b = _GEIST_SENSOR
            entries += [
                _oid_entry(f"{b}.4.1.1.1", "2", "1"),             # tempProbeIndex
                _oid_entry(f"{b}.4.1.2.1", "4", "Inlet"),         # tempProbeLabel
                _oid_entry(f"{b}.4.1.4.1", "2", str(inlet_t10)),  # tempProbeValue ×10 °C
                _oid_entry(f"{b}.5.1.1.1", "2", "1"),             # humProbeIndex
                _oid_entry(f"{b}.5.1.2.1", "4", "Inlet"),         # humProbeLabel
                _oid_entry(f"{b}.5.1.4.1", "2", str(humid_t10)),  # humProbeValue ×10 %
                _oid_entry(f"{b}.6.1.1.1", "2", "1"),             # dewProbeIndex
                _oid_entry(f"{b}.6.1.2.1", "4", "Inlet"),         # dewProbeLabel
                _oid_entry(f"{b}.6.1.4.1", "2", str(dewpt_t10)),  # dewProbeValue ×10 °C
            ]

        elif device.vendor == Vendor.APC:
            # APC NetBotz 250 (APC-NETBOTZ-MIB enterprise 318.1.1.10.4.2.2.1)
            # Three rows: 1=temperature, 2=humidity, 3=airflow
            b = _APC_NETBOTZ
            entries += [
                _oid_entry(f"{b}.2.1",  "4", "Temperature"),        # label
                _oid_entry(f"{b}.10.1", "2", str(inlet_t10)),       # value ×10 °C
                _oid_entry(f"{b}.11.1", "2", "4"),                  # state=normal
                _oid_entry(f"{b}.2.2",  "4", "Humidity"),           # label
                _oid_entry(f"{b}.10.2", "2", str(humid_t10 // 10)), # value % (integer)
                _oid_entry(f"{b}.11.2", "2", "4"),                  # state=normal
                _oid_entry(f"{b}.2.3",  "4", "Airflow"),            # label
                _oid_entry(f"{b}.10.3", "2", str(airflow_t10)),     # value ×10 m/s
                _oid_entry(f"{b}.11.3", "2", "4"),                  # state=normal
            ]

        return entries

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _build_neighbor_tuples(self, device: Device, topology: TopologyEngine):
        """Build (neighbor, local_port_idx, remote_port_idx, layer) list.

        MultiGraph-aware: iterates all edges per neighbor pair so that a device
        with both a power and a management edge to the same OOB switch produces
        one tuple per layer (the caller filters by layer).
        """
        result = []
        for neighbor in topology.get_neighbors(device.id):
            # graph[u][v] → {edge_key: {attr_dict}} for MultiGraph
            for _key, edge in topology.graph[device.id][neighbor.id].items():
                if edge.get("broken", False):
                    continue
                if edge.get("src_node") == device.id:
                    local_port  = edge.get("src_iface", 0)
                    remote_port = edge.get("dst_iface", 0)
                else:
                    local_port  = edge.get("dst_iface", 0)
                    remote_port = edge.get("src_iface", 0)
                layer = edge.get("layer", "production")
                result.append((neighbor, local_port, remote_port, layer))
        return result

    def _build_switch_port_tuples(self, device: Device, topology: TopologyEngine):
        """Build (neighbor_device, port_index) list for MAC table (production layer only)."""
        result = []
        for neighbor in topology.get_neighbors(device.id):
            for _key, edge in topology.graph[device.id][neighbor.id].items():
                if edge.get("layer", "production") != "production":
                    continue  # MAC table tracks production links only
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

    def _reindex(self, snmprec_path: str, canonical_path: str = None,
                 target_mtime: int = None) -> None:
        """
        Write a fresh dbm.dumb index (.dat + .dir) for *snmprec_path* in a
        single O(n) pass so SNMPSim's freshness check passes and it skips its
        own slow internal rebuild.

        canonical_path: the "real" snmprec path whose index entry SNMPSim will
                        look up (defaults to snmprec_path).  Pass this when
                        reading from a temp file but writing the index for the
                        live path so the index is in place before the atomic
                        rename makes the new snmprec visible.
        target_mtime:   explicit mtime (integer seconds) to stamp on the index
                        files.  Defaults to snmprec_path mtime + 1.

        dbm.dumb format (matches CPython's dbm/dumb.py exactly):
          .dat  – raw UTF-8 value bytes, concatenated
          .dir  – text lines: "%r, %r\\n" % (key_str, (pos, siz))
                  where key_str is the OID as a Latin-1 string
                  (first char must be ' or " so whichdb() recognises the file)
        """
        index_for = canonical_path if canonical_path is not None else snmprec_path
        cache_dir = self._get_cache_dir()
        if cache_dir is None:
            return

        os.makedirs(cache_dir, exist_ok=True)
        db_base  = self._db_path(index_for, cache_dir)
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
            if target_mtime is not None:
                future = target_mtime
            else:
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

    def _atomic_write(self, filepath: str, entries: List[OidEntry]) -> None:
        """Write entries atomically: temp file → pre-build dbm index → rename.

        SNMPSim reads the file via a cached dbm index. Writing directly
        (truncate + fill) creates a window where SNMPSim reads a zero-byte
        file, rebuilds an empty index in-process, and crashes — taking every
        simulated device offline. The atomic pattern guarantees SNMPSim always
        sees a complete file with a fresh index already in place.
        """
        lines = [f"{oid}|{typ}|{val}\n" for oid, typ, val in entries]
        tmp = None
        try:
            import tempfile as _tmp
            with _tmp.NamedTemporaryFile(
                    "w", dir=_tmp.gettempdir(), suffix=".snmprec.tmp",
                    encoding="utf-8", delete=False) as f:
                f.writelines(lines)
                tmp = f.name
            target_mtime = int(os.stat(tmp).st_mtime) + 1
            self._reindex(tmp, canonical_path=filepath, target_mtime=target_mtime)
            _replace_with_retry(tmp, filepath)
        except Exception:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            self._write_file(filepath, entries)  # fallback

    def _write_file(self, filepath: str, entries: List[OidEntry]):
        lines = [f"{oid}|{typ}|{val}\n" for oid, typ, val in entries]
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)
