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

# On Windows SNMPSim holds every .snmprec open, so the atomic rename ALWAYS
# fails and we fall back to direct writes on every tick. Log that fallback
# once at INFO (so it's discoverable) and stay quiet (DEBUG) thereafter —
# otherwise each tick spams two INFO lines per device forever.
_logged_rename_fallback = False


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
      1. open(dst, 'r+b') — OPEN_EXISTING (overwrite then truncate, NO empty-file window)
      2. open(dst, 'wb')  — CREATE_ALWAYS (truncate then write, last resort)
    r+b is tried first because wb truncates the file to zero before writing,
    creating a brief window where SNMPSim reads an empty file and returns no
    response for any OID query.  r+b overwrites in place without truncating,
    so the file always contains valid (possibly briefly stale) content.
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

    global _logged_rename_fallback
    if not _logged_rename_fallback:
        _logged_rename_fallback = True
        log.info("[SNMPRecGen] rename blocked (%s) — SNMPSim holds the file open; "
                 "switching to in-place writes for the rest of this run (further "
                 "occurrences logged at DEBUG). First file: %s",
                 last_rename_exc, os.path.basename(dst))
    else:
        log.debug("[SNMPRecGen] rename blocked (%s); direct write to %s",
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
    for mode in ("r+b", "wb"):
        try:
            with open(dst, mode) as fout:
                fout.write(data)
                fout.truncate()  # always truncate: r+b removes old tail, wb is already truncated
            # Restore src mtime on dst so dbm_mtime (src_mtime+1) > dst_mtime.
            # Without this, dst's mtime is "now" which can exceed dbm_mtime and
            # cause SNMPSim to discard our pre-built index and rebuild its own.
            try:
                os.utime(dst, (src_mtime, src_mtime))
            except OSError:
                pass
            log.debug("[SNMPRecGen] direct write (mode=%s) succeeded to %s", mode, os.path.basename(dst))
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

from core import dataset_fingerprint as _fingerprint
from core import vendor_oids as _vendor_oids
from core.device_manager import Device, DeviceType, Vendor, SERVER_OS_INFO
from core.lldp_generator import (generate_lldp_entries, generate_cdp_entries,
                                  LLDP_BASE, CDP_BASE)
from core.mac_table_generator import generate_mac_table, generate_stp_entries
from core.topology_engine import TopologyEngine


OidEntry = Tuple[str, str, str]

# Device types with NO SNMP agent. RPP is a passive panel; chiller, pump,
# cooling tower and valve are BACnet/Modbus-native plant equipment — real
# units carry no SNMP card (the BMS gateways their points). CRAH and CDU
# stay SNMP-capable: real ones ship native network/comm cards.
_NO_SNMP_TYPES = frozenset({
    DeviceType.RPP, DeviceType.CHILLER, DeviceType.PUMP,
    DeviceType.COOLING_TOWER, DeviceType.VALVE,
})

# Server BMC SNMP agent — enterprise subtree (project base 99999, .26 = BMC;
# .20–.25 belong to the chiller-plant devices: chiller, pump, cooling tower,
# valve, CRAH, CDU)
BMC_BASE = "1.3.6.1.4.1.99999.26"
# BMC boot anchors {device name: epoch} — BMC uptime is independent of the
# host: it keeps counting while the chassis is Off, resets on app restart
# (equivalent to a BMC reboot).
_BMC_BOOT: dict = {}

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
_ENTITY_SENSOR  = "1.3.6.1.2.1.99.1.1.1"          # ENTITY-SENSOR-MIB entPhySensorEntry
_RARITAN_SENSOR = "1.3.6.1.4.1.13742.6.5.5.3.1"   # Raritan PX2/DPX2 external sensor table
_GEIST_SENSOR   = "1.3.6.1.4.1.21239.5.1"           # Vertiv Geist probe tables
_APC_NETBOTZ    = "1.3.6.1.4.1.318.1.1.10.4.2.2.1"  # APC NetBotz sensor value table
_CISCO_ENVMON_TEMP = "1.3.6.1.4.1.9.9.13.1.3.1"      # CISCO-ENVMON-MIB ciscoEnvMonTemperatureStatusTable
_CISCO_CPU_MIB     = "1.3.6.1.4.1.9.9.109.1.1.1.1"   # CISCO-PROCESS-MIB cpmCPUTotalTable
_CISCO_MEM_MIB     = "1.3.6.1.4.1.9.9.48.1.1.1"       # CISCO-MEMORY-POOL-MIB ciscoMemoryPool
_BGP4_PEER_TBL     = "1.3.6.1.2.1.15.3.1"             # BGP4-MIB bgpPeerTable
_PDU_OUTLET_ENT    = "1.3.6.1.4.1.99999.5.20.1"       # pduOutletTable (per-outlet: idx/name/status/current/power)


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


# Maps a plant device_type → (enterprise base OID, analog points, binary points)
#   analog: (BACnet point name, OID index, scale, snmp type)  value = round(v*scale)
#   binary: (BACnet point name, OID index)                    value = 2 if on/alarm else 1
# Scales mirror the static _chiller_entries/_pump_entries/... layouts so the
# patched (live) OIDs read identically to the generated ones.
_PLANT_OID_PATCH = {
    "chiller": ("1.3.6.1.4.1.99999.20",
        [("CHW_Supply_Temp", 1, 10, "2"), ("CHW_Return_Temp", 2, 10, "2"),
         ("CHW_Setpoint", 3, 10, "2"), ("CHW_Flow", 4, 10, "2"),
         ("Cond_Supply_Temp", 5, 10, "2"), ("Cond_Return_Temp", 6, 10, "2"),
         ("Compressor_Load", 7, 1, "2"), ("Active_Power", 9, 1, "2"),
         ("Cooling_Capacity", 10, 1, "2"), ("COP", 11, 10, "2"),
         ("Evap_Pressure", 12, 1, "2"), ("Cond_Pressure", 13, 1, "2"),
         ("Run_Hours", 14, 1, "65")],
        [("Chiller_Running", 8), ("Alarm_HighPressure", 16),
         ("Alarm_LowEvapTemp", 17), ("Alarm_FlowLoss", 18)]),
    "pump": ("1.3.6.1.4.1.99999.21",
        [("Speed", 2, 1, "2"), ("Flow", 3, 10, "2"), ("Discharge_Pressure", 4, 1, "2"),
         ("Suction_Pressure", 5, 1, "2"), ("Diff_Pressure", 6, 1, "2"),
         ("Motor_Power", 7, 10, "2"), ("Motor_Temp", 8, 1, "2"),
         ("VFD_Frequency", 9, 10, "2"), ("Run_Hours", 10, 1, "65")],
        [("Run_Status", 1), ("Alarm_Fault", 11), ("Alarm_LowFlow", 12)]),
    "cooling_tower": ("1.3.6.1.4.1.99999.22",
        [("Fan_Speed", 2, 1, "2"), ("Basin_Temp", 3, 10, "2"), ("Cond_Water_In", 4, 10, "2"),
         ("Cond_Water_Out", 5, 10, "2"), ("Fan_Power", 6, 10, "2"), ("Basin_Level", 7, 1, "2"),
         ("Makeup_Flow", 8, 10, "2"), ("Vibration", 9, 10, "2"), ("Run_Hours", 10, 1, "65")],
        [("Fan_Status", 1), ("Alarm_HighVibration", 11), ("Alarm_LowBasin", 12)]),
    "valve": ("1.3.6.1.4.1.99999.23",
        [("Position", 1, 1, "2"), ("Commanded_Position", 2, 1, "2"), ("Actuator_Temp", 4, 1, "2")],
        [("Status_Modulating", 3), ("Alarm_ActuatorFault", 6)]),
    "cdu": ("1.3.6.1.4.1.99999.25",
        [("TCS_Supply_Temp", 1, 10, "2"), ("TCS_Return_Temp", 2, 10, "2"),
         ("TCS_Setpoint", 3, 10, "2"), ("TCS_Flow", 4, 10, "2"),
         ("Facility_CHW_Valve", 5, 1, "2"), ("Facility_CHW_Flow", 6, 10, "2"),
         ("Heat_Load", 7, 1, "2"), ("Pump_Power", 8, 10, "2"),
         ("Pump_Speed", 9, 1, "2"), ("Approach_Temp", 10, 10, "2"),
         ("Filter_DP", 11, 1, "2"), ("Run_Hours", 12, 1, "65"),
         ("TCS_Loop_Pressure", 18, 1, "2")],
        [("Unit_Running", 13), ("Alarm_Leak", 14), ("Alarm_HighSupplyTemp", 15),
         ("Alarm_PumpFault", 16), ("Alarm_LowFlow", 17)]),
    "crah": ("1.3.6.1.4.1.99999.24",
        [("Supply_Air_Temp", 1, 10, "2"), ("Return_Air_Temp", 2, 10, "2"),
         ("Setpoint", 3, 10, "2"), ("Fan_Speed", 4, 1, "2"), ("CHW_Valve", 5, 1, "2"),
         ("Cooling_Capacity", 6, 1, "2"), ("Supply_Humidity", 7, 1, "2"),
         ("Airflow", 8, 1, "2"), ("Fan_Power", 9, 1, "2"), ("Run_Hours", 10, 1, "65")],
        [("Unit_Running", 11), ("Alarm_HighTemp", 12), ("Alarm_AirflowLoss", 13),
         ("Filter_Dirty", 14)]),
}


class SNMPRecGenerator:
    # Sidecar recording WHICH topology the datasets in this directory were built
    # from. Deliberately not a .snmprec: snmpsim serves anything matching that
    # glob, and the orphan reaper deletes anything that is not an expected agent.
    FINGERPRINT_FILE = _fingerprint.FINGERPRINT_FILE

    def __init__(self, output_dir: str = "datasets/snmp"):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir: Optional[str] = None   # lazily resolved from snmpsim.confdir

    # ------------------------------------------------------------------ #
    #  Topology fingerprint                                                #
    # ------------------------------------------------------------------ #

    # The fingerprint describes the TOPOLOGY, not SNMP — it lives in
    # core.dataset_fingerprint so the gNMI generator stamps the same value into
    # its own directory instead of importing this module.
    topology_fingerprint = staticmethod(_fingerprint.compute)

    def write_fingerprint(self, topology: TopologyEngine) -> str:
        return _fingerprint.write(self.output_dir, topology)

    def read_fingerprint(self) -> Optional[str]:
        return _fingerprint.read(self.output_dir)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def snmp_address(device: Device) -> str:
        """Return the IP address used to reach this device via SNMP.

        Switches/routers/etc.: the NOS SNMP agent answers on the OOB mgmt
        network (mgmt_ip) when present, otherwise on ip_address.

        Servers: the OS SNMP agent (net-snmp style) binds the production
        NIC — the mgmt IP belongs to the BMC, a separate controller (see
        bmc_address / generate_server_bmc for the BMC SNMP agent).
        """
        if device.device_type == DeviceType.SERVER:
            return device.ip_address or device.mgmt_ip
        return device.mgmt_ip if device.mgmt_ip else device.ip_address

    @staticmethod
    def bmc_address(device: Device) -> str:
        """Mgmt IP served by a server's BMC SNMP agent ('' if not a server
        or no OOB connection). Same IP the Redfish BMC binds."""
        if device.device_type == DeviceType.SERVER and device.mgmt_ip:
            return device.mgmt_ip
        return ""

    @classmethod
    def snmp_bind_ips(cls, device: Device) -> List[str]:
        """All IPs snmpsim should listen on for this device (OS + BMC)."""
        if device.device_type in _NO_SNMP_TYPES:
            return []
        ips = []
        a = cls.snmp_address(device)
        if a:
            ips.append(a)
        b = cls.bmc_address(device)
        if b and b != a:
            ips.append(b)
        return ips

    def reap_orphans(self, topology: TopologyEngine) -> List[str]:
        """Delete .snmprec files no device in THIS topology should serve.

        snmpsim resolves a request by community -> "<community>.snmprec", so any
        file left in this directory is a live agent whether or not the topology
        still contains that device. Datasets were only ever written, never removed,
        so IPs from retired topologies kept answering: a pump at an address that
        once belonged to a Lenovo server replied "XClarity Controller — Lenovo
        Server BMC", and plant gear that must have NO SNMP agent at all
        (_NO_SNMP_TYPES: chiller/pump/cooling tower/valve/RPP — BACnet & Modbus
        devices) answered anyway. A DCIM polling that address would inventory
        hardware that does not exist.

        snmp_bind_ips() is the authority on what SHOULD exist — the same function
        the binder uses — so this cannot drift from what gets generated: it returns
        [] for the types that have no agent.

        Refuses to run on an empty expectation (no devices / topology not loaded)
        rather than emptying the directory.
        """
        expected = set()
        for d in topology.get_all_devices():
            for ip in self.snmp_bind_ips(d):
                expected.add(f"{ip}.snmprec")
        if not expected:
            return []
        removed = []
        for p in self.output_dir.glob("*.snmprec"):
            if p.name in expected:
                continue
            try:
                p.unlink()
                removed.append(p.name)
            except OSError:
                pass          # busy/permission — leave it; next run retries
        return removed

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
            if device.device_type in _NO_SNMP_TYPES:
                continue   # BACnet-only plant gear / passive panel — no agent
            # Servers always get an OS-agent dataset on the production IP;
            # other types with no OOB connection are unreachable via SNMP.
            if (has_mgmt_layer and not device.mgmt_ip
                    and device.device_type != DeviceType.SERVER):
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

        # ENTITY-MIB chassis inventory — structured OS/model/serial for the
        # NOS-running devices (switches, routers, firewalls, LBs, OOB switches),
        # which all implement it. Lets a DCIM read the software rev / serial
        # directly instead of regex-parsing sysDescr.
        _ENTITY_TYPES = (DeviceType.ROUTER, DeviceType.SWITCH, DeviceType.FIREWALL,
                         DeviceType.LOAD_BALANCER, DeviceType.OOB_SWITCH)
        if device.device_type in _ENTITY_TYPES:
            entries += self._entity_entries(device)

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
                entries += self._cisco_envmon_temp_entries(device)
                entries += self._cisco_perf_entries(device)
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

        if device.device_type == DeviceType.UPS:
            entries += self._ups_entries(device)

        if device.device_type in (DeviceType.PDU, DeviceType.FLOOR_PDU):
            entries += self._pdu_entries(device)
            entries += self._pdu_outlet_entries(device, topology)

        if device.device_type == DeviceType.GENERATOR:
            entries += self._generator_entries(device)

        if device.device_type == DeviceType.UTILITY_FEED:
            entries += self._utility_entries(device)

        if device.device_type == DeviceType.SWITCHGEAR:
            entries += self._switchgear_entries(device)

        if device.device_type == DeviceType.ATS:
            entries += self._ats_entries(device)

        if device.device_type == DeviceType.MCC:
            entries += self._mcc_entries(device)

        if device.device_type == DeviceType.MPP:
            entries += self._mpp_entries(device)

        if device.device_type == DeviceType.CRAH:
            entries += self._crah_entries(device)

        if device.device_type == DeviceType.CDU:
            entries += self._cdu_entries(device)

        # No SNMP agent → skip file generation entirely:
        #   RPP — passive breaker panel.
        #   Chiller / pump / cooling tower / valve — BACnet/Modbus-native
        #   plant equipment; real units have no SNMP card (telemetry reaches
        #   IT through the BMS). CRAH and CDU keep SNMP — they ship native
        #   network cards (Liebert IntelliSlot / CoolIT-style) in real DCs.
        if device.device_type in _NO_SNMP_TYPES:
            return

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

        # Servers with an OOB connection also expose a BMC SNMP agent on the
        # mgmt IP (the same address Redfish binds) — hardware health only.
        if self.bmc_address(device):
            self.generate_server_bmc(device)

        return filepath

    # ------------------------------------------------------------------ #
    #  Server BMC SNMP agent (mgmt IP)                                     #
    # ------------------------------------------------------------------ #
    # A real BMC (iDRAC/iLO/XCC) runs its own SNMP agent on the OOB mgmt
    # IP, powered by standby power — it answers even while the chassis is
    # Off, serving hardware health only (temps, fans, PSUs, power state),
    # never OS metrics. The OS agent lives on the production IP instead.

    def _bmc_entries(self, device: Device) -> List[OidEntry]:
        import time as _time
        from core.redfish_data_generator import _bmc_branding, _live_watts
        bmc_name, _short, fw = _bmc_branding(device.vendor.value)
        boot = _BMC_BOOT.setdefault(device.name, _time.time())
        uptime_cs = int((_time.time() - boot) * 100)   # BMC uptime ≠ host uptime
        off = getattr(device, "power_state", "On") == "Off"
        watts = _live_watts(device)                    # 0 while chassis Off

        entries = [
            _oid_entry(f"{SYSTEM_BASE}.1.0", "4",
                       f"{bmc_name} {fw} — {device.vendor.value} "
                       f"{device.model_name or 'Server'} BMC"),
            _oid_entry(f"{SYSTEM_BASE}.2.0", "6", BMC_BASE),
            _oid_entry(f"{SYSTEM_BASE}.3.0", "67", str(uptime_cs)),
            _oid_entry(f"{SYSTEM_BASE}.4.0", "4",
                       device.sys_contact or f"admin@{device.name.lower()}.example.com"),
            _oid_entry(f"{SYSTEM_BASE}.5.0", "4", f"{device.name}-bmc"),
            _oid_entry(f"{SYSTEM_BASE}.6.0", "4", device.sys_location or ""),
            _oid_entry(f"{SYSTEM_BASE}.7.0", "2", "72"),
            # Chassis power state: 1=On 2=Off
            _oid_entry(f"{BMC_BASE}.1.1.0", "2", "2" if off else "1"),
            # Thermal sensors (tenths of °C) — readable on standby power
            _oid_entry(f"{BMC_BASE}.2.1.0", "2", str(int(round((device.inlet_temp or 0.0) * 10)))),
            _oid_entry(f"{BMC_BASE}.2.2.0", "2", str(int(round((device.cpu_temp or 0.0) * 10)))),
        ]
        # Fans ×4 (RPM, gauge) — stopped while the chassis is Off.
        # Reads the ticker-driven fan_rpm, the same single source of truth Redfish
        # _fans() uses, so the BMC's two agents cannot disagree about the same
        # chassis. That matters now that the curve is cooling-dependent: a
        # direct-to-chip server runs its fans far slower than an air-cooled one at
        # the same load, and recomputing an air-cooled curve here would report a
        # DLC box spinning at speeds its cold-plated CPU never asks for.
        # Falls back to the air-cooled curve only when no tick has run yet.
        base_rpm = float(getattr(device, "fan_rpm", 0) or 0)
        if base_rpm <= 0:
            from core.device_manager import fan_rpm_range
            _lo, _hi = fan_rpm_range(getattr(device, "model_name", "") or "")
            base_rpm = _lo + (_hi - _lo) * max(
                0.0, min(1.0, ((device.cpu_temp or 20.0) - 40.0) / 45.0))
        # Per-fan STATUS alongside the tach reading (1=ok 2=underSpeed 3=failed),
        # mirroring the PSU status/watts pair below and what a real BMC MIB carries
        # (IDRAC coolingDeviceStatus, cpqHeFltTolFanCondition). A bare RPM gauge
        # cannot be alarmed on generically — the healthy range depends on chassis
        # height and cooling type — so the BMC publishes its own verdict, computed
        # from the same floor the rule engine and Redfish use.
        from core.device_state_store import _DTC_IDLE_FACTOR, _is_liquid_server
        from core.device_manager import fan_rpm_range as _fr
        _flo, _ = _fr(getattr(device, "model_name", "") or "")
        if _is_liquid_server(device.name):
            _flo *= _DTC_IDLE_FACTOR
        for i in range(1, 5):
            rpm = 0 if off else max(0, int(base_rpm + (i - 2.5) * 110))
            entries.append(_oid_entry(f"{BMC_BASE}.3.1.{i}", "42", str(rpm)))
            # A powered-off chassis has stopped fans by design, not by fault.
            if off:
                st = "1"
            elif rpm < _flo * 0.25:
                st = "3"
            elif rpm < _flo * 0.90:
                st = "2"
            else:
                st = "1"
            entries.append(_oid_entry(f"{BMC_BASE}.3.2.{i}", "2", st))
        # PSUs ×2 — status (1=ok) + output watts (chassis load split)
        for i in range(1, 3):
            entries.append(_oid_entry(f"{BMC_BASE}.4.1.{i}", "2", "1"))
            entries.append(_oid_entry(f"{BMC_BASE}.4.2.{i}", "42", str(int(watts / 2))))
        entries += [
            _oid_entry(f"{BMC_BASE}.5.1.0", "42", str(int(watts))),   # total draw W
            _oid_entry(f"{BMC_BASE}.6.1.0", "4", device.model_name or device.vendor.value),
            _oid_entry(f"{BMC_BASE}.6.2.0", "4", device.vendor.value),
        ]
        return _sort_oids(entries)

    def generate_server_bmc(self, device: Device) -> Optional[str]:
        """Write the BMC hardware-health dataset at <mgmt_ip>.snmprec."""
        ip = self.bmc_address(device)
        if not ip:
            return None
        filepath = str(self.output_dir / f"{ip}.snmprec")
        self._atomic_write(filepath, self._bmc_entries(device))
        return filepath

    def patch_bmc_metrics(self, device: Device) -> Optional[str]:
        """Per-tick refresh of the BMC dataset (every value is dynamic).

        Runs regardless of chassis power state — the BMC lives on standby
        power, so temps/fans/power-state keep reporting while the host is
        Off. Uses the same tmp → pre-built index → replace dance as
        patch_metrics so SNMPSim never sees a partial file.
        """
        ip = self.bmc_address(device)
        if not ip:
            return None
        filepath = self.output_dir / f"{ip}.snmprec"
        if not filepath.exists():
            return str(filepath)

        new_content = "\n".join("|".join(e) for e in self._bmc_entries(device))
        lock = _get_write_lock(str(filepath.resolve()))
        with lock:
            import tempfile as _tempfile
            tmp = None
            try:
                with _tempfile.NamedTemporaryFile(
                        "w", dir=str(self.output_dir), suffix=".snmprec.tmp",
                        encoding="utf-8", newline="\n", delete=False) as f:
                    f.write(new_content)
                    tmp = f.name
                mtime = int(os.stat(tmp).st_mtime)
                self._reindex(tmp, canonical_path=str(filepath.resolve()),
                              target_mtime=mtime + 1)
                _replace_with_retry(tmp, str(filepath))
            except OSError as e:
                log.warning("[SNMPRecGen] patch_bmc_metrics failed for %s: %s",
                            filepath, e)
                if tmp:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
        return str(filepath)

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
            _is_pdu     = device.device_type in (DeviceType.PDU, DeviceType.FLOOR_PDU)
            _pdu_ol_out = 1    # per-outlet status (1=on, 2=off)

            # System uptime (centiseconds)
            updates[f"{SYSTEM_BASE}.3.0"] = ("67", str(device.sys_uptime))
            updates[f"{SYSTEM_BASE}.8.0"] = ("67", str(device.sys_uptime))

            # Editable system OIDs — keep in sync with device fields
            updates[f"{SYSTEM_BASE}.5.0"] = ("4", device.name)
            updates[f"{SYSTEM_BASE}.6.0"] = ("4", device.sys_location)
            updates[f"{SYSTEM_BASE}.4.0"] = ("4", f"admin@{device.name.lower()}.example.com")

            # Interface counters
            ifx_base = "1.3.6.1.2.1.31.1.1.1"
            for iface in device.interfaces:
                i = iface.index
                # Unconnected ports carry no traffic — report zero counters.
                unconnected = iface.connected_to_device is None
                in_oct   = 0 if unconnected else iface.in_octets
                out_oct  = 0 if unconnected else iface.out_octets
                in_err   = 0 if unconnected else iface.in_errors
                out_err  = 0 if unconnected else iface.out_errors
                in_pkts  = in_oct  // 1500
                out_pkts = out_oct // 1500
                oper = 2 if unconnected else iface.oper_status
                updates[f"{IF_TABLE}.8.{i}"]  = ("2",  str(oper))
                updates[f"{IF_TABLE}.10.{i}"] = ("41", str(in_oct))
                updates[f"{IF_TABLE}.11.{i}"] = ("41", str(in_pkts))
                updates[f"{IF_TABLE}.13.{i}"] = ("41", str(in_err // 10))
                updates[f"{IF_TABLE}.14.{i}"] = ("41", str(in_err))
                updates[f"{IF_TABLE}.16.{i}"] = ("41", str(out_oct))
                updates[f"{IF_TABLE}.17.{i}"] = ("41", str(out_pkts))
                updates[f"{IF_TABLE}.19.{i}"] = ("41", str(out_err // 10))
                updates[f"{IF_TABLE}.20.{i}"] = ("41", str(out_err))
                updates[f"{ifx_base}.6.{i}"]  = ("44", str(in_oct  * 4))
                updates[f"{ifx_base}.10.{i}"] = ("44", str(out_oct * 4))

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
                # Standard ssCpu percentages — keep them live so a real poller's
                # 100 - ssCpuIdle tracks the device, not a frozen value.
                updates[f"{UCD_CPU}.9.0"]  = ("2",  str(cpu_user))    # ssCpuUser %
                updates[f"{UCD_CPU}.10.0"] = ("2",  str(cpu_system))  # ssCpuSystem %
                updates[f"{UCD_CPU}.11.0"] = ("2",  str(cpu_idle))    # ssCpuIdle %
                updates[f"{UCD_MEM}.5.0"]  = ("2", str(device.memory_total // 1024))
                updates[f"{UCD_MEM}.6.0"]  = ("2", str(mem_free // 1024))
                updates[f"{UCD_MEM}.11.0"] = ("2", str(device.memory_used  // 1024))
                # ENTITY-SENSOR-MIB temperature (×10 Celsius)
                updates["1.3.6.1.2.1.99.1.1.1.4.1"] = ("2", str(int(device.inlet_temp * 10)))
                updates["1.3.6.1.2.1.99.1.1.1.4.2"] = ("2", str(int(device.cpu_temp   * 10)))
                updates[f"{UCD_DISK}.5.1"] = ("2",  str(disk_pct))
                updates[f"{UCD_DISK}.6.1"] = ("2",  str(disk_total_kb))
                updates[f"{UCD_DISK}.7.1"] = ("2",  str(disk_avail_kb))

            # CISCO-ENVMON-MIB temperature — Cisco switches / routers
            _CISCO_NET = (DeviceType.SWITCH, DeviceType.ROUTER,
                          DeviceType.FIREWALL, DeviceType.LOAD_BALANCER)
            if device.vendor == Vendor.CISCO_SYSTEMS and device.device_type in _CISCO_NET:
                _b = _CISCO_ENVMON_TEMP
                updates[f"{_b}.3.1"] = ("66", str(int(round(device.inlet_temp))))
                updates[f"{_b}.3.2"] = ("66", str(int(round(device.cpu_temp))))
                updates[f"{_b}.6.1"] = ("2",  str(2 if device.inlet_temp >= 40 else 1))
                updates[f"{_b}.6.2"] = ("2",  str(2 if device.cpu_temp   >= 75 else 1))

            # CISCO-PROCESS-MIB CPU + CISCO-MEMORY-POOL-MIB
            if device.vendor == Vendor.CISCO_SYSTEMS and device.device_type in _CISCO_NET:
                mem_free = max(0, device.memory_total - device.memory_used)
                updates[f"{_CISCO_CPU_MIB}.7.1"] = ("2", str(device.cpu_usage))
                updates[f"{_CISCO_CPU_MIB}.8.1"] = ("2", str(device.cpu_usage))
                updates[f"{_CISCO_MEM_MIB}.5.1"] = ("2", str(device.memory_used // (1024 * 1024)))
                updates[f"{_CISCO_MEM_MIB}.6.1"] = ("2", str(mem_free // (1024 * 1024)))

            # Sensor environmental readings
            if device.device_type == DeviceType.SENSOR:
                # Plant header instrument: one point, served on ENTITY-SENSOR-MIB.
                # Must be patched here as well as generated, or a poll would keep
                # returning the value the file was written with while the loop moved.
                _probe = self._plant_probe_entries(device)
                inlet_t10   = int(round(device.inlet_temp * 10))
                humid_t10   = int(round(device.humidity   * 10))
                dewpt_t10   = int(round(device.dewpoint   * 10))
                airflow_t10 = int(round(device.airflow    * 10))
            else:
                _probe = None

            if _probe is not None:
                for _oid, _typ, _val in _probe:
                    updates[_oid] = (_typ, _val)
            elif device.device_type == DeviceType.SENSOR:
                if device.vendor == Vendor.RARITAN:
                    if device.model_name == "Raritan DPX2-T3H1":
                        mid_t10     = int(round(device.mid_temp    * 10))
                        exhaust_t10 = int(round(device.outlet_temp * 10))
                        updates[f"{_RARITAN_SENSOR}.4.1.1"] = ("2", str(inlet_t10))
                        updates[f"{_RARITAN_SENSOR}.4.1.2"] = ("2", str(mid_t10))
                        updates[f"{_RARITAN_SENSOR}.4.1.3"] = ("2", str(exhaust_t10))
                        updates[f"{_RARITAN_SENSOR}.4.1.4"] = ("2", str(humid_t10))
                    elif device.model_name == "Raritan DPX2-CC2":
                        try:
                            from core.device_state_store import _get_ext_state
                            _cc2_ext = _get_ext_state(device.name)
                        except Exception:
                            _cc2_ext = {}
                        water_val = 1 if _cc2_ext.get("water_detection", "dry") == "wet" else 0
                        updates[f"{_RARITAN_SENSOR}.4.1.1"] = ("2", str(water_val))
                        updates[f"{_RARITAN_SENSOR}.4.1.2"] = ("2", str(inlet_t10))
                    else:
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

            # UPS pollable status OIDs
            if device.device_type == DeviceType.UPS:
                ext = {}
                try:
                    from core.device_state_store import _get_ext_state
                    ext = _get_ext_state(device.name)
                except Exception:
                    pass
                _UPS_MIB = "1.3.6.1.2.1.33.1"
                _UPS_ENT = "1.3.6.1.4.1.99999.4"
                ups_status  = ext.get("ups_status", "normal")
                batt_status = ext.get("ups_battery_status", "normal")
                out_load    = int(ext.get("ups_output_load", 40.0))
                in_volt     = int(ext.get("ups_input_voltage", 220.0))
                in_freq     = int(round(ext.get("ups_input_frequency", 50.0) * 10))
                # upsBatteryStatus: 2=normal, 3=low, 4=depleted
                batt_snmp = 2
                if ups_status == "low_battery":
                    batt_snmp = 4
                elif ups_status == "on_battery":
                    batt_snmp = 3
                # upsEstimatedChargeRemaining: 100% when normal, 50% on_battery, 15% low
                charge = 100 if ups_status == "normal" else (50 if ups_status == "on_battery" else 15)
                minutes = 60 if ups_status == "normal" else (20 if ups_status == "on_battery" else 5)
                # upsOutputSource: 3=normal, 5=battery
                out_src = 5 if ups_status in ("on_battery", "low_battery") else 3
                updates[f"{_UPS_MIB}.2.3.0"]     = ("2",  str(batt_snmp))
                updates[f"{_UPS_MIB}.2.4.0"]     = ("2",  str(minutes))
                updates[f"{_UPS_MIB}.2.5.0"]     = ("2",  str(charge))
                updates[f"{_UPS_MIB}.3.3.1.2.1"] = ("2",  str(in_freq))
                updates[f"{_UPS_MIB}.3.3.1.3.1"] = ("2",  str(in_volt))
                updates[f"{_UPS_MIB}.4.1.0"]     = ("2",  str(out_src))
                updates[f"{_UPS_MIB}.4.4.1.6.1"] = ("2",  str(out_load))
                # Enterprise UPS status OIDs
                fan_s  = 1 if ext.get("ups_fan_status", "ok") == "failure" else 2
                chg_s  = 1 if ext.get("ups_charger_status", "ok") == "failure" else 2
                rec_s  = 1 if ext.get("ups_rectifier_status", "ok") == "failure" else 2
                pha_s  = 1 if ext.get("ups_phase_status", "ok") == "failure" else 2
                b_ex   = {"normal": 2, "failure": 3, "disconnected": 4}.get(batt_status, 2)
                updates[f"{_UPS_ENT}.1.0"] = ("2", str(fan_s))
                updates[f"{_UPS_ENT}.2.0"] = ("2", str(chg_s))
                updates[f"{_UPS_ENT}.3.0"] = ("2", str(rec_s))
                updates[f"{_UPS_ENT}.4.0"] = ("2", str(pha_s))
                updates[f"{_UPS_ENT}.5.0"] = ("2", str(b_ex))
                # Extended power metrics
                bypass_on   = ext.get("ups_bypass_status", "off") == "on"
                batt_health = int(round(ext.get("ups_battery_health", 100.0)))
                energy_kwh  = int(round(ext.get("ups_energy_kwh", 0.0) * 10))
                # upsOperatingMode: 3=bypass, 2=battery, 1=online
                if bypass_on:
                    op_mode = 3
                elif ups_status in ("on_battery", "low_battery"):
                    op_mode = 2
                else:
                    op_mode = 1
                # upsBypassStatus: 1=not-on-bypass, 2=on-bypass
                bypass_snmp = 2 if bypass_on else 1
                _OUT_PF      = 0.9      # typical UPS output power factor
                _EFFICIENCY  = 0.92     # typical UPS efficiency
                _OUT_V       = 220
                # upsOutputPower is a MEASURED register on real gear, so serve the live
                # watts the store publishes (ups_output_kw). A UPS with no rating in the
                # SKU catalog isn't wired into the power graph and has no live watts, so
                # it falls back to a nominal 3 kVA desktop frame scaled by percent load —
                # which is all the old code ever did, and why a 1200 kW datacenter UPS
                # used to report its output as a few hundred watts.
                _NOM_VA      = 3000.0   # nominal small-frame VA, fallback only
                out_kw = float(ext.get("ups_output_kw", 0.0) or 0.0)
                if out_kw > 0.0:
                    out_real_w = int(round(out_kw * 1000.0))
                    out_va     = out_real_w / _OUT_PF
                    # A datacenter frame is 400 V three-phase, not a 220 V wall socket.
                    # Reporting its current against 220 V single-phase would triple it.
                    _OUT_V     = 400
                    _out_phase = 1.7320508    # sqrt(3)
                else:
                    out_va     = out_load * _NOM_VA / 100.0
                    out_real_w = int(round(out_va * _OUT_PF))
                    _out_phase = 1.0
                # upsOutputApparentPower (VA)
                app_power = int(round(out_va))
                updates[f"{_UPS_ENT}.6.0"]  = ("2", str(op_mode))
                updates[f"{_UPS_ENT}.7.0"]  = ("2", str(bypass_snmp))
                updates[f"{_UPS_ENT}.8.0"]  = ("2", str(batt_health))
                updates[f"{_UPS_ENT}.9.0"]  = ("2", str(app_power))
                updates[f"{_UPS_ENT}.10.0"] = ("2", str(energy_kwh))
                updates[f"{_UPS_ENT}.11.0"] = ("2", str(int(round(ext.get("ups_runtime_min", 8.0)))))  # battery runtime (min)
                out_cur_x10   = int(round(out_va / (_OUT_V * _out_phase) * 10))
                in_real_w     = int(round(out_real_w / _EFFICIENCY))
                in_cur_x10    = int(round(in_real_w / (max(in_volt, 1) * _out_phase) * 10))
                # Battery voltage sags with discharge: 220V at full, ~180V at empty (×10)
                # 480 V VRLA string: float ~544 V, sagging toward the ~420 V cutoff.
                batt_volt_x10 = int(round((416.0 + 128.0 * charge / 100.0) * 10))
                updates[f"{_UPS_MIB}.2.6.0"]     = ("2", str(batt_volt_x10))  # upsBatteryVoltage ×10 V
                updates[f"{_UPS_MIB}.3.3.1.4.1"] = ("2", str(in_cur_x10))    # upsInputCurrent ×10 A
                updates[f"{_UPS_MIB}.3.3.1.5.1"] = ("2", str(in_real_w))     # upsInputTruePower W
                updates[f"{_UPS_MIB}.4.4.1.2.1"] = ("2", str(_OUT_V))        # upsOutputVoltage V
                updates[f"{_UPS_MIB}.4.4.1.3.1"] = ("2", str(out_cur_x10))  # upsOutputCurrent ×10 A
                updates[f"{_UPS_MIB}.4.4.1.4.1"] = ("2", str(out_real_w))   # upsOutputPower W

            # PDU pollable status OIDs
            if device.device_type in (DeviceType.PDU, DeviceType.FLOOR_PDU):
                ext = {}
                try:
                    from core.device_state_store import _get_ext_state
                    ext = _get_ext_state(device.name)
                except Exception:
                    pass
                _PDU_ENT = "1.3.6.1.4.1.99999.5"
                _pdu_vendor = _vendor_oids.vendor_key(device.vendor)
                pdu_load = int(ext.get("pdu_load", 45.0))
                pdu_volt = int(ext.get("pdu_voltage", 220.0))
                pdu_pf   = int(round(ext.get("pdu_power_factor", 0.95) * 100))
                pdu_pi   = int(ext.get("pdu_phase_imbalance", 2.0))
                pdu_out  = 1 if ext.get("pdu_outlet_status", "on") == "on" else 2
                pdu_brk  = 1 if ext.get("pdu_breaker_status", "ok") == "ok" else 2
                pdu_of   = 1 if ext.get("pdu_outlet_failure", "ok") == "ok" else 2
                pdu_smk  = 1 if ext.get("pdu_smoke", "no") == "no" else 2
                pdu_cur  = int(round(ext.get("pdu_outlet_current", 10.0) * 10))
                pdu_gf   = 1 if ext.get("pdu_ground_fault", "no") == "no" else 2
                # Vendors with a verified MIB publish on their own tree instead
                # (below); a device that says "APC Rack PDU 2G" in sysDescr must
                # not also answer on a placeholder enterprise tree.
                _pdu_native = _pdu_vendor in ("apc", "raritan")
                if not _pdu_native:
                    updates[f"{_PDU_ENT}.1.0"]  = ("2", str(pdu_load))
                    updates[f"{_PDU_ENT}.2.0"]  = ("2", str(pdu_volt))
                    updates[f"{_PDU_ENT}.3.0"]  = ("2", str(pdu_pf))
                    updates[f"{_PDU_ENT}.4.0"]  = ("2", str(pdu_pi))
                    updates[f"{_PDU_ENT}.5.0"]  = ("2", str(pdu_out))
                    updates[f"{_PDU_ENT}.6.0"]  = ("2", str(pdu_brk))
                    updates[f"{_PDU_ENT}.7.0"]  = ("2", str(pdu_of))
                    updates[f"{_PDU_ENT}.8.0"]  = ("2", str(pdu_smk))
                    updates[f"{_PDU_ENT}.9.0"]  = ("2", str(pdu_cur))
                    updates[f"{_PDU_ENT}.10.0"] = ("2", str(pdu_gf))
                # Derived power metrics
                pdu_real_w    = int(round(pdu_volt * ext.get("pdu_outlet_current", 10.0) * ext.get("pdu_power_factor", 0.95)))
                pdu_appar_va  = int(round(pdu_volt * ext.get("pdu_outlet_current", 10.0)))
                pdu_outlet_w  = pdu_real_w  # per-outlet (single outlet model)
                pdu_energy_x10 = int(round(ext.get("pdu_energy_kwh", 0.0) * 10))
                pdu_freq_x10  = int(round(ext.get("pdu_frequency", 50.0) * 10))
                pdu_temp_x10  = int(round(ext.get("pdu_temperature", 23.0) * 10))
                pdu_hum_x10   = int(round(ext.get("pdu_humidity", 45.0) * 10))
                if not _pdu_native:
                    updates[f"{_PDU_ENT}.11.0"] = ("2", str(pdu_real_w))
                    updates[f"{_PDU_ENT}.12.0"] = ("2", str(pdu_appar_va))
                    updates[f"{_PDU_ENT}.13.0"] = ("2", str(pdu_energy_x10))
                    updates[f"{_PDU_ENT}.14.0"] = ("2", str(pdu_freq_x10))
                    updates[f"{_PDU_ENT}.15.0"] = ("2", str(pdu_temp_x10))
                    updates[f"{_PDU_ENT}.16.0"] = ("2", str(pdu_hum_x10))
                    updates[f"{_PDU_ENT}.17.0"] = ("2", str(pdu_outlet_w))

                # The same live numbers, published where the vendor's own MIB
                # says they live: PowerNet-MIB rPDU2 tables for APC, PDU2-MIB
                # measurement tables for Raritan.
                _pdu_amps = float(ext.get("pdu_outlet_current", 10.0))
                if _pdu_vendor == "apc":
                    A = _vendor_oids.APC
                    # rPDU2 units: power/apparent power in hundredths of kW/kVA,
                    # current in tenths of an amp, energy in tenths of a kWh,
                    # power factor in hundredths. Load state is
                    # lowLoad(1)/normal(2)/nearOverload(3)/overload(4).
                    _load_state = (4 if pdu_load >= 90 else
                                   3 if pdu_load >= 80 else
                                   1 if pdu_load < 10 else 2)
                    updates[f"{A['rpdu2Power']}.1"]        = ("2", str(int(round(pdu_real_w / 10.0))))
                    updates[f"{A['rpdu2ApparentPwr']}.1"]  = ("2", str(int(round(pdu_appar_va / 10.0))))
                    updates[f"{A['rpdu2PowerFactor']}.1"]  = ("2", str(pdu_pf))
                    updates[f"{A['rpdu2Energy']}.1"]       = ("2", str(pdu_energy_x10))
                    updates[f"{A['rpdu2LoadState']}.1"]    = ("2", str(_load_state))
                    updates[f"{A['rpdu2PhaseCurrent']}.1"] = ("2", str(int(round(_pdu_amps * 10))))
                    updates[f"{A['rpdu2PhaseVoltage']}.1"] = ("2", str(pdu_volt))
                    updates[f"{A['rpdu2PhaseState']}.1"]   = ("2", str(_load_state))
                    # A tripped bank breaker reads as an overload bank state —
                    # PowerNet has no separate "breaker open" object.
                    updates[f"{A['rpdu2BankState']}.1"]    = ("2", "4" if pdu_brk != 1 else str(_load_state))
                    updates[f"{A['rpdu2BankCurrent']}.1"]  = ("2", str(int(round(_pdu_amps * 10))))
                    updates[f"{A['rpdu2OutletState']}.1"]  = ("2", "1" if pdu_out == 1 else "2")
                    updates[f"{A['rpdu2SensorTempC']}.1"]  = ("2", str(pdu_temp_x10))
                    updates[f"{A['rpdu2SensorHumid']}.1"]  = ("2", str(int(round(pdu_hum_x10 / 10))))
                elif _pdu_vendor == "raritan":
                    R  = _vendor_oids.RARITAN
                    ST = _vendor_oids.RARITAN_SENSOR_TYPE
                    SS = _vendor_oids.RARITAN_SENSOR_STATE
                    # PDU2-MIB indexes measurements as [pduId][inletId][sensorType]
                    # and scales every reading by that sensor's decimalDigits, so
                    # both are published together.
                    _state = (SS["aboveUpperCritical"] if pdu_load >= 90 else
                              SS["aboveUpperWarning"] if pdu_load >= 80 else SS["normal"])
                    _inlet = {
                        ST["current"]:       (int(round(_pdu_amps * 1000)), 3),   # mA
                        ST["voltage"]:       (int(round(pdu_volt * 1000)), 3),    # mV
                        ST["activePower"]:   (pdu_real_w, 0),                     # W
                        ST["apparentPower"]: (pdu_appar_va, 0),                   # VA
                        ST["powerFactor"]:   (pdu_pf, 2),                         # 0.00–1.00
                        ST["activeEnergy"]:  (int(round(pdu_energy_x10 * 100)), 0),# Wh
                        ST["frequency"]:     (pdu_freq_x10, 1),                   # Hz
                    }
                    for _stype, (_val, _dec) in _inlet.items():
                        updates[f"{R['inletValue']}.1.1.{_stype}"]    = ("66", str(max(0, _val)))
                        updates[f"{R['inletState']}.1.1.{_stype}"]    = ("2",  str(_state))
                        updates[f"{R['inletDecimals']}.1.1.{_stype}"] = ("66", str(_dec))
                    updates[f"{R['outletValue']}.1.1.{ST['current']}"] = ("66", str(int(round(_pdu_amps * 1000))))
                    updates[f"{R['outletState']}.1.1.{ST['onOff']}"]   = ("2", str(SS["on"] if pdu_out == 1 else SS["off"]))
                    updates[f"{R['ocpState']}.1.1.{ST['trip']}"]       = ("2", str(SS["open"] if pdu_brk != 1 else SS["closed"]))
                    # DPX2 temperature/humidity probes on the PDU's sensor port
                    updates[f"{R['externalValue']}.1.1"]    = ("66", str(pdu_temp_x10))
                    updates[f"{R['externalState']}.1.1"]    = ("2",  str(SS["normal"]))
                    updates[f"{R['externalDecimals']}.1.1"] = ("66", "1")
                    updates[f"{R['externalType']}.1.1"]     = ("2",  str(ST["temperature"]))
                    updates[f"{R['externalValue']}.1.2"]    = ("66", str(pdu_hum_x10))
                    updates[f"{R['externalState']}.1.2"]    = ("2",  str(SS["normal"]))
                    updates[f"{R['externalDecimals']}.1.2"] = ("66", "1")
                    updates[f"{R['externalType']}.1.2"]     = ("2",  str(ST["humidity"]))
                # Store outlet status for per-outlet table update below
                _pdu_ol_out = pdu_out
                # Individually switched outlets. The strip flag above is still the
                # "whole PDU dead" case; this is the per-receptacle relay, so a
                # single opened outlet no longer reads as all 42 going dark.
                _pdu_ol_off = {int(o) for o in (ext.get("pdu_outlets_off") or [])}

            # Generator pollable status OIDs — live load/fuel/runtime/status
            if device.device_type == DeviceType.GENERATOR:
                ext = {}
                try:
                    from core.device_state_store import _get_ext_state
                    ext = _get_ext_state(device.name)
                except Exception:
                    pass
                if ext:
                    _GEN_ENT = "1.3.6.1.4.1.99999.7"
                    _gen_st = {"standby": 1, "running": 2, "fault": 3,
                               "cranking": 4, "cooldown": 5}.get(
                        ext.get("gen_status", "standby"), 1)
                    updates[f"{_GEN_ENT}.1.0"]  = ("2", str(int(round(ext.get("gen_fuel_pct", 80.0)))))
                    updates[f"{_GEN_ENT}.2.0"]  = ("2", str(int(ext.get("gen_run_hours", 0.0))))
                    updates[f"{_GEN_ENT}.3.0"]  = ("2", str(_gen_st))
                    updates[f"{_GEN_ENT}.4.0"]  = ("2", str(int(round(ext.get("gen_load_pct", 0.0)))))
                    updates[f"{_GEN_ENT}.5.0"]  = ("2", str(int(round(ext.get("gen_kw", 0.0)))))
                    updates[f"{_GEN_ENT}.13.0"] = ("2", str(int(ext.get("gen_start_attempts", 0))))
                    updates[f"{_GEN_ENT}.14.0"] = ("2", str(int(round(ext.get("gen_runtime_min", 0.0)))))
                    # Discrete alarm points (0 = clear, 1 = active) — pollable, so an
                    # NMS reads the same condition it was trapped on.
                    updates[f"{_GEN_ENT}.15.0"] = ("2", str(int(ext.get("gen_alarm_low_fuel", 0.0))))
                    updates[f"{_GEN_ENT}.16.0"] = ("2", str(int(ext.get("gen_alarm_low_coolant", 0.0))))
                    updates[f"{_GEN_ENT}.17.0"] = ("2", "1" if ext.get("gen_battery_status") == "failure" else "0")
                    updates[f"{_GEN_ENT}.18.0"] = ("2", str(int(ext.get("gen_alarm_transfer", 0.0))))
                    updates[f"{_GEN_ENT}.19.0"] = ("2", str(int(ext.get("gen_alarm_temp", 0.0))))

            # Electrical upstream pollable OIDs — utility feed / switchgear / ATS / MCC.
            if device.device_type in (DeviceType.UTILITY_FEED, DeviceType.SWITCHGEAR,
                                      DeviceType.ATS, DeviceType.MCC, DeviceType.MPP):
                ext = {}
                try:
                    from core.device_state_store import _get_ext_state
                    ext = _get_ext_state(device.name)
                except Exception:
                    pass
                if ext:
                    updates.update(self._electrical_updates(device, ext))

            # Plant live telemetry — patched from the shared BACnet engine
            # (via _plant_state_cache) so SNMP serves the same ticking values
            # as BACnet. Only CDU/CRAH have SNMP agents (native comm cards);
            # chiller/pump/tower/valve are BACnet-only (_NO_SNMP_TYPES).
            if device.device_type in (DeviceType.CDU, DeviceType.CRAH):
                pv = {}
                try:
                    from core.device_state_store import _get_plant_state
                    pv = _get_plant_state(device.name)
                except Exception:
                    pv = {}
                _spec = _PLANT_OID_PATCH.get(device.device_type.value) if pv else None
                if _spec:
                    _base, _ai, _bi = _spec
                    for _nm, _idx, _scale, _typ in _ai:
                        if _nm in pv:
                            updates[f"{_base}.{_idx}.0"] = (_typ, str(int(round(pv[_nm] * _scale))))
                    for _nm, _idx in _bi:
                        if _nm in pv:
                            updates[f"{_base}.{_idx}.0"] = ("2", "2" if pv[_nm] >= 0.5 else "1")

            # Read existing file, replace matching OID lines.
            try:
                lines = filepath.read_text(encoding="utf-8").splitlines()

                # Per-outlet table prefixes.  Status follows the PDU outlet
                # state; current/power are zeroed when the outlet is off,
                # otherwise left at their per-device generated values.
                def _ol_idx(oid: str, pfx: str):
                    """Trailing outlet number of a per-outlet row, or None."""
                    try:
                        return int(oid[len(pfx):])
                    except ValueError:
                        return None

                _ol_stat_pfx = _PDU_OUTLET_ENT + ".3."
                _ol_cur_pfx  = _PDU_OUTLET_ENT + ".4."
                _ol_pwr_pfx  = _PDU_OUTLET_ENT + ".5."
                _ol_off      = _is_pdu and _pdu_ol_out == 2

                patched = []
                for line in lines:
                    sep = line.find("|")
                    if sep != -1:
                        oid = line[:sep]
                        if oid in updates:
                            typ, val = updates[oid]
                            patched.append(f"{oid}|{typ}|{val}")
                            continue
                        if _is_pdu:
                            if oid.startswith(_ol_stat_pfx):
                                _n = _ol_idx(oid, _ol_stat_pfx)
                                _st = (2 if (_pdu_ol_out == 2 or _n in _pdu_ol_off)
                                       else 1)
                                patched.append(f"{oid}|2|{_st}")
                                continue
                            # Current and power follow the SAME outlet's relay: an
                            # open receptacle reads zero amps, a live one keeps its
                            # generated value. Zeroing the whole strip whenever any
                            # outlet was off would hide the load still on the others.
                            if oid.startswith(_ol_cur_pfx) or oid.startswith(_ol_pwr_pfx):
                                _pfx = (_ol_cur_pfx if oid.startswith(_ol_cur_pfx)
                                        else _ol_pwr_pfx)
                                _n = _ol_idx(oid, _pfx)
                                if _ol_off or _n in _pdu_ol_off:
                                    patched.append(f"{oid}|2|0")
                                    continue
                    patched.append(line)

                # Router/Firewall: strip stale BGP peer entries, inject current sessions
                if device.device_type in (DeviceType.ROUTER, DeviceType.FIREWALL):
                    bgp_ext = {}
                    try:
                        from core.device_state_store import _get_ext_state
                        bgp_ext = _get_ext_state(device.name)
                    except Exception:
                        pass
                    _bgp_pfx = _BGP4_PEER_TBL + "."
                    _bgp_st  = {"established": 6, "idle": 1, "active": 3,
                                "connect": 2, "opensent": 4, "openconfirm": 5}
                    patched = [l for l in patched
                               if not ("|" in l and l[:l.index("|")].startswith(_bgp_pfx))]
                    new_bgp: list = []
                    for sess in bgp_ext.get("bgp_sessions", []):
                        peer = sess.get("peer", "")
                        if not peer:
                            continue
                        sv = _bgp_st.get(sess.get("state", "idle"), 1)
                        new_bgp += [
                            f"{_BGP4_PEER_TBL}.2.{peer}|2|{sv}",
                            f"{_BGP4_PEER_TBL}.3.{peer}|2|2",
                            f"{_BGP4_PEER_TBL}.7.{peer}|64|{peer}",
                        ]
                    if new_bgp:
                        def _lkey(l: str) -> tuple:
                            try:
                                return tuple(int(x) for x in l.split("|")[0].split("."))
                            except ValueError:
                                return (999999,)
                        patched = sorted(patched + new_bgp, key=_lkey)

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
                        encoding="utf-8", newline="\n", delete=False) as f:
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
                        encoding="utf-8", newline="\n", delete=False) as f:
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

    @staticmethod
    def _entity_serial(device: Device) -> str:
        """Deterministic 7-char service-tag-style serial, stable across restarts
        (derived from the topology device id)."""
        import hashlib
        h = hashlib.sha1((device.id or device.name).encode()).hexdigest().upper()
        return h[:7]

    def _entity_entries(self, device: Device) -> List[OidEntry]:
        """ENTITY-MIB (RFC 4133) chassis row: entPhysicalEntry columns for a single
        chassis entity (index 1). Real switches also list fan/PSU/module/port rows;
        modelled minimally here. entPhysicalClass=3 (chassis); IsFRU=2 (false).
        entPhysicalSoftwareRev carries the NOS version so a DCIM reads it directly."""
        ent = "1.3.6.1.2.1.47.1.1.1.1"      # entPhysicalEntry
        i = 1
        model = device.model_name or device.vendor.value
        sw    = device.os_version or "Unknown"
        return [
            _oid_entry(f"{ent}.2.{i}",  "4", f"{model} chassis"),           # entPhysicalDescr
            _oid_entry(f"{ent}.5.{i}",  "2", "3"),                          # entPhysicalClass = chassis(3)
            _oid_entry(f"{ent}.7.{i}",  "4", device.name),                  # entPhysicalName
            _oid_entry(f"{ent}.8.{i}",  "4", "A00"),                        # entPhysicalHardwareRev
            _oid_entry(f"{ent}.10.{i}", "4", sw),                           # entPhysicalSoftwareRev
            _oid_entry(f"{ent}.11.{i}", "4", self._entity_serial(device)),  # entPhysicalSerialNum
            _oid_entry(f"{ent}.12.{i}", "4", device.vendor.value),          # entPhysicalMfgName
            _oid_entry(f"{ent}.13.{i}", "4", model),                        # entPhysicalModelName
            _oid_entry(f"{ent}.16.{i}", "2", "2"),                          # entPhysicalIsFRU = false(2)
        ]

    def _system_entries(self, device: Device) -> List[OidEntry]:
        return [
            _oid_entry(f"{SYSTEM_BASE}.1.0", "4", device.sys_descr),
            _oid_entry(f"{SYSTEM_BASE}.2.0", "6", device.sys_oid),  # sysObjectID
            _oid_entry(f"{SYSTEM_BASE}.3.0", "67", str(device.sys_uptime)),
            _oid_entry(f"{SYSTEM_BASE}.4.0", "4", device.sys_contact or f"admin@{device.name.lower()}.example.com"),
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
            # Unconnected ports carry no traffic — report zero counters.
            unconnected = iface.connected_to_device is None
            in_oct  = 0 if unconnected else iface.in_octets
            out_oct = 0 if unconnected else iface.out_octets
            in_err  = 0 if unconnected else iface.in_errors
            out_err = 0 if unconnected else iface.out_errors
            entries += [
                _oid_entry(f"{IF_TABLE}.1.{i}",  "2",  str(i)),            # ifIndex
                _oid_entry(f"{IF_TABLE}.2.{i}",  "4",  iface.name),         # ifDescr
                _oid_entry(f"{IF_TABLE}.3.{i}",  "2",  "6"),                # ifType (ethernetCsmacd)
                _oid_entry(f"{IF_TABLE}.4.{i}",  "2",  "1500"),             # ifMtu
                _oid_entry(f"{IF_TABLE}.5.{i}",  "66", str(min(iface.speed, 4294967295))),   # ifSpeed (Gauge32, capped per RFC 2863)
                _oid_entry(f"{IF_TABLE}.6.{i}",  "4x", iface.mac_address.replace(":", "")),  # ifPhysAddress
                _oid_entry(f"{IF_TABLE}.7.{i}",  "2",  "1"),                # ifAdminStatus (1=up)
                _oid_entry(f"{IF_TABLE}.8.{i}",  "2",  str(2 if unconnected else iface.oper_status)), # ifOperStatus
                _oid_entry(f"{IF_TABLE}.9.{i}",  "67", str(device.sys_uptime)), # ifLastChange
                _oid_entry(f"{IF_TABLE}.10.{i}", "41", str(in_oct)),   # ifInOctets Counter32
                _oid_entry(f"{IF_TABLE}.11.{i}", "41", str(0 if unconnected else random.randint(0,9999))),  # ifInUcastPkts
                _oid_entry(f"{IF_TABLE}.12.{i}", "41", "0"),                # ifInNUcastPkts
                _oid_entry(f"{IF_TABLE}.13.{i}", "41", str(in_err // 10)), # ifInDiscards
                _oid_entry(f"{IF_TABLE}.14.{i}", "41", str(in_err)),       # ifInErrors
                _oid_entry(f"{IF_TABLE}.15.{i}", "41", "0"),                # ifInUnknownProtos
                _oid_entry(f"{IF_TABLE}.16.{i}", "41", str(out_oct)),      # ifOutOctets
                _oid_entry(f"{IF_TABLE}.17.{i}", "41", str(0 if unconnected else random.randint(0,9999))), # ifOutUcastPkts
                _oid_entry(f"{IF_TABLE}.18.{i}", "41", "0"),                # ifOutNUcastPkts
                _oid_entry(f"{IF_TABLE}.19.{i}", "41", str(out_err // 10)), # ifOutDiscards
                _oid_entry(f"{IF_TABLE}.20.{i}", "41", str(out_err)),       # ifOutErrors
                _oid_entry(f"{IF_TABLE}.21.{i}", "66", "0"),                  # ifOutQLen (Gauge32)
                _oid_entry(f"{IF_TABLE}.22.{i}", "6",  "0.0"),              # ifSpecific
            ]

        # IF-MIB extensions (ifXTable)
        ifx_base = "1.3.6.1.2.1.31.1.1.1"
        for iface in device.interfaces:
            i = iface.index
            unconnected = iface.connected_to_device is None
            in_oct  = 0 if unconnected else iface.in_octets
            out_oct = 0 if unconnected else iface.out_octets
            entries += [
                _oid_entry(f"{ifx_base}.1.{i}",  "4",  iface.name),          # ifName
                _oid_entry(f"{ifx_base}.6.{i}",  "44", str(in_oct * 4)),  # ifHCInOctets
                _oid_entry(f"{ifx_base}.10.{i}", "44", str(out_oct * 4)), # ifHCOutOctets
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
            # Standard UCD-SNMP-MIB ssCpu percentages (ssCpuUser/System/Idle) —
            # what real pollers actually read for Linux CPU. Previously .9 held
            # softirq and .11 a stray "systemStats" string, so 100-.9 read ~99%.
            _oid_entry(f"{UCD_CPU}.9.0",  "2",  str(cpu_user)),    # ssCpuUser %
            _oid_entry(f"{UCD_CPU}.10.0", "2",  str(cpu_system)),  # ssCpuSystem %
            _oid_entry(f"{UCD_CPU}.11.0", "2",  str(cpu_idle)),    # ssCpuIdle %
        ]

        # Memory UCD
        mem_total_bytes = device.memory_total
        mem_free        = device.memory_total - device.memory_used
        entries += [
            _oid_entry(f"{UCD_MEM}.1.0",  "2", "1"),
            _oid_entry(f"{UCD_MEM}.4.0",  "2", "0"),                             # memAvailSwap (no swap)
            _oid_entry(f"{UCD_MEM}.5.0",  "2", str(mem_total_bytes // 1024)),    # memTotalReal
            _oid_entry(f"{UCD_MEM}.6.0",  "2", str(mem_free        // 1024)),    # memAvailReal
            _oid_entry(f"{UCD_MEM}.11.0", "2", str(device.memory_used // 1024)), # memCached
            _oid_entry(f"{UCD_MEM}.12.0", "2", str(random.randint(100, 2000))),  # memBuffer
            _oid_entry(f"{UCD_MEM}.14.0", "4", "memory"),
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

        # ENTITY-SENSOR-MIB entPhySensorValue (1.3.6.1.2.1.99.1.1.1.4.*)
        # Values stored ×10 so 1 decimal place survives integer encoding.
        _ESM = "1.3.6.1.2.1.99.1.1.1"
        entries += [
            _oid_entry(f"{_ESM}.4.1", "2", str(int(device.inlet_temp * 10))),  # inlet/chassis temp
            _oid_entry(f"{_ESM}.4.2", "2", str(int(device.cpu_temp   * 10))),  # CPU die temp
        ]

        return entries

    # ------------------------------------------------------------------ #
    #  UPS OIDs (UPS-MIB standard + enterprise)                           #
    # ------------------------------------------------------------------ #

    def _ups_entries(self, device: Device) -> List[OidEntry]:
        """UPS-MIB standard + enterprise status OIDs for UPS devices."""
        entries: List[OidEntry] = []
        _UPS_MIB = "1.3.6.1.2.1.33.1"
        _UPS_ENT = "1.3.6.1.4.1.99999.4"

        # UPS-MIB: battery group
        entries += [
            _oid_entry(f"{_UPS_MIB}.2.1.0",     "2",  "2"),      # upsBatteryStatus: 2=normal
            _oid_entry(f"{_UPS_MIB}.2.2.0",      "2",  "0"),      # upsSecondsOnBattery
            _oid_entry(f"{_UPS_MIB}.2.3.0",      "2",  "2"),      # upsBatteryStatus (alias)
            _oid_entry(f"{_UPS_MIB}.2.4.0",      "2",  "60"),     # upsEstimatedMinutesRemaining
            _oid_entry(f"{_UPS_MIB}.2.5.0",      "2",  "100"),    # upsEstimatedChargeRemaining %
            _oid_entry(f"{_UPS_MIB}.2.6.0",      "2",  "5440"),   # upsBatteryVoltage (544.0V x10)
            _oid_entry(f"{_UPS_MIB}.2.7.0",      "2",  "0"),      # upsBatteryCurrent (0A)
            _oid_entry(f"{_UPS_MIB}.2.8.0",      "2",  "25"),     # upsBatteryTemperature C
        ]
        # UPS-MIB: input group (1 line table)
        entries += [
            _oid_entry(f"{_UPS_MIB}.3.1.0",      "2",  "1"),      # upsInputNumLines
            _oid_entry(f"{_UPS_MIB}.3.3.1.1.1",  "2",  "1"),      # upsInputLineIndex
            _oid_entry(f"{_UPS_MIB}.3.3.1.2.1",  "2",  "500"),    # upsInputFrequency x10 Hz (50.0)
            _oid_entry(f"{_UPS_MIB}.3.3.1.3.1",  "2",  "400"),    # upsInputVoltage V (400 V 3-phase)
            _oid_entry(f"{_UPS_MIB}.3.3.1.4.1",  "2",  "100"),    # upsInputCurrent x10 A
            _oid_entry(f"{_UPS_MIB}.3.3.1.5.1",  "2",  "2200"),   # upsInputTruePower W
        ]
        # UPS-MIB: output group
        entries += [
            _oid_entry(f"{_UPS_MIB}.4.1.0",      "2",  "3"),      # upsOutputSource: 3=normal
            _oid_entry(f"{_UPS_MIB}.4.2.0",      "2",  "500"),    # upsOutputFrequency x10 Hz
            _oid_entry(f"{_UPS_MIB}.4.3.0",      "2",  "1"),      # upsOutputNumLines
            _oid_entry(f"{_UPS_MIB}.4.4.1.1.1",  "2",  "1"),      # upsOutputLineIndex
            _oid_entry(f"{_UPS_MIB}.4.4.1.2.1",  "2",  "220"),    # upsOutputVoltage V
            _oid_entry(f"{_UPS_MIB}.4.4.1.3.1",  "2",  "0"),      # upsOutputCurrent x10 A
            _oid_entry(f"{_UPS_MIB}.4.4.1.4.1",  "2",  "0"),      # upsOutputPower W
            _oid_entry(f"{_UPS_MIB}.4.4.1.5.1",  "2",  "0"),      # upsOutputPercentLoad (alias)
            _oid_entry(f"{_UPS_MIB}.4.4.1.6.1",  "2",  "40"),     # upsOutputPercentLoad %
        ]
        # Enterprise UPS status OIDs (1.3.6.1.4.1.99999.4.x)
        entries += [
            _oid_entry(f"{_UPS_ENT}.1.0", "2", "2"),  # upsFanStatus (2=ok)
            _oid_entry(f"{_UPS_ENT}.2.0", "2", "2"),  # upsChargerStatus (2=ok)
            _oid_entry(f"{_UPS_ENT}.3.0", "2", "2"),  # upsRectifierStatus (2=ok)
            _oid_entry(f"{_UPS_ENT}.4.0", "2", "2"),  # upsPhaseStatus (2=ok)
            _oid_entry(f"{_UPS_ENT}.5.0", "2", "2"),  # upsBatteryStatusEx (2=normal)
        ]
        # Enterprise UPS extended power metrics (1.3.6.1.4.1.99999.4.6-.10)
        entries += [
            _oid_entry(f"{_UPS_ENT}.6.0",  "2", "1"),     # upsOperatingMode (1=online,2=battery,3=bypass,4=eco,5=standby)
            _oid_entry(f"{_UPS_ENT}.7.0",  "2", "1"),     # upsBypassStatus (1=not-on-bypass,2=on-bypass)
            _oid_entry(f"{_UPS_ENT}.8.0",  "2", "100"),   # upsBatteryHealthPercent %
            _oid_entry(f"{_UPS_ENT}.9.0",  "2", "1200"),  # upsOutputApparentPower VA
            _oid_entry(f"{_UPS_ENT}.10.0", "2", "0"),     # upsEnergyOutputKWh (kWh x10)
            _oid_entry(f"{_UPS_ENT}.11.0", "2", "8"),     # upsBatteryRuntimeMin (min remaining)
        ]
        return entries

    # ------------------------------------------------------------------ #
    #  PDU OIDs (enterprise)                                               #
    # ------------------------------------------------------------------ #

    def _pdu_entries(self, device: Device) -> List[OidEntry]:
        """PDU status OIDs for PDU/floor_pdu devices.

        APC and Raritan units seed their own MIB's tables (PowerNet rPDU2 /
        PDU2-MIB measurements) — those are the OIDs a real NMS walks, and the
        live patch path in _apply_live_state writes the same rows. Only PDU
        vendors with no verified MIB fall back to the simulator's placeholder
        enterprise tree.
        """
        entries: List[OidEntry] = []
        _PDU_ENT = "1.3.6.1.4.1.99999.5"
        vkey = _vendor_oids.vendor_key(device.vendor)
        if vkey == "apc":
            A = _vendor_oids.APC
            return [
                _oid_entry(f"{A['rpdu2Power']}.1",        "2", "209"),  # 2.09 kW ×100
                _oid_entry(f"{A['rpdu2ApparentPwr']}.1",  "2", "220"),  # 2.20 kVA ×100
                _oid_entry(f"{A['rpdu2PowerFactor']}.1",  "2", "95"),   # 0.95 ×100
                _oid_entry(f"{A['rpdu2Energy']}.1",       "2", "0"),    # kWh ×10
                _oid_entry(f"{A['rpdu2LoadState']}.1",    "2", "2"),    # normal
                _oid_entry(f"{A['rpdu2PhaseCurrent']}.1", "2", "100"),  # 10.0 A ×10
                _oid_entry(f"{A['rpdu2PhaseVoltage']}.1", "2", "220"),
                _oid_entry(f"{A['rpdu2PhaseState']}.1",   "2", "2"),
                _oid_entry(f"{A['rpdu2BankState']}.1",    "2", "2"),
                _oid_entry(f"{A['rpdu2BankCurrent']}.1",  "2", "100"),
                _oid_entry(f"{A['rpdu2OutletState']}.1",  "2", "1"),    # on
                _oid_entry(f"{A['rpdu2SensorTempC']}.1",  "2", "230"),  # 23.0 °C ×10
                _oid_entry(f"{A['rpdu2SensorHumid']}.1",  "2", "45"),   # %
                _oid_entry(f"{A['identName']}.0",         "4", device.name),
                _oid_entry(f"{A['identSerial']}.0",       "4", f"SN-{device.name}"),
            ]
        if vkey == "raritan":
            R  = _vendor_oids.RARITAN
            ST = _vendor_oids.RARITAN_SENSOR_TYPE
            SS = _vendor_oids.RARITAN_SENSOR_STATE
            seed = {
                ST["current"]:       ("10000", 3), ST["voltage"]:      ("220000", 3),
                ST["activePower"]:   ("2090", 0),  ST["apparentPower"]: ("2200", 0),
                ST["powerFactor"]:   ("95", 2),    ST["activeEnergy"]:  ("0", 0),
                ST["frequency"]:     ("500", 1),
            }
            rows: List[OidEntry] = []
            for stype, (val, dec) in seed.items():
                rows += [
                    _oid_entry(f"{R['inletValue']}.1.1.{stype}",    "66", val),
                    _oid_entry(f"{R['inletState']}.1.1.{stype}",    "2",  str(SS["normal"])),
                    _oid_entry(f"{R['inletDecimals']}.1.1.{stype}", "66", str(dec)),
                ]
            rows += [
                _oid_entry(f"{R['outletValue']}.1.1.{ST['current']}", "66", "10000"),
                _oid_entry(f"{R['outletState']}.1.1.{ST['onOff']}",   "2",  str(SS["on"])),
                _oid_entry(f"{R['ocpState']}.1.1.{ST['trip']}",       "2",  str(SS["closed"])),
                _oid_entry(f"{R['externalValue']}.1.1",    "66", "230"),
                _oid_entry(f"{R['externalState']}.1.1",    "2",  str(SS["normal"])),
                _oid_entry(f"{R['externalDecimals']}.1.1", "66", "1"),
                _oid_entry(f"{R['externalType']}.1.1",     "2",  str(ST["temperature"])),
                _oid_entry(f"{R['externalValue']}.1.2",    "66", "450"),
                _oid_entry(f"{R['externalState']}.1.2",    "2",  str(SS["normal"])),
                _oid_entry(f"{R['externalDecimals']}.1.2", "66", "1"),
                _oid_entry(f"{R['externalType']}.1.2",     "2",  str(ST["humidity"])),
                _oid_entry(f"{R['pduName']}.1",   "4", device.name),
                _oid_entry(f"{R['pduModel']}.1",  "4", getattr(device, "model_name", "") or "PX3"),
                _oid_entry(f"{R['pduSerial']}.1", "4", f"SN-{device.name}"),
            ]
            return rows
        entries += [
            _oid_entry(f"{_PDU_ENT}.1.0",  "2",  "45"),   # pduLoadPercent %
            _oid_entry(f"{_PDU_ENT}.2.0",  "2",  "220"),  # pduVoltage V
            _oid_entry(f"{_PDU_ENT}.3.0",  "2",  "95"),   # pduPowerFactor x100 (0.95)
            _oid_entry(f"{_PDU_ENT}.4.0",  "2",  "2"),    # pduPhaseImbalancePercent
            _oid_entry(f"{_PDU_ENT}.5.0",  "2",  "1"),    # pduOutletStatus (1=on)
            _oid_entry(f"{_PDU_ENT}.6.0",  "2",  "1"),    # pduBreakerStatus (1=ok)
            _oid_entry(f"{_PDU_ENT}.7.0",  "2",  "1"),    # pduOutletFailure (1=ok)
            _oid_entry(f"{_PDU_ENT}.8.0",  "2",  "1"),    # pduSmokeDetected (1=no)
            _oid_entry(f"{_PDU_ENT}.9.0",  "2",  "100"),  # pduOutletCurrent x10 A (10.0A)
            _oid_entry(f"{_PDU_ENT}.10.0", "2",  "1"),    # pduGroundFault (1=no)
            _oid_entry(f"{_PDU_ENT}.11.0", "2",  "2090"), # pduRealPower W (220V × 10A × 0.95)
            _oid_entry(f"{_PDU_ENT}.12.0", "2",  "2200"), # pduApparentPower VA (220V × 10A)
            _oid_entry(f"{_PDU_ENT}.13.0", "2",  "0"),    # pduEnergyKWh x10
            _oid_entry(f"{_PDU_ENT}.14.0", "2",  "500"),  # pduFrequency x10 Hz (50.0)
            _oid_entry(f"{_PDU_ENT}.15.0", "2",  "230"),  # pduTemperature x10 °C (23.0)
            _oid_entry(f"{_PDU_ENT}.16.0", "2",  "450"),  # pduHumidity x10 % (45.0)
            _oid_entry(f"{_PDU_ENT}.17.0", "2",  "2090"), # pduOutletPower W (per-outlet)
        ]
        return entries

    # ------------------------------------------------------------------ #
    #  PDU per-outlet table (enterprise 1.3.6.1.4.1.99999.5.20)          #
    # ------------------------------------------------------------------ #

    def _pdu_outlet_entries(self, device: Device, topology: TopologyEngine) -> List[OidEntry]:
        """Per-outlet SNMP table for PDU/floor_pdu devices.

        One row per RECEPTACLE ON THE STRIP, indexed by the outlet number the cord
        actually terminates on:
          .20.1.1.{N}  pduOutletIndex   — INTEGER
          .20.1.2.{N}  pduOutletName    — DisplayString (load name, or "Outlet N")
          .20.1.3.{N}  pduOutletStatus  — INTEGER 1=on 2=off
          .20.1.4.{N}  pduOutletCurrentX10 — INTEGER (A × 10)
          .20.1.5.{N}  pduOutletPowerW  — INTEGER (W)

        This table used to rank the connected devices by name and number them 1..M,
        which meant outlet 3 in the table was whatever sorted third, not the outlet
        silk-screened 3 on the unit — and every empty receptacle vanished, so the
        walk ended at the last USED outlet. Both are wrong against real gear and
        against anything consuming the table: an NMS that walks outlets 1..N (openDCIM
        does exactly this, and stops at the first gap) reads a truncated strip whose
        rows point at the wrong receptacles.

        An unplugged outlet is still a row and still ENERGISED — a rack PDU does not
        de-power a receptacle because nothing is in it, and only a switched SKU can
        turn one off at all. It reads zero current, which is how a metered PDU reports
        an idle outlet.
        """
        entries: List[OidEntry] = []
        # Typical power draw (W) by device type — fallback when power_draw_w unset
        _DEFAULT_W = {
            DeviceType.SERVER: 450, DeviceType.SWITCH: 150, DeviceType.ROUTER: 300,
            DeviceType.FIREWALL: 400, DeviceType.LOAD_BALANCER: 350,
            DeviceType.OOB_SWITCH: 80, DeviceType.SENSOR: 10,
        }
        try:
            loads = topology.outlet_loads(device.id)
        except Exception:
            loads = {}

        # The receptacles that physically exist. A SKU missing from the outlet
        # catalog models none (device_manager._generate_outlets refuses to invent
        # them), and a floor PDU has none at all — its loads land on breakers, not
        # receptacles. In both cases fall back to just the terminated outlets rather
        # than publishing an empty table.
        if device.outlets:
            indices = [o.index for o in device.outlets]
        else:
            indices = sorted(loads)

        _pdu_volt = 220.0
        for n in indices:
            load_ref = loads.get(n)
            load = topology.get_device(load_ref["load_id"]) if load_ref else None
            if load is None:
                # Energised, nothing drawing on it.
                entries += [
                    _oid_entry(f"{_PDU_OUTLET_ENT}.1.{n}", "2", str(n)),
                    _oid_entry(f"{_PDU_OUTLET_ENT}.2.{n}", "4", f"Outlet {n}"),
                    _oid_entry(f"{_PDU_OUTLET_ENT}.3.{n}", "2", "1"),
                    _oid_entry(f"{_PDU_OUTLET_ENT}.4.{n}", "2", "0"),
                    _oid_entry(f"{_PDU_OUTLET_ENT}.5.{n}", "2", "0"),
                ]
                continue

            pwr_w = int(getattr(load, "power_draw_w", 0) or 0)
            if pwr_w <= 0:
                pwr_w = _DEFAULT_W.get(load.device_type, 200)
            # A dual-corded load is fed from TWO strips and splits its draw across
            # them; charging the full chassis draw to the outlet on each one double-
            # counts the rack. Real 1+1 supplies do not share exactly 50/50, but an
            # even split is far closer than 100/100 — and the failure case (survivor
            # carries everything) is a fault to model, not the steady state.
            try:
                cords = max(1, len(topology.power_feeds(load.id)))
            except Exception:
                cords = 1
            pwr_w = int(round(pwr_w / cords))
            cur_x10 = int(round(pwr_w / _pdu_volt * 10)) if _pdu_volt > 0 else 0
            entries += [
                _oid_entry(f"{_PDU_OUTLET_ENT}.1.{n}", "2", str(n)),
                # The outlet's NAME is the receptacle, not its load — for both an
                # occupied and an empty outlet.
                #
                # On real gear this field is operator-configured and ships as
                # "Outlet <n>". Publishing the connected device's name here asserts
                # that somebody has labelled all 2712 receptacles by hand, and it
                # makes any consumer that adopts these names disagree with its own
                # cabling record: openDCIM's "Refresh Port Names" writes this string
                # into the outlet Label, so its Device Port column rendered the load
                # name and duplicated the Device column beside it. What is plugged in
                # belongs in the DCIM's connection, which already holds it; what
                # belongs here is the number printed on the strip.
                _oid_entry(f"{_PDU_OUTLET_ENT}.2.{n}", "4", f"Outlet {n}"),
                _oid_entry(f"{_PDU_OUTLET_ENT}.3.{n}", "2", "1"),           # status=on
                _oid_entry(f"{_PDU_OUTLET_ENT}.4.{n}", "2", str(cur_x10)),  # current ×10 A
                _oid_entry(f"{_PDU_OUTLET_ENT}.5.{n}", "2", str(pwr_w)),    # power W
            ]
        return entries

    # ------------------------------------------------------------------ #
    #  Generator OIDs (enterprise 1.3.6.1.4.1.99999.7)                   #
    # ------------------------------------------------------------------ #

    def _generator_entries(self, device: Device) -> List[OidEntry]:
        """Generator status OIDs — fuel, runtime, load, status, phase voltages."""
        entries: List[OidEntry] = []
        _GEN_ENT = "1.3.6.1.4.1.99999.7"
        entries += [
            _oid_entry(f"{_GEN_ENT}.1.0",  "2",  "80"),   # genFuelLevelPercent %
            _oid_entry(f"{_GEN_ENT}.2.0",  "2",  "0"),    # genRunHours (hours)
            # genStatus 1=standby 2=running 3=fault 4=cranking 5=cooldown.
            # Cranking and cooldown are distinct because in both the engine turns
            # but carries no load — an operator watching only "running" would
            # misread a post-retransfer cooldown as the site still being on generator.
            _oid_entry(f"{_GEN_ENT}.3.0",  "2",  "1"),    # genStatus
            _oid_entry(f"{_GEN_ENT}.4.0",  "2",  "0"),    # genOutputLoadPercent %
            _oid_entry(f"{_GEN_ENT}.5.0",  "2",  "0"),    # genOutputKW (kW)
            _oid_entry(f"{_GEN_ENT}.6.0",  "2",  "4000"), # genOutputVoltagePhA x10 V (400.0)
            _oid_entry(f"{_GEN_ENT}.7.0",  "2",  "4000"), # genOutputVoltagePhB x10 V
            _oid_entry(f"{_GEN_ENT}.8.0",  "2",  "4000"), # genOutputVoltagePhC x10 V
            _oid_entry(f"{_GEN_ENT}.9.0",  "2",  "500"),  # genOutputFrequency x10 Hz (50.0)
            _oid_entry(f"{_GEN_ENT}.10.0", "2",  "1"),    # genCoolantTempOk (1=ok,2=warning)
            _oid_entry(f"{_GEN_ENT}.11.0", "2",  "1"),    # genOilPressureOk (1=ok,2=warning)
            _oid_entry(f"{_GEN_ENT}.12.0", "2",  "1"),    # genBatteryStatus (1=ok,2=fault)
            _oid_entry(f"{_GEN_ENT}.13.0", "2",  "0"),    # genStartAttempts (counter)
            _oid_entry(f"{_GEN_ENT}.14.0", "2",  "0"),    # genRuntimeRemainingMin (min)
        ]
        return entries

    # ------------------------------------------------------------------ #
    #  Electrical upstream OIDs (enterprise 1.3.6.1.4.1.99999.8-11)       #
    #                                                                     #
    #  On real gear these points come from different transports: the      #
    #  service-entrance meter and the MCC bucket meters are Modbus, the   #
    #  switchgear is Modbus or SNMP depending on the relay, and the ATS   #
    #  is genuinely SNMP (ASCO ACC card, Eaton ATC-900, APC). The sim     #
    #  serves all four over SNMP, which is the transport it already       #
    #  speaks for power gear — Modbus is not modelled.                    #
    # ------------------------------------------------------------------ #

    _UTIL_ENT = "1.3.6.1.4.1.99999.8"
    _SWGR_ENT = "1.3.6.1.4.1.99999.9"
    _ATS_ENT  = "1.3.6.1.4.1.99999.10"
    _MCC_ENT  = "1.3.6.1.4.1.99999.11"
    _MPP_ENT  = "1.3.6.1.4.1.99999.12"

    def _electrical_updates(self, device: Device, ext: dict) -> dict:
        """Live OID values for the electrical upstream, from the device's ext state.
        Voltages are x10 scaled ints, matching the generator and UPS subtrees."""
        dt = device.device_type
        v10 = lambda k: ("2", str(int(round(float(ext.get(k, 0.0)) * 10))))
        i = lambda k, d=0.0: ("2", str(int(round(float(ext.get(k, d))))))

        pf100 = lambda k: ("2", str(int(round(float(ext.get(k, 0.0)) * 100))))

        if dt == DeviceType.UTILITY_FEED:
            # Schneider ION9000 revenue/PQ meter: system + per-phase V/I, imbalance,
            # THD, kW/kVAR/kVA, PF, peak demand, kWh (all measured quantities).
            E = self._UTIL_ENT
            return {
                f"{E}.1.0": ("2", "1" if ext.get("util_status") == "normal" else "2"),
                f"{E}.2.0": v10("util_voltage"),
                f"{E}.3.0": i("util_current"),
                f"{E}.4.0": v10("util_frequency"),
                f"{E}.5.0": i("util_kw"),
                f"{E}.6.0": pf100("util_power_factor"),
                f"{E}.7.0": i("util_energy_kwh"),
                # per-phase line-to-neutral voltage (x10) and line current
                f"{E}.8.0": v10("util_va"),
                f"{E}.9.0": v10("util_vb"),
                f"{E}.10.0": v10("util_vc"),
                f"{E}.11.0": i("util_ia"),
                f"{E}.12.0": i("util_ib"),
                f"{E}.13.0": i("util_ic"),
                # power quality (x10 for %/THD) + reactive/apparent power + peak demand
                f"{E}.14.0": v10("util_phase_imbalance"),
                f"{E}.15.0": v10("util_thd_v"),
                f"{E}.16.0": v10("util_thd_i"),
                f"{E}.17.0": i("util_kvar"),
                f"{E}.18.0": i("util_kva"),
                f"{E}.19.0": i("util_peak_kw"),
            }
        if dt == DeviceType.SWITCHGEAR:
            # Eaton Magnum DS / ASCO paralleling — Digitrip energy-metering trip unit:
            # system + per-phase V/I, imbalance, Hz, kW/kVAR/kVA, PF, kWh, % rating,
            # breaker, source. (No THD/peak-demand — that is the ION9000's domain.)
            E = self._SWGR_ENT
            return {
                f"{E}.1.0": ("2", "1" if ext.get("swgr_bus_status") == "energized" else "2"),
                f"{E}.2.0": v10("swgr_voltage"),
                f"{E}.3.0": i("swgr_current"),
                f"{E}.4.0": i("swgr_kw"),
                f"{E}.5.0": i("swgr_load_pct"),
                f"{E}.6.0": ("2", "1" if ext.get("swgr_breaker_status") == "closed" else "2"),
                f"{E}.7.0": ("2", "2" if ext.get("swgr_source") == "generator" else "1"),
                # per-phase line-to-neutral voltage (x10) and line current
                f"{E}.8.0": v10("swgr_va"),
                f"{E}.9.0": v10("swgr_vb"),
                f"{E}.10.0": v10("swgr_vc"),
                f"{E}.11.0": i("swgr_ia"),
                f"{E}.12.0": i("swgr_ib"),
                f"{E}.13.0": i("swgr_ic"),
                # imbalance % (x10), frequency (x10), reactive/apparent power, PF, kWh
                f"{E}.14.0": v10("swgr_phase_imbalance"),
                f"{E}.15.0": v10("swgr_frequency"),
                f"{E}.16.0": i("swgr_kvar"),
                f"{E}.17.0": i("swgr_kva"),
                f"{E}.18.0": pf100("swgr_power_factor"),
                f"{E}.19.0": i("swgr_energy_kwh"),
            }
        if dt == DeviceType.ATS:
            # ASCO 7000: position, source availabilities, both source voltages, active +
            # per-source Hz, transfer count, time-on-emergency. A switch — no kW/load.
            E = self._ATS_ENT
            pos = {"normal": 1, "emergency": 2, "none": 3}.get(ext.get("ats_position"), 3)
            return {
                f"{E}.1.0": ("2", str(pos)),
                f"{E}.2.0": ("2", "1" if ext.get("ats_normal_available") == "yes" else "2"),
                f"{E}.3.0": ("2", "1" if ext.get("ats_emergency_available") == "yes" else "2"),
                f"{E}.4.0": v10("ats_normal_voltage"),
                f"{E}.5.0": v10("ats_emergency_voltage"),
                f"{E}.6.0": v10("ats_frequency"),          # active source Hz
                f"{E}.7.0": i("ats_transfer_count"),
                f"{E}.8.0": i("ats_time_on_emergency"),
                f"{E}.9.0": v10("ats_normal_frequency"),
                f"{E}.10.0": v10("ats_emergency_frequency"),
            }
        if dt == DeviceType.MPP:
            # Schneider PowerLogic PM5000 panel-main meter: system + per-phase V/I,
            # imbalance, Hz, kW/kVAR/kVA, PF, % load, kWh.
            E = self._MPP_ENT
            return {
                f"{E}.1.0": ("2", "1" if ext.get("mpp_status") == "energized" else "2"),
                f"{E}.2.0": v10("mpp_voltage"),
                f"{E}.3.0": i("mpp_kw"),
                f"{E}.4.0": i("mpp_load_pct"),
                f"{E}.5.0": i("mpp_energy_kwh"),
                f"{E}.6.0": i("mpp_current"),
                # per-phase line-to-neutral voltage (x10) and line current
                f"{E}.7.0": v10("mpp_va"),
                f"{E}.8.0": v10("mpp_vb"),
                f"{E}.9.0": v10("mpp_vc"),
                f"{E}.10.0": i("mpp_ia"),
                f"{E}.11.0": i("mpp_ib"),
                f"{E}.12.0": i("mpp_ic"),
                # imbalance % (x10), frequency (x10), reactive/apparent power, PF
                f"{E}.13.0": v10("mpp_phase_imbalance"),
                f"{E}.14.0": v10("mpp_frequency"),
                f"{E}.15.0": i("mpp_kvar"),
                f"{E}.16.0": i("mpp_kva"),
                f"{E}.17.0": pf100("mpp_power_factor"),
            }
        # Eaton Freedom 2100 MCC with metered main (Power Xpert/IQ): system + per-phase
        # V/I, imbalance, Hz, kW/kVAR/kVA, motor PF, kWh, % load, tie, source.
        E = self._MCC_ENT
        return {
            f"{E}.1.0": ("2", "1" if ext.get("mcc_status") == "energized" else "2"),
            f"{E}.2.0": v10("mcc_voltage"),
            f"{E}.3.0": i("mcc_current"),
            f"{E}.4.0": i("mcc_kw"),
            f"{E}.5.0": i("mcc_load_pct"),
            f"{E}.6.0": ("2", "2" if ext.get("mcc_tie") == "closed" else "1"),
            f"{E}.7.0": ("2", {"normal": "1", "tie": "2"}.get(ext.get("mcc_source"), "3")),
            # per-phase line-to-neutral voltage (x10) and line current
            f"{E}.8.0": v10("mcc_va"),
            f"{E}.9.0": v10("mcc_vb"),
            f"{E}.10.0": v10("mcc_vc"),
            f"{E}.11.0": i("mcc_ia"),
            f"{E}.12.0": i("mcc_ib"),
            f"{E}.13.0": i("mcc_ic"),
            # imbalance % (x10), frequency (x10), reactive/apparent power, PF, kWh
            f"{E}.14.0": v10("mcc_phase_imbalance"),
            f"{E}.15.0": v10("mcc_frequency"),
            f"{E}.16.0": i("mcc_kvar"),
            f"{E}.17.0": i("mcc_kva"),
            f"{E}.18.0": pf100("mcc_power_factor"),
            f"{E}.19.0": i("mcc_energy_kwh"),
        }

    def _utility_entries(self, device: Device) -> List[OidEntry]:
        """Utility service entrance — Schneider ION9000 revenue/PQ meter points."""
        E = self._UTIL_ENT
        return [
            _oid_entry(f"{E}.1.0", "2", "1"),     # utilStatus (1=normal,2=failed)
            _oid_entry(f"{E}.2.0", "2", "4000"),  # utilVoltage x10 V (400.0)
            _oid_entry(f"{E}.3.0", "2", "0"),     # utilCurrent A
            _oid_entry(f"{E}.4.0", "2", "500"),   # utilFrequency x10 Hz (50.0)
            _oid_entry(f"{E}.5.0", "2", "0"),     # utilKW (kW)
            _oid_entry(f"{E}.6.0", "2", "97"),    # utilPowerFactor x100
            _oid_entry(f"{E}.7.0", "2", "0"),     # utilEnergyKWh (cumulative)
        ]

    def _switchgear_entries(self, device: Device) -> List[OidEntry]:
        """LV main / generator paralleling board — Digitrip trip-unit metering."""
        E = self._SWGR_ENT
        return [
            _oid_entry(f"{E}.1.0", "2", "1"),     # swgrBusStatus (1=energized,2=dead)
            _oid_entry(f"{E}.2.0", "2", "4000"),  # swgrBusVoltage x10 V
            _oid_entry(f"{E}.3.0", "2", "0"),     # swgrCurrent A
            _oid_entry(f"{E}.4.0", "2", "0"),     # swgrKW (kW)
            _oid_entry(f"{E}.5.0", "2", "0"),     # swgrLoadPercent % of breaker rating
            _oid_entry(f"{E}.6.0", "2", "1"),     # swgrMainBreaker (1=closed,2=open)
            _oid_entry(f"{E}.7.0", "2", "1"),     # swgrSource (1=utility,2=generator)
        ]

    def _ats_entries(self, device: Device) -> List[OidEntry]:
        """ASCO 7000 transfer switch — sources, position, transfer counters."""
        E = self._ATS_ENT
        return [
            _oid_entry(f"{E}.1.0", "2", "1"),     # atsPosition (1=normal,2=emergency,3=none)
            _oid_entry(f"{E}.2.0", "2", "1"),     # atsNormalSourceAvailable (1=yes,2=no)
            _oid_entry(f"{E}.3.0", "2", "2"),     # atsEmergencySourceAvailable (1=yes,2=no)
            _oid_entry(f"{E}.4.0", "2", "4000"),  # atsNormalVoltage x10 V
            _oid_entry(f"{E}.5.0", "2", "0"),     # atsEmergencyVoltage x10 V
            _oid_entry(f"{E}.6.0", "2", "500"),   # atsFrequency x10 Hz
            _oid_entry(f"{E}.7.0", "2", "0"),     # atsTransferCount (counter)
            _oid_entry(f"{E}.8.0", "2", "0"),     # atsTimeOnEmergency (minutes)
        ]

    def _mcc_entries(self, device: Device) -> List[OidEntry]:
        """Eaton Freedom 2100 MCC (metered main) — the mechanical bus."""
        E = self._MCC_ENT
        return [
            _oid_entry(f"{E}.1.0", "2", "1"),     # mccStatus (1=energized,2=dead)
            _oid_entry(f"{E}.2.0", "2", "4000"),  # mccBusVoltage x10 V
            _oid_entry(f"{E}.3.0", "2", "0"),     # mccCurrent A
            _oid_entry(f"{E}.4.0", "2", "0"),     # mccKW (kW)
            _oid_entry(f"{E}.5.0", "2", "0"),     # mccLoadPercent %
            _oid_entry(f"{E}.6.0", "2", "1"),     # mccTieBreaker (1=open,2=closed)
            _oid_entry(f"{E}.7.0", "2", "1"),     # mccSource (1=own ATS,2=tie,3=none)
        ]

    def _mpp_entries(self, device: Device) -> List[OidEntry]:
        """Mechanical power panel (metered panelboard main) — fed from an MCC."""
        E = self._MPP_ENT
        return [
            _oid_entry(f"{E}.1.0", "2", "1"),     # mppStatus (1=energized,2=dead)
            _oid_entry(f"{E}.2.0", "2", "4000"),  # mppBusVoltage x10 V
            _oid_entry(f"{E}.3.0", "2", "0"),     # mppKW (kW)
            _oid_entry(f"{E}.4.0", "2", "0"),     # mppLoadPercent %
            _oid_entry(f"{E}.5.0", "2", "0"),     # mppEnergyKWh (cumulative)
        ]

    # ------------------------------------------------------------------ #
    #  Chiller-plant OIDs (enterprise 1.3.6.1.4.1.99999.20-23)            #
    #  Scaled ints: temps/pressures/flows ×10, percents/kW whole.        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _plant_seed(device: Device) -> int:
        try:
            return int(device.id, 16)
        except (ValueError, TypeError):
            return abs(hash(device.name))

    def _chiller_entries(self, device: Device) -> List[OidEntry]:
        """Water-cooled chiller telemetry — CHW/condenser temps, load, kW, alarms."""
        s = self._plant_seed(device)
        kw = max(1, int(round((device.power_draw_w or 0) / 1000.0)))
        load = 55 + s % 35                       # compressor % RLA 55–89
        chws = 65 + s % 10                        # CHW supply 6.5–7.4 °C ×10
        chwr = chws + 45 + s % 15                 # CHW return ≈ +4.5–6.0 °C
        b = "1.3.6.1.4.1.99999.20"
        return [
            _oid_entry(f"{b}.1.0",  "2", str(chws)),          # chwSupplyTemp ×10 °C
            _oid_entry(f"{b}.2.0",  "2", str(chwr)),          # chwReturnTemp ×10 °C
            _oid_entry(f"{b}.3.0",  "2", "70"),               # chwSetpoint ×10 °C (7.0)
            _oid_entry(f"{b}.4.0",  "2", str(200 + s % 120)), # chwFlow ×10 L/s
            _oid_entry(f"{b}.5.0",  "2", str(295 + s % 20)),  # condSupplyTemp ×10 °C (~29.5–31.5)
            _oid_entry(f"{b}.6.0",  "2", str(345 + s % 20)),  # condReturnTemp ×10 °C (~34.5–36.5)
            _oid_entry(f"{b}.7.0",  "2", str(load)),          # compressorLoad %
            _oid_entry(f"{b}.8.0",  "2", "2"),                # chillerStatus 1=off 2=running 3=fault
            _oid_entry(f"{b}.9.0",  "2", str(kw)),            # activePower kW
            _oid_entry(f"{b}.10.0", "2", "800"),              # ratedCoolingCapacity kW (nameplate, 800kW-class)
            _oid_entry(f"{b}.11.0", "2", str(50 + s % 15)),   # COP ×10 (5.0–6.4)
            _oid_entry(f"{b}.12.0", "2", str(340 + s % 40)),  # evapPressure kPa
            _oid_entry(f"{b}.13.0", "2", str(880 + s % 80)),  # condPressure kPa
            _oid_entry(f"{b}.14.0", "65", str(10000 + s % 50000)),  # runHours (Counter32)
            _oid_entry(f"{b}.15.0", "65", str(s % 2000)),     # startCount (Counter32)
            _oid_entry(f"{b}.16.0", "2", "1"),                # alarmHighPressure 1=ok 2=alarm
            _oid_entry(f"{b}.17.0", "2", "1"),                # alarmLowEvapTemp
            _oid_entry(f"{b}.18.0", "2", "1"),                # alarmFlowLoss
            _oid_entry(f"{b}.19.0", "4", "R-134a"),           # refrigerantType
        ]

    def _pump_entries(self, device: Device) -> List[OidEntry]:
        """CHW / condenser-water VFD pump telemetry."""
        s = self._plant_seed(device)
        kw10 = max(1, int(round((device.power_draw_w or 0) / 100.0)))  # kW ×10
        speed = 55 + s % 40
        b = "1.3.6.1.4.1.99999.21"
        return [
            _oid_entry(f"{b}.1.0",  "2", "2"),                # runStatus 1=off 2=run
            _oid_entry(f"{b}.2.0",  "2", str(speed)),         # speed %
            _oid_entry(f"{b}.3.0",  "2", str(180 + s % 120)), # flow ×10 L/s
            _oid_entry(f"{b}.4.0",  "2", str(420 + s % 80)),  # dischargePressure kPa
            _oid_entry(f"{b}.5.0",  "2", str(120 + s % 40)),  # suctionPressure kPa
            _oid_entry(f"{b}.6.0",  "2", str(280 + s % 60)),  # diffPressure kPa
            _oid_entry(f"{b}.7.0",  "2", str(kw10)),          # motorPower kW ×10
            _oid_entry(f"{b}.8.0",  "2", str(45 + s % 20)),   # motorTemp °C
            _oid_entry(f"{b}.9.0",  "2", str(int(speed * 0.5)),),  # vfdFrequency ×10 Hz (~speed×0.5)
            _oid_entry(f"{b}.10.0", "65", str(8000 + s % 40000)),  # runHours
            _oid_entry(f"{b}.11.0", "2", "1"),                # alarmFault 1=ok 2=fault
            _oid_entry(f"{b}.12.0", "2", "1"),                # alarmLowFlow
        ]

    def _cooling_tower_entries(self, device: Device) -> List[OidEntry]:
        """Cooling-tower telemetry — fan, basin, condenser-water temps."""
        s = self._plant_seed(device)
        kw10 = max(1, int(round((device.power_draw_w or 0) / 100.0)))
        b = "1.3.6.1.4.1.99999.22"
        return [
            _oid_entry(f"{b}.1.0",  "2", "2"),                # fanStatus 1=off 2=run
            _oid_entry(f"{b}.2.0",  "2", str(50 + s % 45)),   # fanSpeed %
            _oid_entry(f"{b}.3.0",  "2", str(265 + s % 30)),  # basinTemp ×10 °C
            _oid_entry(f"{b}.4.0",  "2", str(345 + s % 20)),  # condWaterIn ×10 °C (hot return)
            _oid_entry(f"{b}.5.0",  "2", str(295 + s % 20)),  # condWaterOut ×10 °C (cooled)
            _oid_entry(f"{b}.6.0",  "2", str(kw10)),          # fanMotorPower kW ×10
            _oid_entry(f"{b}.7.0",  "2", str(60 + s % 30)),   # basinLevel %
            _oid_entry(f"{b}.8.0",  "2", str(40 + s % 60)),   # makeupFlow ×10 L/min
            _oid_entry(f"{b}.9.0",  "2", str(5 + s % 25)),    # vibration ×10 mm/s
            _oid_entry(f"{b}.10.0", "65", str(9000 + s % 40000)),  # runHours
            _oid_entry(f"{b}.11.0", "2", "1"),                # alarmHighVibration
            _oid_entry(f"{b}.12.0", "2", "1"),                # alarmLowBasin
        ]

    def _valve_entries(self, device: Device) -> List[OidEntry]:
        """Control / isolation valve actuator telemetry."""
        s = self._plant_seed(device)
        pos = 30 + s % 65
        b = "1.3.6.1.4.1.99999.23"
        return [
            _oid_entry(f"{b}.1.0", "2", str(pos)),            # position %
            _oid_entry(f"{b}.2.0", "2", str(pos)),            # commandedPosition %
            _oid_entry(f"{b}.3.0", "2", "2"),                 # status 1=closed 2=modulating 3=open 4=fault
            _oid_entry(f"{b}.4.0", "2", str(35 + s % 15)),    # actuatorTemp °C
            _oid_entry(f"{b}.5.0", "65", str(s % 100000)),    # strokeCount
            _oid_entry(f"{b}.6.0", "2", "1"),                 # alarmActuatorFault 1=ok 2=fault
        ]

    def _crah_entries(self, device: Device) -> List[OidEntry]:
        """CRAH air-handler telemetry — air temps, fan, CHW valve, alarms."""
        s = self._plant_seed(device)
        kw = max(1, int(round((device.power_draw_w or 0) / 1000.0)))
        fan = 55 + s % 35
        b = "1.3.6.1.4.1.99999.24"
        return [
            _oid_entry(f"{b}.1.0",  "2", str(215 + s % 20)),  # supplyAirTemp ×10 °C (~21.5–23.5)
            _oid_entry(f"{b}.2.0",  "2", str(310 + s % 25)),  # returnAirTemp ×10 °C (~31–33.5)
            _oid_entry(f"{b}.3.0",  "2", "220"),              # setpoint ×10 °C (22.0)
            _oid_entry(f"{b}.4.0",  "2", str(fan)),           # fanSpeed %
            _oid_entry(f"{b}.5.0",  "2", str(45 + s % 40)),   # chwValve %
            _oid_entry(f"{b}.6.0",  "2", str(50 + s % 35)),   # coolingCapacity %
            _oid_entry(f"{b}.7.0",  "2", str(40 + s % 15)),   # supplyHumidity %RH
            _oid_entry(f"{b}.8.0",  "2", str(70 + s % 25)),   # airflow % of nominal
            _oid_entry(f"{b}.9.0",  "2", str(kw)),            # fanPower kW
            _oid_entry(f"{b}.10.0", "65", str(15000 + s % 40000)),  # runHours
            _oid_entry(f"{b}.11.0", "2", "2"),                # unitStatus 1=off 2=run
            _oid_entry(f"{b}.12.0", "2", "1"),                # alarmHighTemp 1=ok 2=alarm
            _oid_entry(f"{b}.13.0", "2", "1"),                # alarmAirflowLoss
            _oid_entry(f"{b}.14.0", "2", "1"),                # filterDirty 1=clean 2=dirty
        ]

    def _cdu_entries(self, device: Device) -> List[OidEntry]:
        """Coolant Distribution Unit (direct-to-chip) telemetry — TCS loop temps,
        facility-CHW interface, chip heat load, pump, leak/over-temp alarms."""
        s = self._plant_seed(device)
        kw10 = max(1, int(round((device.power_draw_w or 0) / 100.0)))  # pump kW ×10
        tcss = 315 + s % 20                       # TCS supply 31.5–33.5 °C ×10
        tcsr = tcss + 110 + s % 30                # TCS return ≈ +11–14 °C ×10
        heat = 400 + s % 120                      # chip heat removed kW (thermal)
        speed = 55 + s % 35
        b = "1.3.6.1.4.1.99999.25"
        return [
            _oid_entry(f"{b}.1.0",  "2", str(tcss)),          # tcsSupplyTemp ×10 °C
            _oid_entry(f"{b}.2.0",  "2", str(tcsr)),          # tcsReturnTemp ×10 °C
            _oid_entry(f"{b}.3.0",  "2", "320"),              # tcsSetpoint ×10 °C (32.0)
            _oid_entry(f"{b}.4.0",  "2", str(150 + s % 100)), # tcsFlow ×10 L/s
            _oid_entry(f"{b}.5.0",  "2", str(45 + s % 40)),   # facilityChwValve %
            _oid_entry(f"{b}.6.0",  "2", str(130 + s % 90)),  # facilityChwFlow ×10 L/s
            _oid_entry(f"{b}.7.0",  "2", str(heat)),          # heatLoad kW (thermal removed)
            _oid_entry(f"{b}.8.0",  "2", str(kw10)),          # pumpPower kW ×10
            _oid_entry(f"{b}.9.0",  "2", str(speed)),         # pumpSpeed %
            _oid_entry(f"{b}.10.0", "2", str(25 + s % 20)),   # approachTemp ×10 °C (2.5–4.5)
            _oid_entry(f"{b}.11.0", "2", str(25 + s % 25)),   # filterDP kPa
            _oid_entry(f"{b}.12.0", "65", str(8000 + s % 40000)),  # runHours (Counter32)
            _oid_entry(f"{b}.13.0", "2", "2"),                # unitStatus 1=off 2=run
            _oid_entry(f"{b}.14.0", "2", "1"),                # alarmLeak 1=ok 2=leak
            _oid_entry(f"{b}.15.0", "2", "1"),                # alarmHighSupplyTemp 1=ok 2=alarm
            _oid_entry(f"{b}.16.0", "2", "1"),                # alarmPumpFault 1=ok 2=fault
            _oid_entry(f"{b}.17.0", "2", "1"),                # alarmLowFlow 1=ok 2=alarm
            _oid_entry(f"{b}.18.0", "2", str(240 + s % 25)),  # tcsLoopPressure kPa
        ]


    # ------------------------------------------------------------------ #
    #  Environmental sensor OIDs (vendor-specific MIB branches)           #
    # ------------------------------------------------------------------ #

    def _cisco_envmon_temp_entries(self, device: Device) -> List[OidEntry]:
        """CISCO-ENVMON-MIB ciscoEnvMonTemperatureStatusTable.

        Two entries: index 1 = Inlet (chassis inlet air), index 2 = CPU/ASIC.
        State: 1=normal, 2=warning, 3=critical, 4=shutdown, 5=notPresent, 6=notFunctioning.
        """
        inlet = int(round(device.inlet_temp))
        cpu   = int(round(device.cpu_temp))
        s_in  = 2 if device.inlet_temp >= 40 else 1
        s_cpu = 2 if device.cpu_temp   >= 75 else 1
        b = _CISCO_ENVMON_TEMP
        return [
            _oid_entry(f"{b}.1.1", "2",  "1"),
            _oid_entry(f"{b}.2.1", "4",  "Inlet"),
            _oid_entry(f"{b}.3.1", "66", str(inlet)),   # Gauge32, Celsius
            _oid_entry(f"{b}.4.1", "2",  "40"),
            _oid_entry(f"{b}.5.1", "2",  "0"),
            _oid_entry(f"{b}.6.1", "2",  str(s_in)),
            _oid_entry(f"{b}.1.2", "2",  "2"),
            _oid_entry(f"{b}.2.2", "4",  "CPU"),
            _oid_entry(f"{b}.3.2", "66", str(cpu)),     # Gauge32, Celsius
            _oid_entry(f"{b}.4.2", "2",  "75"),
            _oid_entry(f"{b}.5.2", "2",  "0"),
            _oid_entry(f"{b}.6.2", "2",  str(s_cpu)),
        ]

    def _cisco_perf_entries(self, device: Device) -> List[OidEntry]:
        """CISCO-PROCESS-MIB (CPU%) + CISCO-MEMORY-POOL-MIB for Cisco network devices."""
        mem_free = max(0, device.memory_total - device.memory_used)
        return [
            _oid_entry(f"{_CISCO_CPU_MIB}.7.1", "2", str(device.cpu_usage)),              # cpmCPUTotal1minRev %
            _oid_entry(f"{_CISCO_CPU_MIB}.8.1", "2", str(device.cpu_usage)),              # cpmCPUTotal5minRev %
            _oid_entry(f"{_CISCO_MEM_MIB}.2.1", "4", "Processor"),                        # ciscoMemoryPoolName
            _oid_entry(f"{_CISCO_MEM_MIB}.5.1", "2", str(device.memory_used // (1024 * 1024))),  # used MB
            _oid_entry(f"{_CISCO_MEM_MIB}.6.1", "2", str(mem_free // (1024 * 1024))),     # free MB
        ]

    @staticmethod
    def _plant_probe_entries(device: Device) -> "Optional[List[OidEntry]]":
        """OIDs for a chiller-plant header instrument, or None if this SENSOR is an
        ordinary rack environmental probe.

        A header instrument is ONE point — a thermowell in a water header or a
        magnetic flow meter on the main. Falling through to the generic probe tables
        below would publish humidity and dew point alongside it, which is a
        fabrication: there is no hygrometer in a pipe. So the probe tables are
        skipped and the single real reading is served on ENTITY-SENSOR-MIB, which is
        the vendor-neutral place for one instrument with one value — and the right
        model for a BMS-gatewayed point in any case.

        WHAT this device is comes from its IDENTITY (_probe_role: the "Plant " model
        prefix plus the role code leading its name), never from published state.
        That distinction is the whole bug this used to have. The role was read out of
        ext_state, which the store only writes DURING a tick — so at dataset-build
        time, before any tick has run, every plant probe returned None here, fell
        through to the generic Vertiv Geist air tables, and was written to disk as a
        temperature/humidity/dew-point head. The live patcher then correctly asked to
        update ENTITY-SENSOR OIDs, but the patcher only rewrites lines that already
        exist in the .snmprec — so those writes were silently dropped and the Geist
        values were frozen at whatever the build wrote.

        The result was a thermowell in a chilled-water header answering 36.4 C with
        38.8 % RH and a dew point, and a magnetic flow meter answering the SAME
        temperature — fabricated air readings on instruments that measure water, the
        exact failure the rest of this function exists to prevent, and stable enough
        to look real.

        A reading the store has not published yet is served as entPhySensorOperStatus
        2 (unavailable) with value 0, not as a plausible number. That is what a BMS
        gateway reports before its first successful poll, and the first tick replaces
        it with the real one.
        """
        # Circular at import time — device_state_store imports this module's siblings.
        from core.device_state_store import _get_ext_state, _probe_role

        role = _probe_role(device)
        if not role:
            return None
        st = _get_ext_state(device.name)
        b = _ENTITY_SENSOR
        if role == "chw_flow":
            # entPhySensorType 12 = "other" (litres/second has no ENTITY enum),
            # scale 9 = units, precision 2.
            raw, stype, precision, scale = st.get("water_flow_lps"), "12", "2", 100
        else:
            # entPhySensorType 8 = celsius; ×10, so precision 1.
            raw, stype, precision, scale = st.get("water_temp"), "8", "1", 10
        if raw is None:
            value, oper = 0, "2"          # nothing published yet — unavailable
        else:
            value, oper = int(round(float(raw) * scale)), "1"
        return [
            _oid_entry(f"{b}.1.1", "2", stype),        # entPhySensorType
            _oid_entry(f"{b}.2.1", "2", "9"),          # entPhySensorScale = units
            _oid_entry(f"{b}.3.1", "2", precision),    # entPhySensorPrecision
            _oid_entry(f"{b}.4.1", "2", str(value)),   # entPhySensorValue
            _oid_entry(f"{b}.5.1", "2", oper),         # entPhySensorOperStatus
        ]

    def _sensor_entries(self, device: Device) -> List[OidEntry]:
        """Return vendor-specific OIDs for temp, humidity, dewpoint, and airflow."""
        entries: List[OidEntry] = []
        inlet_t10   = int(round(device.inlet_temp * 10))
        humid_t10   = int(round(device.humidity   * 10))
        dewpt_t10   = int(round(device.dewpoint   * 10))
        airflow_t10 = int(round(device.airflow    * 10))

        probe = self._plant_probe_entries(device)
        if probe is not None:
            return probe

        if device.vendor == Vendor.RARITAN:
            # Raritan PX2/DPX2 external sensor table (RARITAN-PX2-MIB)
            # T1H1: slot 1=temperature, slot 2=humidity
            # T3H1: slot 1=inlet temp, slot 2=mid-rack temp, slot 3=exhaust temp, slot 4=humidity
            b = _RARITAN_SENSOR
            if device.model_name == "Raritan DPX2-T3H1":
                mid_t10     = int(round(device.mid_temp    * 10))
                exhaust_t10 = int(round(device.outlet_temp * 10))
                entries += [
                    _oid_entry(f"{b}.2.1.1", "2", "1"),              # slot 1 index
                    _oid_entry(f"{b}.3.1.1", "2", "10"),             # type=temperature
                    _oid_entry(f"{b}.4.1.1", "2", str(inlet_t10)),   # inlet ×10 °C
                    _oid_entry(f"{b}.5.1.1", "2", "4"),              # state=normal
                    _oid_entry(f"{b}.2.1.2", "2", "2"),              # slot 2 index
                    _oid_entry(f"{b}.3.1.2", "2", "10"),             # type=temperature
                    _oid_entry(f"{b}.4.1.2", "2", str(mid_t10)),     # mid-rack ×10 °C
                    _oid_entry(f"{b}.5.1.2", "2", "4"),              # state=normal
                    _oid_entry(f"{b}.2.1.3", "2", "3"),              # slot 3 index
                    _oid_entry(f"{b}.3.1.3", "2", "10"),             # type=temperature
                    _oid_entry(f"{b}.4.1.3", "2", str(exhaust_t10)), # exhaust ×10 °C
                    _oid_entry(f"{b}.5.1.3", "2", "4"),              # state=normal
                    _oid_entry(f"{b}.2.1.4", "2", "4"),              # slot 4 index
                    _oid_entry(f"{b}.3.1.4", "2", "11"),             # type=humidity
                    _oid_entry(f"{b}.4.1.4", "2", str(humid_t10)),   # value ×10 %
                    _oid_entry(f"{b}.5.1.4", "2", "4"),              # state=normal
                ]
            elif device.model_name == "Raritan DPX2-CC2":
                # Slot 1: water rope sensor (sensorType=28 waterDetection), value 0=dry 1=wet
                # Slot 2: temperature probe (sensorType=10)
                # Deployed under raised floor / near CRAC units to detect flooding
                entries += [
                    _oid_entry(f"{b}.2.1.1", "2", "1"),
                    _oid_entry(f"{b}.3.1.1", "2", "28"),   # sensorType=waterDetection
                    _oid_entry(f"{b}.4.1.1", "2", "0"),    # 0=no water
                    _oid_entry(f"{b}.5.1.1", "2", "4"),    # state=normal
                    _oid_entry(f"{b}.2.1.2", "2", "2"),
                    _oid_entry(f"{b}.3.1.2", "2", "10"),   # sensorType=temperature
                    _oid_entry(f"{b}.4.1.2", "2", str(inlet_t10)),
                    _oid_entry(f"{b}.5.1.2", "2", "4"),
                ]
            else:
                # DPX2-T1H1: slot 1=temperature, slot 2=humidity
                entries += [
                    _oid_entry(f"{b}.2.1.1", "2",  "1"),
                    _oid_entry(f"{b}.3.1.1", "2",  "10"),
                    _oid_entry(f"{b}.4.1.1", "2",  str(inlet_t10)),
                    _oid_entry(f"{b}.5.1.1", "2",  "4"),
                    _oid_entry(f"{b}.2.1.2", "2",  "2"),
                    _oid_entry(f"{b}.3.1.2", "2",  "11"),
                    _oid_entry(f"{b}.4.1.2", "2",  str(humid_t10)),
                    _oid_entry(f"{b}.5.1.2", "2",  "4"),
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

        Power/cooling edges are skipped: they carry no iface (a cord plugs into an
        outlet, not an ifIndex), and every consumer here indexes device.interfaces.
        """
        result = []
        for neighbor in topology.get_neighbors(device.id):
            # graph[u][v] → {edge_key: {attr_dict}} for MultiGraph
            for _key, edge in topology.graph[device.id][neighbor.id].items():
                if edge.get("broken", False):
                    continue
                if edge.get("src_node") == device.id:
                    local_port  = edge.get("src_iface")
                    remote_port = edge.get("dst_iface")
                else:
                    local_port  = edge.get("dst_iface")
                    remote_port = edge.get("src_iface")
                if local_port is None:
                    continue
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
                    encoding="utf-8", newline="\n", delete=False) as f:
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
        with open(filepath, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(lines)
