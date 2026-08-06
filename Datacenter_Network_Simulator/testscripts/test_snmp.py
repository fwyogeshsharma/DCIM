"""
SNMP Device Tester
==================
Tests one or more simulated devices by issuing SNMPv2c GET and WALK requests
and printing a formatted results table.

The simulator listens on 0.0.0.0:161 (one port, all interfaces) and routes
each request to the correct device dataset via the SNMP community string,
which is set to the device IP address.  Use --agent to tell the tester where
snmpsim is actually running (default: 127.0.0.1).

Server IP semantics (two SNMP agents per server):
  - PRODUCTION IP → OS agent: ifTable, LLDP, CPU/mem/disk. Dead while the
    chassis is powered off (values read zero).
  - MGMT IP → BMC agent: power state, temps, fans, PSUs (--bmc section).
    Answers even while the chassis is Off — same IP Redfish uses.

Usage examples
--------------
  # Test a single device (community = IP by default, agent = 127.0.0.1)
  python test_snmp.py 10.50.0.4

  # Test multiple devices via the local simulator
  python test_snmp.py 10.50.0.1 10.50.0.2 10.50.0.3

  # Simulator on a remote host
  python test_snmp.py 10.50.0.4 --agent 192.168.1.50

  # Custom port / community / timeout
  python test_snmp.py 10.0.0.1 --port 1161 --community public --timeout 3

  # Also walk the interface table
  python test_snmp.py 10.0.0.1 --interfaces

  # Query all sensor readings (Raritan DPX2 / Vertiv Geist / APC NetBotz)
  python test_snmp.py 192.168.0.209 --sensor

  # Query all UPS data (battery, input, output, enterprise status)
  python test_snmp.py 10.50.0.20 --ups

  # Query all PDU data (power, status, environment)
  python test_snmp.py 10.50.0.30 --pdu

  # Query all switch data (MAC table, STP, CDP, Cisco CPU/memory)
  python test_snmp.py 10.50.0.10 --switch

  # Query all router data (BGP peers, CDP, Cisco CPU/memory)
  python test_snmp.py 10.50.0.5 --router

  # Full auto-detect: probe device type and fetch all relevant data
  python test_snmp.py 10.50.0.1 --full

  # Quiet — only print failures
  python test_snmp.py 10.0.0.1 10.0.0.2 --quiet
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import warnings

warnings.filterwarnings("ignore", category=RuntimeWarning, module="pysnmp")
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ── Colour helpers ────────────────────────────────────────────────────────────

_USE_COLOUR = sys.stdout.isatty()

def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text

def green(t):  return _c(t, "32")
def red(t):    return _c(t, "31")
def yellow(t): return _c(t, "33")
def cyan(t):   return _c(t, "36")
def bold(t):   return _c(t, "1")
def grey(t):   return _c(t, "90")


# ── OID catalogue ─────────────────────────────────────────────────────────────

SYSTEM_OIDS: List[Tuple[str, str]] = [
    ("1.3.6.1.2.1.1.1.0",  "sysDescr"),
    ("1.3.6.1.2.1.1.2.0",  "sysObjectID"),
    ("1.3.6.1.2.1.1.3.0",  "sysUpTime"),
    ("1.3.6.1.2.1.1.4.0",  "sysContact"),
    ("1.3.6.1.2.1.1.5.0",  "sysName"),
    ("1.3.6.1.2.1.1.6.0",  "sysLocation"),
    ("1.3.6.1.2.1.1.7.0",  "sysServices"),
]

IFACE_TABLE_OID   = "1.3.6.1.2.1.2.2"
LLDP_REM_OID      = "1.0.8802.1.1.2.1.4.1"

PERF_OIDS: List[Tuple[str, str]] = [
    ("1.3.6.1.4.1.2021.11.9.0",   "cpuUser"),    # ssCpuUser
    ("1.3.6.1.4.1.2021.11.10.0",  "cpuSystem"),  # ssCpuSystem
    ("1.3.6.1.4.1.2021.11.11.0",  "cpuIdle"),    # ssCpuIdle
    ("1.3.6.1.4.1.2021.4.5.0",    "memTotalKB"),
    ("1.3.6.1.4.1.2021.4.6.0",    "memAvailKB"),
    ("1.3.6.1.4.1.2021.4.14.0",   "memBufferKB"),
    ("1.3.6.1.4.1.2021.9.1.5.1",  "diskPercent"),
    ("1.3.6.1.4.1.2021.9.1.6.1",  "diskTotalKB"),
    ("1.3.6.1.4.1.2021.9.1.7.1",  "diskAvailKB"),
]

TEMP_SENSOR_OID       = "1.3.6.1.2.1.99.1.1.1.4"
CISCO_ENVMON_TEMP_OID = "1.3.6.1.4.1.9.9.13.1.3.1.3"

# Environmental sensor vendor OIDs
_RARITAN_SENSOR = "1.3.6.1.4.1.13742.6.5.5.3.1"
_GEIST_SENSOR   = "1.3.6.1.4.1.21239.5.1"
_APC_NETBOTZ    = "1.3.6.1.4.1.318.1.1.10.4.2.2.1"

# UPS OIDs
_UPS_MIB = "1.3.6.1.2.1.33.1"
_UPS_ENT = "1.3.6.1.4.1.99999.4"

UPS_STD_OIDS: List[Tuple[str, str]] = [
    (f"{_UPS_MIB}.2.1.0",     "batteryStatus"),
    (f"{_UPS_MIB}.2.4.0",     "runtimeMinutes"),
    (f"{_UPS_MIB}.2.5.0",     "chargePercent"),
    (f"{_UPS_MIB}.2.6.0",     "batteryVoltagex10"),
    (f"{_UPS_MIB}.2.8.0",     "batteryTempC"),
    (f"{_UPS_MIB}.3.3.1.2.1", "inputFreqx10"),
    (f"{_UPS_MIB}.3.3.1.3.1", "inputVoltageV"),
    (f"{_UPS_MIB}.3.3.1.4.1", "inputCurrentx10"),
    (f"{_UPS_MIB}.3.3.1.5.1", "inputPowerW"),
    (f"{_UPS_MIB}.4.1.0",     "outputSource"),
    (f"{_UPS_MIB}.4.2.0",     "outputFreqx10"),
    (f"{_UPS_MIB}.4.4.1.2.1", "outputVoltageV"),
    (f"{_UPS_MIB}.4.4.1.3.1", "outputCurrentx10"),
    (f"{_UPS_MIB}.4.4.1.4.1", "outputPowerW"),
    (f"{_UPS_MIB}.4.4.1.6.1", "outputLoadPercent"),
]

UPS_ENT_OIDS: List[Tuple[str, str]] = [
    (f"{_UPS_ENT}.1.0",   "fanStatus"),
    (f"{_UPS_ENT}.2.0",   "chargerStatus"),
    (f"{_UPS_ENT}.3.0",   "rectifierStatus"),
    (f"{_UPS_ENT}.4.0",   "phaseStatus"),
    (f"{_UPS_ENT}.5.0",   "batteryStatusEx"),
    (f"{_UPS_ENT}.6.0",   "operatingMode"),
    (f"{_UPS_ENT}.7.0",   "bypassStatus"),
    (f"{_UPS_ENT}.8.0",   "batteryHealthPct"),
    (f"{_UPS_ENT}.9.0",   "apparentPowerVA"),
    (f"{_UPS_ENT}.10.0",  "energyKWhx10"),
]

# PDU OIDs.
#
# APC and Raritan rack PDUs publish on their own MIBs (PowerNet rPDU2 /
# PDU2-MIB) — see core/vendor_oids.py — so the probe asks for the vendor OIDs
# as well as the placeholder tree and keeps whichever the agent answers. The
# vendor readings carry the vendor's own scaling, hence the distinct names.
_APC_RPDU2  = "1.3.6.1.4.1.318.1.1.26"
_RARITAN_M  = "1.3.6.1.4.1.13742.6.5"

PDU_VENDOR_OIDS: List[Tuple[str, str]] = [
    # APC PowerNet rPDU2
    (f"{_APC_RPDU2}.4.3.1.5.1",  "apcPowerHundredthsKW"),
    (f"{_APC_RPDU2}.4.3.1.4.1",  "apcLoadState"),
    (f"{_APC_RPDU2}.4.3.1.17.1", "apcPowerFactorx100"),
    (f"{_APC_RPDU2}.6.3.1.5.1",  "apcPhaseCurrentx10"),
    (f"{_APC_RPDU2}.6.3.1.6.1",  "apcPhaseVoltageV"),
    (f"{_APC_RPDU2}.8.3.1.4.1",  "apcBankState"),
    (f"{_APC_RPDU2}.9.2.3.1.5.1","apcOutletState"),
    (f"{_APC_RPDU2}.10.2.2.1.8.1",  "apcSensorTempx10C"),
    (f"{_APC_RPDU2}.10.2.2.1.10.1", "apcSensorHumidityPct"),
    # Raritan PDU2-MIB inlet measurements: [pdu][inlet][sensorType]
    (f"{_RARITAN_M}.2.3.1.4.1.1.1",  "raritanInletCurrentmA"),
    (f"{_RARITAN_M}.2.3.1.4.1.1.4",  "raritanInletVoltagemV"),
    (f"{_RARITAN_M}.2.3.1.4.1.1.5",  "raritanInletActivePowerW"),
    (f"{_RARITAN_M}.2.3.1.4.1.1.23", "raritanInletFrequencyx10"),
    (f"{_RARITAN_M}.2.3.1.3.1.1.1",  "raritanInletCurrentState"),
    (f"{_RARITAN_M}.3.3.1.3.1.1.15", "raritanBreakerState"),
    (f"{_RARITAN_M}.5.3.1.4.1.1",    "raritanProbeTempx10"),
    (f"{_RARITAN_M}.5.3.1.4.1.2",    "raritanProbeHumidityx10"),
]

_PDU_ENT = "1.3.6.1.4.1.99999.5"

PDU_OIDS: List[Tuple[str, str]] = [
    (f"{_PDU_ENT}.1.0",   "loadPercent"),
    (f"{_PDU_ENT}.2.0",   "voltageV"),
    (f"{_PDU_ENT}.3.0",   "powerFactorx100"),
    (f"{_PDU_ENT}.4.0",   "phaseImbalancePct"),
    (f"{_PDU_ENT}.5.0",   "outletStatus"),
    (f"{_PDU_ENT}.6.0",   "breakerStatus"),
    (f"{_PDU_ENT}.7.0",   "outletFailure"),
    (f"{_PDU_ENT}.8.0",   "smokeDetected"),
    (f"{_PDU_ENT}.9.0",   "outletCurrentx10"),
    (f"{_PDU_ENT}.10.0",  "groundFault"),
    (f"{_PDU_ENT}.11.0",  "realPowerW"),
    (f"{_PDU_ENT}.12.0",  "apparentPowerVA"),
    (f"{_PDU_ENT}.13.0",  "energyKWhx10"),
    (f"{_PDU_ENT}.14.0",  "frequencyx10"),
    (f"{_PDU_ENT}.15.0",  "temperaturex10"),
    (f"{_PDU_ENT}.16.0",  "humidityx10"),
    (f"{_PDU_ENT}.17.0",  "outletPowerW"),
]

# PDU per-outlet table (walk): .20.1.{col}.{idx}
#   col 1=index 2=name 3=status 4=currentX10 5=powerW
_PDU_OUTLET_TBL = "1.3.6.1.4.1.99999.5.20.1"

# Cisco network device OIDs
_CISCO_CPU_MIB = "1.3.6.1.4.1.9.9.109.1.1.1.1"   # CISCO-PROCESS-MIB cpmCPUTotalTable
_CISCO_MEM_MIB = "1.3.6.1.4.1.9.9.48.1.1.1"       # CISCO-MEMORY-POOL-MIB

CISCO_PERF_OIDS: List[Tuple[str, str]] = [
    (f"{_CISCO_CPU_MIB}.7.1",  "cpuTotal1min"),   # cpmCPUTotal1minRev %
    (f"{_CISCO_CPU_MIB}.8.1",  "cpuTotal5min"),   # cpmCPUTotal5minRev %
    (f"{_CISCO_MEM_MIB}.2.1",  "memPoolName"),    # ciscoMemoryPoolName
    (f"{_CISCO_MEM_MIB}.5.1",  "memPoolUsedMB"),  # ciscoMemoryPoolUsed (simulator: MB)
    (f"{_CISCO_MEM_MIB}.6.1",  "memPoolFreeMB"),  # ciscoMemoryPoolFree (simulator: MB)
]

# Switch OIDs (BRIDGE-MIB)
_DOT1D_TP_FDB  = "1.3.6.1.2.1.17.4.3.1"  # dot1dTpFdbTable
_DOT1D_STP     = "1.3.6.1.2.1.17.2"       # dot1dStp scalars
_CDP_BASE      = "1.3.6.1.4.1.9.9.23.1.2.1.1"  # cdpCacheTable

STP_OIDS: List[Tuple[str, str]] = [
    (f"{_DOT1D_STP}.1.0",  "protocol"),      # dot1dStpProtocolSpecification (3=ieee8021d)
    (f"{_DOT1D_STP}.2.0",  "priority"),      # dot1dStpPriority
    (f"{_DOT1D_STP}.3.0",  "topoChangeAge"), # dot1dStpTimeSinceTopologyChange (centiseconds)
    (f"{_DOT1D_STP}.4.0",  "topoChanges"),   # dot1dStpTopChanges
    (f"{_DOT1D_STP}.6.0",  "rootCost"),      # dot1dStpRootCost
    (f"{_DOT1D_STP}.7.0",  "rootPort"),      # dot1dStpRootPort
    (f"{_DOT1D_STP}.8.0",  "maxAge"),        # dot1dStpMaxAge (centiseconds)
    (f"{_DOT1D_STP}.9.0",  "helloTime"),     # dot1dStpHelloTime (centiseconds)
    (f"{_DOT1D_STP}.11.0", "forwardDelay"),  # dot1dStpForwardDelay (centiseconds)
]

# Router OIDs (BGP4-MIB)
_BGP4_PEER_TBL = "1.3.6.1.2.1.15.3.1"   # bgpPeerTable


# ── Result model ──────────────────────────────────────────────────────────────

@dataclass
class OIDResult:
    oid:   str
    name:  str
    value: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.value is not None

@dataclass
class DeviceResult:
    ip:               str
    community:        str
    port:             int
    elapsed_ms:       float           = 0.0
    system:           List[OIDResult] = field(default_factory=list)
    performance:      List[OIDResult] = field(default_factory=list)
    interfaces:       List[dict]      = field(default_factory=list)
    lldp_neighbours:  List[dict]      = field(default_factory=list)
    unreachable:      bool            = False
    error:            Optional[str]   = None
    temperatures:     List[dict]      = field(default_factory=list)
    sensor_readings:  List[dict]      = field(default_factory=dict)
    ups_data:         Dict[str, str]  = field(default_factory=dict)
    pdu_data:         Dict[str, str]  = field(default_factory=dict)
    pdu_outlets:      List[dict]      = field(default_factory=list)
    cisco_perf:       Dict[str, str]  = field(default_factory=dict)
    switch_data:      Dict[str, Any]  = field(default_factory=dict)
    router_data:      Dict[str, Any]  = field(default_factory=dict)
    bmc_data:         Dict[str, str]  = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.system if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.system if not r.ok)


# ── pysnmp async helpers ──────────────────────────────────────────────────────

async def _snmp_get(ip: str, community: str, port: int,
                    oids: List[str], timeout: int) -> Dict[str, str]:
    from pysnmp.entity.engine import SnmpEngine
    from pysnmp.entity import config as snmp_config
    from pysnmp.entity.rfc3413 import cmdgen
    from pysnmp.carrier.asyncio.dispatch import AsyncioDispatcher
    from pysnmp.carrier.asyncio.dgram import udp as udp_mod
    from pyasn1.type import univ

    loop = asyncio.get_running_loop()
    results: Dict[str, str] = {}

    snmp_engine = SnmpEngine()
    dispatcher = AsyncioDispatcher(loop=loop)
    snmp_engine.register_transport_dispatcher(dispatcher)
    snmp_config.add_transport(snmp_engine, udp_mod.DOMAIN_NAME,
                              udp_mod.UdpAsyncioTransport().open_client_mode())
    snmp_config.add_v1_system(snmp_engine, 'comm-area', community)
    snmp_config.add_target_parameters(snmp_engine, 'my-params', 'comm-area', 'noAuthNoPriv', 1)
    snmp_config.add_target_address(snmp_engine, 'my-target', udp_mod.DOMAIN_NAME,
                                   (ip, port), 'my-params',
                                   timeout=float(timeout), retryCount=1)

    pending_oids = list(oids)
    done = loop.create_future()
    oid_iter = iter(pending_oids)
    current_oid = [None]

    def _cb(snmpEngine, handle, errorInd, errorStat, errorIdx, varBinds, cbCtx):
        oid = current_oid[0]
        if errorInd:
            results[oid] = f"ERROR: {errorInd}"
        elif errorStat:
            results[oid] = f"ERROR: {errorStat.prettyPrint()}"
        elif varBinds:
            for name, val in varBinds:
                results[oid] = val.prettyPrint()
        try:
            current_oid[0] = next(oid_iter)
            oid_tuple = tuple(int(x) for x in current_oid[0].split('.'))
            cmdgen.GetCommandGenerator().send_varbinds(
                snmpEngine, 'my-target', None, b'',
                [(univ.ObjectIdentifier(oid_tuple), univ.Null())], _cb)
        except StopIteration:
            if not done.done():
                done.set_result(None)

    try:
        current_oid[0] = next(iter(pending_oids))
        oid_tuple = tuple(int(x) for x in current_oid[0].split('.'))
        cmdgen.GetCommandGenerator().send_varbinds(
            snmp_engine, 'my-target', None, b'',
            [(univ.ObjectIdentifier(oid_tuple), univ.Null())], _cb)
        await asyncio.wait_for(done, timeout=timeout * len(oids) + 2)
    except (asyncio.TimeoutError, StopIteration):
        pass
    finally:
        try:
            dispatcher.close_dispatcher()
            snmp_engine.unregister_transport_dispatcher()
        except Exception:
            pass

    return results


async def _snmp_walk(ip: str, community: str, port: int,
                     base_oid: str, timeout: int,
                     max_rows: int = 200) -> List[Tuple[str, str]]:
    from pysnmp.entity.engine import SnmpEngine
    from pysnmp.entity import config as snmp_config
    from pysnmp.entity.rfc3413 import cmdgen
    from pysnmp.carrier.asyncio.dispatch import AsyncioDispatcher
    from pysnmp.carrier.asyncio.dgram import udp as udp_mod
    from pyasn1.type import univ

    loop = asyncio.get_running_loop()
    done = loop.create_future()
    rows: List[Tuple[str, str]] = []

    snmp_engine = SnmpEngine()
    dispatcher = AsyncioDispatcher(loop=loop)
    snmp_engine.register_transport_dispatcher(dispatcher)
    snmp_config.add_transport(snmp_engine, udp_mod.DOMAIN_NAME,
                              udp_mod.UdpAsyncioTransport().open_client_mode())
    snmp_config.add_v1_system(snmp_engine, 'comm-area', community)
    snmp_config.add_target_parameters(snmp_engine, 'my-params', 'comm-area', 'noAuthNoPriv', 1)
    snmp_config.add_target_address(snmp_engine, 'my-target', udp_mod.DOMAIN_NAME,
                                   (ip, port), 'my-params',
                                   timeout=float(timeout), retryCount=1)

    oid_prefix = base_oid + '.'
    gen = cmdgen.NextCommandGenerator()

    def _cb(snmpEngine, handle, errorInd, errorStat, errorIdx, varBinds, cbCtx):
        if done.done():
            return False
        if errorInd or errorStat or not varBinds:
            done.set_result(None)
            return False
        next_oids = []
        for name, val in varBinds:
            name_str = str(name)
            val_str  = val.prettyPrint()
            if "No more variables" in val_str or val_str == "endOfMibView":
                done.set_result(None)
                return False
            if not name_str.startswith(oid_prefix):
                done.set_result(None)
                return False
            rows.append((name_str, val_str))
            next_oids.append((name, univ.Null()))
            if len(rows) >= max_rows:
                done.set_result(None)
                return False
        gen.send_varbinds(snmp_engine, 'my-target', None, b'', next_oids, _cb)
        return False

    oid_tuple = tuple(int(x) for x in base_oid.split('.'))
    gen.send_varbinds(
        snmp_engine, 'my-target', None, b'',
        [(univ.ObjectIdentifier(oid_tuple), univ.Null())], _cb)

    try:
        await asyncio.wait_for(done, timeout=timeout + 2)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            dispatcher.close_dispatcher()
            snmp_engine.unregister_transport_dispatcher()
        except Exception:
            pass

    return rows


# ── Device-type data queries ───────────────────────────────────────────────────

def _is_valid(val: str) -> bool:
    return bool(val) and not val.startswith("ERROR:") and val not in (
        "noSuchInstance", "noSuchObject", "endOfMibView") and not val.lower().startswith("no such")


async def _query_sensor_data(agent_ip: str, community: str, port: int,
                              timeout: int) -> List[dict]:
    """Walk vendor sensor OID trees. Tries Raritan → Vertiv Geist → APC NetBotz."""
    readings: List[dict] = []

    rows = await _snmp_walk(agent_ip, community, port, _RARITAN_SENSOR, timeout, 60)
    if rows:
        pfx = _RARITAN_SENSOR + "."
        slot_types:  Dict[int, str] = {}
        slot_values: Dict[int, str] = {}
        for oid_str, val in rows:
            tail = oid_str[len(pfx):].split(".")
            if len(tail) < 3:
                continue
            sub, slot_s = tail[0], tail[2]
            try:
                slot = int(slot_s)
                if sub == "3":
                    slot_types[slot] = val
                elif sub == "4":
                    slot_values[slot] = val
            except (ValueError, IndexError):
                pass
        if slot_types:
            _TEMP_LABELS = ["Inlet Temp", "Mid-Rack Temp", "Exhaust Temp"]
            temp_count = 0
            for slot in sorted(slot_types):
                stype = slot_types[slot]
                try:
                    v = int(slot_values.get(slot, "0"))
                except ValueError:
                    continue
                if stype == "10":
                    label = _TEMP_LABELS[temp_count] if temp_count < 3 else f"Temp {temp_count+1}"
                    readings.append({"label": label, "value": v / 10.0, "unit": "°C", "alarm": False})
                    temp_count += 1
                elif stype == "11":
                    readings.append({"label": "Humidity", "value": v / 10.0, "unit": "%RH", "alarm": False})
                elif stype == "28":
                    readings.append({"label": "Leak Sensor", "value": "WET" if v else "dry",
                                     "unit": "", "alarm": bool(v)})
    if readings:
        return readings

    rows = await _snmp_walk(agent_ip, community, port, _GEIST_SENSOR, timeout, 60)
    if rows:
        row_map = {oid: val for oid, val in rows}
        def _g(sub: str, col: str, idx: int) -> Optional[str]:
            return row_map.get(f"{_GEIST_SENSOR}.{sub}.1.{col}.{idx}")
        for probe_idx in range(1, 5):
            for sub, col_v, col_l, lbl, unit in [("4","4","2","Temp","°C"),
                                                   ("5","4","2","Humidity","%RH"),
                                                   ("6","4","2","Dewpoint","°C")]:
                raw = _g(sub, col_v, probe_idx)
                if raw:
                    try:
                        loc = _g(sub, col_l, probe_idx) or "Inlet"
                        readings.append({"label": f"{loc} {lbl}", "value": round(int(raw)/10.0,1),
                                         "unit": unit, "alarm": False})
                    except ValueError:
                        pass
    if readings:
        return readings

    rows = await _snmp_walk(agent_ip, community, port, _APC_NETBOTZ, timeout, 30)
    if rows:
        row_map = {oid: val for oid, val in rows}
        for val_oid, lbl_oid, unit, scale in [
            (f"{_APC_NETBOTZ}.10.1", f"{_APC_NETBOTZ}.2.1", "°C",  0.1),
            (f"{_APC_NETBOTZ}.10.2", f"{_APC_NETBOTZ}.2.2", "%RH", 1.0),
            (f"{_APC_NETBOTZ}.10.3", f"{_APC_NETBOTZ}.2.3", "m/s", 0.1),
        ]:
            raw = row_map.get(val_oid)
            label = row_map.get(lbl_oid) or val_oid.rsplit(".", 1)[-1]
            if raw:
                try:
                    readings.append({"label": label, "value": round(int(raw) * scale, 1),
                                     "unit": unit, "alarm": False})
                except (ValueError, TypeError):
                    pass
    return readings


# ── Server BMC agent (mgmt IP) — hardware health, enterprise .26 subtree ─────
# The BMC SNMP agent answers on the server's MGMT IP (community = mgmt IP) and
# stays up while the chassis is powered off. The OS agent answers on the
# production IP. See core/snmprec_generator.py (_bmc_entries).
_BMC_ENT = "1.3.6.1.4.1.99999.26"
BMC_OIDS = [
    (f"{_BMC_ENT}.1.1.0", "powerState"),       # 1=On 2=Off
    (f"{_BMC_ENT}.2.1.0", "inletTempx10"),
    (f"{_BMC_ENT}.2.2.0", "cpuTempx10"),
    (f"{_BMC_ENT}.3.1.1", "fan1Rpm"),
    (f"{_BMC_ENT}.3.1.2", "fan2Rpm"),
    (f"{_BMC_ENT}.3.1.3", "fan3Rpm"),
    (f"{_BMC_ENT}.3.1.4", "fan4Rpm"),
    (f"{_BMC_ENT}.4.1.1", "psu1Status"),
    (f"{_BMC_ENT}.4.1.2", "psu2Status"),
    (f"{_BMC_ENT}.4.2.1", "psu1OutputW"),
    (f"{_BMC_ENT}.4.2.2", "psu2OutputW"),
    (f"{_BMC_ENT}.5.1.0", "totalPowerW"),
    (f"{_BMC_ENT}.6.1.0", "model"),
    (f"{_BMC_ENT}.6.2.0", "vendor"),
]


async def _query_bmc_data(agent_ip: str, community: str, port: int,
                           timeout: int) -> Dict[str, str]:
    oid_list = [oid for oid, _ in BMC_OIDS]
    try:
        raw = await _snmp_get(agent_ip, community, port, oid_list, timeout)
    except Exception:
        return {}
    return {name: raw[oid] for oid, name in BMC_OIDS if _is_valid(raw.get(oid, ""))}


async def _query_ups_data(agent_ip: str, community: str, port: int,
                           timeout: int) -> Dict[str, str]:
    all_oids = UPS_STD_OIDS + UPS_ENT_OIDS
    oid_list = [oid for oid, _ in all_oids]
    try:
        raw = await _snmp_get(agent_ip, community, port, oid_list, timeout)
    except Exception:
        return {}
    return {name: raw[oid] for oid, name in all_oids if _is_valid(raw.get(oid, ""))}


async def _query_pdu_data(agent_ip: str, community: str, port: int,
                           timeout: int) -> Dict[str, str]:
    all_pdu = PDU_OIDS + PDU_VENDOR_OIDS
    oid_list = [oid for oid, _ in all_pdu]
    try:
        raw = await _snmp_get(agent_ip, community, port, oid_list, timeout)
    except Exception:
        return {}
    return {name: raw[oid] for oid, name in all_pdu if _is_valid(raw.get(oid, ""))}


async def _query_pdu_outlets(agent_ip: str, community: str, port: int,
                              timeout: int) -> List[dict]:
    """Walk PDU per-outlet table — which device is plugged into each outlet."""
    rows = await _snmp_walk(agent_ip, community, port, _PDU_OUTLET_TBL, timeout, 500)
    pfx = _PDU_OUTLET_TBL + "."
    outlets: Dict[int, dict] = {}
    for oid_str, val in rows:
        if not oid_str.startswith(pfx):
            continue
        tail = oid_str[len(pfx):].split(".")
        if len(tail) < 2:
            continue
        try:
            col = int(tail[0])
            idx = int(tail[1])
        except ValueError:
            continue
        entry = outlets.setdefault(idx, {"outlet": idx})
        if col == 2:   entry["device"]  = val
        elif col == 3: entry["status"]  = "on" if val == "1" else "off"
        elif col == 4:
            try:    entry["current"] = int(val) / 10.0
            except ValueError: entry["current"] = val
        elif col == 5: entry["power"] = val
    return [outlets[k] for k in sorted(outlets)]


async def _query_cisco_perf(agent_ip: str, community: str, port: int,
                              timeout: int) -> Dict[str, str]:
    """GET Cisco PROCESS-MIB (CPU%) + MEMORY-POOL-MIB."""
    oid_list = [oid for oid, _ in CISCO_PERF_OIDS]
    try:
        raw = await _snmp_get(agent_ip, community, port, oid_list, timeout)
    except Exception:
        return {}
    return {name: raw[oid] for oid, name in CISCO_PERF_OIDS if _is_valid(raw.get(oid, ""))}


async def _query_switch_data(agent_ip: str, community: str, port: int,
                               timeout: int) -> Dict[str, Any]:
    """Walk BRIDGE-MIB MAC table, STP scalars, CDP neighbours."""
    result: Dict[str, Any] = {"mac_table": [], "stp": {}, "cdp": []}

    # MAC table (dot1dTpFdbTable)
    rows = await _snmp_walk(agent_ip, community, port, _DOT1D_TP_FDB, timeout, 2000)
    mac_map: Dict[str, dict] = {}
    pfx = _DOT1D_TP_FDB + "."
    for oid_str, val in rows:
        if not oid_str.startswith(pfx):
            continue
        parts = oid_str[len(pfx):].split(".")
        if len(parts) < 7:
            continue
        col     = parts[0]
        mac_key = ".".join(parts[1:7])
        entry   = mac_map.setdefault(mac_key, {})
        if col == "1":    # dot1dTpFdbAddress — reconstruct MAC from OID octets
            entry["mac"] = ":".join(f"{int(o):02x}" for o in parts[1:7])
        elif col == "2":  # dot1dTpFdbPort
            entry["port"] = val
        elif col == "3":  # dot1dTpFdbStatus (3=learned, 4=self, 5=mgmt)
            entry["status"] = {"3": "learned", "4": "self", "5": "mgmt"}.get(val, val)
    result["mac_table"] = [e for e in mac_map.values() if "mac" in e]

    # STP scalars
    stp_oid_list = [oid for oid, _ in STP_OIDS]
    try:
        stp_raw = await _snmp_get(agent_ip, community, port, stp_oid_list, timeout)
        result["stp"] = {name: stp_raw[oid] for oid, name in STP_OIDS if _is_valid(stp_raw.get(oid, ""))}
    except Exception:
        pass

    # CDP neighbours (Cisco Discover Protocol)
    rows = await _snmp_walk(agent_ip, community, port, _CDP_BASE, timeout, 500)
    cdp_map: Dict[str, dict] = {}
    pfx = _CDP_BASE + "."
    for oid_str, val in rows:
        if not oid_str.startswith(pfx):
            continue
        parts = oid_str[len(pfx):].split(".")
        if len(parts) < 3:
            continue
        col, lport, idx = parts[0], parts[1], parts[2]
        key = f"{lport}.{idx}"
        entry = cdp_map.setdefault(key, {"local_port": lport})
        if col == "6":    entry["device_id"]   = val
        elif col == "4":  entry["address"]     = val
        elif col == "5":  entry["platform"]    = val[:40]
        elif col == "7":  entry["remote_port"] = val
        elif col == "8":  entry["vendor"]      = val
    result["cdp"] = list(cdp_map.values())

    return result


async def _query_router_data(agent_ip: str, community: str, port: int,
                               timeout: int) -> Dict[str, Any]:
    """Walk BGP4-MIB peer table and CDP neighbours."""
    result: Dict[str, Any] = {"bgp_peers": [], "cdp": []}

    # BGP peers
    _BGP_STATE = {"1": "Idle", "2": "Connect", "3": "Active",
                  "4": "OpenSent", "5": "OpenConfirm", "6": "Established"}
    rows = await _snmp_walk(agent_ip, community, port, _BGP4_PEER_TBL, timeout, 200)
    bgp_map: Dict[str, dict] = {}
    pfx = _BGP4_PEER_TBL + "."
    for oid_str, val in rows:
        if not oid_str.startswith(pfx):
            continue
        parts = oid_str[len(pfx):].split(".")
        if len(parts) < 5:
            continue
        col     = parts[0]
        peer_ip = ".".join(parts[1:5])
        entry   = bgp_map.setdefault(peer_ip, {"peer": peer_ip})
        if col == "2":   entry["state"] = _BGP_STATE.get(val, val)
        elif col == "3": entry["admin"] = "up" if val == "2" else "down"
        elif col == "7": entry["remote_addr"] = val
    result["bgp_peers"] = list(bgp_map.values())

    # CDP neighbours
    rows = await _snmp_walk(agent_ip, community, port, _CDP_BASE, timeout, 200)
    cdp_map: Dict[str, dict] = {}
    pfx = _CDP_BASE + "."
    for oid_str, val in rows:
        if not oid_str.startswith(pfx):
            continue
        parts = oid_str[len(pfx):].split(".")
        if len(parts) < 3:
            continue
        col, lport, idx = parts[0], parts[1], parts[2]
        entry = cdp_map.setdefault(f"{lport}.{idx}", {"local_port": lport})
        if col == "6":    entry["device_id"]   = val
        elif col == "4":  entry["address"]     = val
        elif col == "7":  entry["remote_port"] = val
    result["cdp"] = list(cdp_map.values())

    return result


# ── Per-device test ───────────────────────────────────────────────────────────

async def _test_device(ip: str, agent_ip: str, community: str, port: int,
                       timeout: int,
                       do_interfaces: bool,
                       do_lldp: bool,
                       do_metrics: bool,
                       do_sensor: bool = False,
                       do_ups: bool = False,
                       do_pdu: bool = False,
                       do_switch: bool = False,
                       do_router: bool = False,
                       do_bmc: bool = False,
                       do_full: bool = False) -> DeviceResult:
    result = DeviceResult(ip=ip, community=community, port=port)
    t0 = time.perf_counter()

    # ── System OIDs ───────────────────────────────────────────────────────────
    oid_list = [oid for oid, _ in SYSTEM_OIDS]
    try:
        raw = await _snmp_get(agent_ip, community, port, oid_list, timeout)
    except Exception as exc:
        result.unreachable = True
        result.error = str(exc)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        return result

    for oid, name in SYSTEM_OIDS:
        val = raw.get(oid, "")
        if val.startswith("ERROR:"):
            result.system.append(OIDResult(oid=oid, name=name, error=val[7:].strip()))
        elif not _is_valid(val):
            result.system.append(OIDResult(oid=oid, name=name, error=val or "no value"))
        else:
            result.system.append(OIDResult(oid=oid, name=name, value=val))

    if all(not r.ok for r in result.system):
        result.unreachable = True
        errors = {r.error for r in result.system}
        result.error = next(iter(errors), "no response")
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        return result

    # ── Auto-detect device type for --full ────────────────────────────────────
    if do_full:
        # Probe signature OIDs in parallel
        probe_ups    = f"{_UPS_MIB}.2.1.0"
        probe_pdu    = f"{_PDU_ENT}.1.0"
        probe_stp    = f"{_DOT1D_STP}.1.0"   # switch-only (STP bridge MIB)
        probe_cisco  = f"{_CISCO_CPU_MIB}.7.1"
        probe_bmc    = f"{_BMC_ENT}.1.1.0"   # server BMC (mgmt IP only)

        probes = await _snmp_get(agent_ip, community, port,
                                 [probe_ups, probe_pdu, probe_stp, probe_cisco,
                                  probe_bmc], timeout)

        if _is_valid(probes.get(probe_ups, "")):
            do_ups = True
        if _is_valid(probes.get(probe_pdu, "")):
            do_pdu = True
        if _is_valid(probes.get(probe_stp, "")):
            do_switch = True
        if _is_valid(probes.get(probe_bmc, "")):
            do_bmc = True
        if _is_valid(probes.get(probe_cisco, "")):
            # Cisco network device — check router by trying BGP walk
            bgp_probe = await _snmp_walk(agent_ip, community, port, _BGP4_PEER_TBL, timeout, 5)
            if bgp_probe:
                do_router = True
            else:
                # Cisco but no BGP — still a switch, already set above
                pass

        do_sensor     = True
        do_metrics    = True
        do_interfaces = True
        do_lldp       = True

    # ── Server performance metrics (UCD-SNMP-MIB) ─────────────────────────────
    if do_metrics:
        perf_oids = [oid for oid, _ in PERF_OIDS]
        try:
            perf_raw = await _snmp_get(agent_ip, community, port, perf_oids, timeout)
            for oid, name in PERF_OIDS:
                val = perf_raw.get(oid, "")
                if val.startswith("ERROR:"):
                    result.performance.append(OIDResult(oid=oid, name=name, error=val[7:].strip()))
                elif not _is_valid(val):
                    result.performance.append(OIDResult(oid=oid, name=name, error=val or "no value"))
                else:
                    result.performance.append(OIDResult(oid=oid, name=name, value=val))
        except Exception as exc:
            result.performance.append(OIDResult(oid="metrics", name="metrics", error=str(exc)))

    # ── Temperature sensors ────────────────────────────────────────────────────
    if do_metrics:
        try:
            rows = await _snmp_walk(agent_ip, community, port, TEMP_SENSOR_OID, timeout, 200)
            for oid_str, val in rows:
                try:
                    temp_c = int(val) / 10
                except (ValueError, TypeError):
                    temp_c = val
                result.temperatures.append({"oid": oid_str, "value": temp_c})
        except (ValueError, TypeError):
            pass

        sys_oid = next((r.value for r in result.system if r.name == "sysObjectID" and r.ok), "")
        if sys_oid.startswith("1.3.6.1.4.1.9."):
            try:
                cisco_rows = await _snmp_walk(agent_ip, community, port,
                                              CISCO_ENVMON_TEMP_OID, timeout, 20)
                for oid_str, val in cisco_rows:
                    try:
                        temp_c = float(int(val))
                    except (ValueError, TypeError):
                        temp_c = val
                    result.temperatures.append({"oid": oid_str, "value": temp_c})
            except (ValueError, TypeError):
                pass

    # ── Interface table ────────────────────────────────────────────────────────
    if do_interfaces:
        rows = await _snmp_walk(agent_ip, community, port, IFACE_TABLE_OID, timeout, 2000)
        iface_map: Dict[int, dict] = {}
        for oid_str, val in rows:
            parts = oid_str.split(".")
            if len(parts) < 2:
                continue
            try:
                col = int(parts[-2])
                idx = int(parts[-1])
            except ValueError:
                continue
            iface_map.setdefault(idx, {"index": idx})
            if col == 2:    iface_map[idx]["descr"]      = val
            elif col == 5:  iface_map[idx]["speed"]      = val
            elif col == 7:  iface_map[idx]["admin"]      = "up" if val == "1" else "down"
            elif col == 8:  iface_map[idx]["oper"]       = "up" if val == "1" else "down"
            elif col == 10: iface_map[idx]["in_octets"]  = val
            elif col == 16: iface_map[idx]["out_octets"] = val
        result.interfaces = sorted(iface_map.values(), key=lambda x: x["index"])

    # ── LLDP ──────────────────────────────────────────────────────────────────
    if do_lldp:
        rows = await _snmp_walk(agent_ip, community, port, LLDP_REM_OID, timeout, 2000)
        nbr_map: Dict[str, dict] = {}
        for oid_str, val in rows:
            parts = oid_str.split(".")
            if len(parts) < 4:
                continue
            try:
                col      = int(parts[-4])
                port_num = int(parts[-2])
                idx      = int(parts[-1])
            except ValueError:
                continue
            key = f"{port_num}.{idx}"
            nbr_map.setdefault(key, {"port": port_num, "idx": idx})
            if col == 5:   nbr_map[key]["chassis_id"] = val
            elif col == 7: nbr_map[key]["port_id"]    = val
            elif col == 9: nbr_map[key]["sys_name"]   = val
        result.lldp_neighbours = list(nbr_map.values())

    # ── Vendor sensor readings ─────────────────────────────────────────────────
    if do_sensor:
        try:
            result.sensor_readings = await _query_sensor_data(agent_ip, community, port, timeout)
        except Exception:
            pass

    # ── Server BMC hardware health (mgmt IP) ──────────────────────────────────
    if do_bmc:
        try:
            result.bmc_data = await _query_bmc_data(agent_ip, community, port, timeout)
        except Exception:
            pass

    # ── UPS data ──────────────────────────────────────────────────────────────
    if do_ups:
        try:
            result.ups_data = await _query_ups_data(agent_ip, community, port, timeout)
        except Exception:
            pass

    # ── PDU data ──────────────────────────────────────────────────────────────
    if do_pdu:
        try:
            result.pdu_data = await _query_pdu_data(agent_ip, community, port, timeout)
        except Exception:
            pass
        try:
            result.pdu_outlets = await _query_pdu_outlets(agent_ip, community, port, timeout)
        except Exception:
            pass

    # ── Cisco network device performance ──────────────────────────────────────
    if do_switch or do_router:
        try:
            result.cisco_perf = await _query_cisco_perf(agent_ip, community, port, timeout)
        except Exception:
            pass

    # ── Switch-specific data ───────────────────────────────────────────────────
    if do_switch:
        try:
            result.switch_data = await _query_switch_data(agent_ip, community, port, timeout)
        except Exception:
            pass

    # ── Router-specific data ──────────────────────────────────────────────────
    if do_router:
        try:
            result.router_data = await _query_router_data(agent_ip, community, port, timeout)
        except Exception:
            pass

    result.elapsed_ms = (time.perf_counter() - t0) * 1000
    return result


# ── Rendering ─────────────────────────────────────────────────────────────────

def _ups_status_str(name: str, val: str) -> str:
    maps = {
        "batteryStatus":   {"1":"Unknown","2":"Normal","3":"Low","4":"Depleted"},
        "batteryStatusEx": {"1":"Depleted","2":"Normal","3":"Low"},
        "outputSource":    {"1":"Other","2":"None","3":"Normal","4":"Bypass","5":"Battery","6":"Booster","7":"Reducer"},
        "operatingMode":   {"1":"Online","2":"Battery","3":"Bypass","4":"Eco","5":"Standby"},
        "bypassStatus":    {"1":"Not Bypassed","2":"ON BYPASS"},
        "fanStatus":       {"1":"FAILURE","2":"OK"},
        "chargerStatus":   {"1":"FAILURE","2":"OK"},
        "rectifierStatus": {"1":"FAILURE","2":"OK"},
        "phaseStatus":     {"1":"FAILURE","2":"OK"},
    }
    m = maps.get(name)
    return m.get(val, val) if m else val

def _pdu_status_str(name: str, val: str) -> str:
    maps = {
        "outletStatus":  {"1":"ON","2":"OFF"},
        "breakerStatus": {"1":"OK","2":"TRIPPED"},
        "outletFailure": {"1":"OK","2":"FAILURE"},
        "smokeDetected": {"1":"No","2":"YES"},
        "groundFault":   {"1":"No","2":"YES"},
    }
    m = maps.get(name)
    return m.get(val, val) if m else val


def _print_bmc_section(bmc: Dict[str, str]) -> None:
    if not bmc:
        return
    on = bmc.get("powerState") == "1"
    print(f"\n  {bold('Server BMC')}:")
    state = "On" if on else "Off"
    print(f"    {'Chassis Power':<26} {green(state) if on else red(state)}")
    print(f"    {'Model':<26} {bmc.get('vendor', '—')} {bmc.get('model', '')}")
    for key, label in (("inletTempx10", "Inlet Temp"), ("cpuTempx10", "CPU Temp")):
        try:
            print(f"    {label:<26} {int(bmc[key]) / 10:.1f} °C")
        except (KeyError, ValueError):
            pass
    fans = [bmc.get(f"fan{i}Rpm") for i in range(1, 5)]
    if any(fans):
        print(f"    {'Fans (RPM)':<26} {' / '.join(f or '—' for f in fans)}")
    for i in (1, 2):
        st = bmc.get(f"psu{i}Status")
        w  = bmc.get(f"psu{i}OutputW", "—")
        if st is not None:
            ok = st == "1"
            print(f"    {f'PSU {i}':<26} {green('OK') if ok else red('FAIL')}  {w} W")
    if "totalPowerW" in bmc:
        print(f"    {'Total Power Draw':<26} {bmc['totalPowerW']} W")


def _print_ups_section(ups: Dict[str, str]) -> None:
    if not ups:
        return
    def _get(k: str, default: str = "—") -> str:
        return ups.get(k, default)

    print(f"\n  {bold('UPS Battery')}:")
    batt_s = _ups_status_str("batteryStatus", _get("batteryStatus"))
    print(f"    {'Battery Status':<26} {green(batt_s) if batt_s == 'Normal' else red(batt_s)}")
    print(f"    {'Runtime Remaining':<26} {_get('runtimeMinutes')} min")
    print(f"    {'Charge Remaining':<26} {_get('chargePercent')} %")
    try:
        print(f"    {'Battery Voltage':<26} {int(ups.get('batteryVoltagex10','0'))/10:.1f} V")
    except ValueError:
        pass
    print(f"    {'Battery Temp':<26} {_get('batteryTempC')} °C")

    print(f"\n  {bold('UPS Input')}:")
    try:
        print(f"    {'Input Freq':<26} {int(ups.get('inputFreqx10','0'))/10:.1f} Hz")
    except ValueError:
        pass
    print(f"    {'Input Voltage':<26} {_get('inputVoltageV')} V")
    try:
        print(f"    {'Current':<26} {int(ups.get('inputCurrentx10','0'))/10:.1f} A")
    except ValueError:
        pass
    print(f"    {'Power':<26} {_get('inputPowerW')} W")

    print(f"\n  {bold('UPS Output')}:")
    out_src = _ups_status_str("outputSource", _get("outputSource"))
    print(f"    {'Output Source':<26} {green(out_src) if out_src == 'Normal' else yellow(out_src)}")
    try:
        print(f"    {'Output Freq':<26} {int(ups.get('outputFreqx10','0'))/10:.1f} Hz")
    except ValueError:
        pass
    print(f"    {'Output Voltage':<26} {_get('outputVoltageV')} V")
    try:
        print(f"    {'Current':<26} {int(ups.get('outputCurrentx10','0'))/10:.1f} A")
    except ValueError:
        pass
    print(f"    {'Power':<26} {_get('outputPowerW')} W")
    print(f"    {'Output Load':<26} {_get('outputLoadPercent')} %")

    print(f"\n  {bold('UPS Status')}:")
    op_mode = _ups_status_str("operatingMode", _get("operatingMode"))
    print(f"    {'Operating Mode':<26} {green(op_mode) if op_mode == 'Online' else yellow(op_mode)}")
    bypass = _ups_status_str("bypassStatus", _get("bypassStatus"))
    print(f"    {'Bypass':<26} {green(bypass) if bypass == 'Not Bypassed' else red(bypass)}")
    for sn, label in [("fanStatus","Fan"),("chargerStatus","Charger"),
                      ("rectifierStatus","Rectifier"),("phaseStatus","Phase")]:
        v = _ups_status_str(sn, _get(sn))
        print(f"    {label+' Status':<26} {green(v) if v == 'OK' else red(v)}")
    print(f"    {'Battery Health':<26} {_get('batteryHealthPct')} %")
    print(f"    {'Apparent Power':<26} {_get('apparentPowerVA')} VA")
    try:
        print(f"    {'Energy (kWh)':<26} {int(ups.get('energyKWhx10','0'))/10:.1f} kWh")
    except ValueError:
        pass


def _print_pdu_section(pdu: Dict[str, str], outlets: Optional[List[dict]] = None) -> None:
    if not pdu:
        return
    def _get(k: str, default: str = "—") -> str:
        return pdu.get(k, default)

    print(f"\n  {bold('PDU Power')}:")
    print(f"    {'PDU Load':<26} {_get('loadPercent')} %")
    print(f"    {'Input Voltage':<26} {_get('voltageV')} V")
    try:
        print(f"    {'Power Factor':<26} {int(pdu.get('powerFactorx100','0'))/100:.2f}")
    except ValueError:
        pass
    print(f"    {'Real Power (W)':<26} {_get('realPowerW')} W")
    print(f"    {'Apparent (VA)':<26} {_get('apparentPowerVA')} VA")
    try:
        print(f"    {'Outlet Current':<26} {int(pdu.get('outletCurrentx10','0'))/10:.1f} A")
    except ValueError:
        pass
    print(f"    {'Outlet Power (W)':<26} {_get('outletPowerW')} W")
    try:
        print(f"    {'Energy (kWh)':<26} {int(pdu.get('energyKWhx10','0'))/10:.1f} kWh")
    except ValueError:
        pass
    try:
        print(f"    {'Input Freq':<26} {int(pdu.get('frequencyx10','0'))/10:.1f} Hz")
    except ValueError:
        pass

    print(f"\n  {bold('PDU Status')}:")
    for sn, label in [("outletStatus","Outlet Status"),("breakerStatus","Breaker Status"),
                      ("outletFailure","Outlet Failure"),("smokeDetected","Smoke Detection"),
                      ("groundFault","Ground Fault")]:
        v = _pdu_status_str(sn, _get(sn))
        col = green(v) if v in {"ON","OK","No"} else red(v)
        print(f"    {label:<26} {col}")
    print(f"    {'Phase Imbalance':<26} {_get('phaseImbalancePct')} %")

    if "temperaturex10" in pdu or "humidityx10" in pdu:
        print(f"\n  {bold('PDU Environment')}:")
        try:
            print(f"    {'Ambient Temp':<26} {int(pdu.get('temperaturex10','0'))/10:.1f} °C")
        except ValueError:
            pass
        try:
            print(f"    {'Ambient Humidity':<26} {int(pdu.get('humidityx10','0'))/10:.1f} %")
        except ValueError:
            pass

    if outlets:
        on  = sum(1 for o in outlets if o.get("status") == "on")
        print(f"\n  {bold('PDU Outlets')} ({len(outlets)} outlets, {on} on):")
        fmt = "    {outlet:>3}  {device:<28} {status:<5} {current:>8}  {power:>7}"
        print(grey(fmt.format(outlet="#", device="connected device", status="state",
                              current="current", power="power")))
        for o in outlets:
            cur = o.get("current", "—")
            cur_s = f"{cur:.1f} A" if isinstance(cur, (int, float)) else str(cur)
            status = o.get("status", "?")
            colour = green if status == "on" else grey
            print(colour(fmt.format(
                outlet  = o.get("outlet", "?"),
                device  = (o.get("device", "—") or "—")[:28],
                status  = status,
                current = cur_s,
                power   = f"{o.get('power','—')} W",
            )))


def _print_cisco_perf(perf: Dict[str, str]) -> None:
    if not perf:
        return
    print(f"\n  {bold('Cisco Performance')}:")
    cpu1  = perf.get("cpuTotal1min")
    cpu5  = perf.get("cpuTotal5min")
    if cpu1:
        try:
            v = int(cpu1)
            col = red if v >= 90 else (yellow if v >= 70 else green)
            print(f"    {'CPU (1-min avg)':<26} {col(str(v) + '%')}")
        except ValueError:
            print(f"    {'CPU (1-min avg)':<26} {cpu1} %")
    if cpu5:
        try:
            v = int(cpu5)
            col = red if v >= 90 else (yellow if v >= 70 else green)
            print(f"    {'CPU (5-min avg)':<26} {col(str(v) + '%')}")
        except ValueError:
            print(f"    {'CPU (5-min avg)':<26} {cpu5} %")
    used = perf.get("memPoolUsedMB")
    free = perf.get("memPoolFree" + "MB")
    name = perf.get("memPoolName", "Processor")
    if used and free:
        try:
            u, f = int(used), int(free)
            total = u + f
            pct   = int(u * 100 / total) if total else 0
            col   = red if pct >= 90 else (yellow if pct >= 70 else green)
            print(f"    {'Memory Pool':<26} {name}")
            print(f"    {'Memory Usage':<26} {col(str(pct) + '%')}")
            print(f"    {'Memory Used':<26} {u} MB")
            print(f"    {'Memory Free':<26} {f} MB")
            print(f"    {'Memory Total':<26} {total} MB")
        except ValueError:
            pass


def _print_switch_section(sw: Dict[str, Any]) -> None:
    if not sw:
        return

    stp = sw.get("stp", {})
    if stp:
        print(f"\n  {bold('STP (Spanning Tree)')}:")
        proto_map = {"1": "unknown", "2": "dec-lb100", "3": "ieee802.1d"}
        proto = proto_map.get(stp.get("protocol", ""), stp.get("protocol", "—"))
        print(f"    {'Protocol':<22} {proto}")
        print(f"    {'Priority':<22} {stp.get('priority', '—')}")
        try:
            cs = int(stp.get("topoChangeAge", "0"))
            print(f"    {'Last Topology Change':<22} {cs // 100} s ago")
        except ValueError:
            pass
        print(f"    {'Topology Changes':<22} {stp.get('topoChanges', '—')}")
        print(f"    {'Root Port':<22} {stp.get('rootPort', '—')}")
        try:
            print(f"    {'Max Age':<22} {int(stp.get('maxAge','0'))//100} s")
        except ValueError:
            pass
        try:
            print(f"    {'Hello Time':<22} {int(stp.get('helloTime','0'))//100} s")
        except ValueError:
            pass

    cdp = sw.get("cdp", [])
    if cdp:
        print(f"\n  {bold('CDP Neighbours')} ({len(cdp)} found):")
        fmt = "    port={port:<4} {device:<28} addr={addr:<18} port={rport}"
        print(grey(fmt.format(port="port", device="device-id", addr="address", rport="remote-port")))
        for n in cdp:
            print(cyan(fmt.format(
                port  = n.get("local_port", "?"),
                device= n.get("device_id",  "—")[:28],
                addr  = n.get("address",    "—"),
                rport = n.get("remote_port","—"),
            )))

    mac = sw.get("mac_table", [])
    if mac:
        learned  = [e for e in mac if e.get("status") == "learned"]
        self_mac = [e for e in mac if e.get("status") == "self"]
        print(f"\n  {bold('MAC Address Table')} ({len(mac)} total: "
              f"{len(learned)} learned, {len(self_mac)} self):")
        shown = learned[:20]
        fmt = "    {mac:<20} port={port:<6} {status}"
        print(grey(fmt.format(mac="MAC", port="port", status="status")))
        for e in shown:
            print(fmt.format(
                mac    = e.get("mac", "—"),
                port   = e.get("port", "?"),
                status = e.get("status", "—"),
            ))
        if len(learned) > 20:
            print(grey(f"    … {len(learned)-20} more learned entries"))


def _print_router_section(rt: Dict[str, Any]) -> None:
    if not rt:
        return

    bgp = rt.get("bgp_peers", [])
    if bgp:
        _BGP_STATE_COL = {"Established": green, "Idle": red, "Active": yellow,
                          "Connect": yellow, "OpenSent": yellow, "OpenConfirm": yellow}
        print(f"\n  {bold('BGP Peers')} ({len(bgp)} found):")
        fmt = "    {peer:<20} {state:<16} {admin}"
        print(grey(fmt.format(peer="peer-addr", state="state", admin="admin")))
        for p in bgp:
            state = p.get("state", "—")
            col   = _BGP_STATE_COL.get(state, grey)
            print(fmt.format(
                peer  = p.get("peer", "—"),
                state = col(state),
                admin = p.get("admin", "—"),
            ))
    elif rt.get("cdp") or rt:
        print(f"\n  {bold('BGP Peers')}: {grey('none found (sessions not yet initialised or not a BGP router)')}")

    cdp = rt.get("cdp", [])
    if cdp:
        print(f"\n  {bold('CDP Neighbours')} ({len(cdp)} found):")
        fmt = "    port={port:<4} {device:<28} addr={addr:<18} port={rport}"
        print(grey(fmt.format(port="port", device="device-id", addr="address", rport="remote-port")))
        for n in cdp:
            print(cyan(fmt.format(
                port  = n.get("local_port", "?"),
                device= n.get("device_id",  "—")[:28],
                addr  = n.get("address",    "—"),
                rport = n.get("remote_port","—"),
            )))


def _print_device(result: DeviceResult, quiet: bool) -> None:
    status = red("UNREACHABLE") if result.unreachable else (
        green("OK") if result.failed == 0 else yellow(f"{result.failed} FAILED")
    )
    print(bold(f"\n{'─'*64}"))
    print(bold(f"  {cyan(result.ip)}   community={result.community}   port={result.port}"
               f"   {status}   {grey(f'{result.elapsed_ms:.0f} ms')}"))
    print(bold(f"{'─'*64}"))

    if result.unreachable:
        print(f"  {red('✗')}  {result.error}")
        return

    col_w = max(len(r.name) for r in result.system) + 2
    for r in result.system:
        if not r.ok:
            if not quiet:
                print(f"  {red('✗')}  {r.name:<{col_w}} {red(r.error or 'no value')}")
        else:
            val = r.value
            if len(val) > 80:
                val = val[:77] + "…"
            if not quiet:
                print(f"  {green('✓')}  {r.name:<{col_w}} {val}")

    # Server performance (UCD-SNMP-MIB)
    if result.performance and any(r.ok for r in result.performance):
        print(f"\n  {bold('Performance Metrics')}:")
        perf_map = {r.name: r.value for r in result.performance if r.ok}
        try:
            idle_val = perf_map.get("cpuIdle")
            if idle_val:
                cpu_usage = 100 - float(idle_val)
                print(f"    {'CPU Usage':<22} {cpu_usage:.1f}%")
            else:
                print(f"    {'CPU Usage':<22} unavailable")
        except Exception:
            pass
        try:
            total_val = perf_map.get("memTotalKB")
            avail_val = perf_map.get("memAvailKB")
            if total_val and avail_val:
                total = int(total_val)
                avail = int(avail_val)
                if total > 0:
                    pct = ((total - avail) / total) * 100
                    print(f"    {'Memory Usage':<22} {pct:.1f}%")
                    print(f"    {'Memory Used':<22} {(total - avail) // 1024} MB")
                    print(f"    {'Memory Total':<22} {total // 1024} MB")
            else:
                print(f"    {'Memory Usage':<22} unavailable")
        except Exception:
            pass
        try:
            dpct = perf_map.get("diskPercent")
            dtot = perf_map.get("diskTotalKB")
            dav  = perf_map.get("diskAvailKB")
            if dpct:
                print(f"    {'Disk Usage':<22} {dpct}%")
                if dtot and dav:
                    print(f"    {'Disk Used':<22} {(int(dtot)-int(dav))//1024} MB")
                    print(f"    {'Disk Total':<22} {int(dtot)//1024} MB")
        except (ValueError, TypeError):
            pass
    elif result.performance:
        # All failed — still show section if do_metrics was requested but suppress in full mode
        # (non-server devices won't have these OIDs)
        pass

    # Temperature sensors
    if result.temperatures:
        print(f"\n  {bold('Temperature Sensors')}:")
        _TEMP_LABELS = {
            "1.3.6.1.2.1.99.1.1.1.4.1":     "Inlet Temperature",
            "1.3.6.1.2.1.99.1.1.1.4.2":     "CPU Temperature",
            "1.3.6.1.4.1.9.9.13.1.3.1.3.1": "Cisco Inlet Temp",
            "1.3.6.1.4.1.9.9.13.1.3.1.3.2": "Cisco CPU Temp",
        }
        for sensor in result.temperatures:
            label = _TEMP_LABELS.get(sensor['oid'], sensor['oid'])
            print(f"    {label:<26} {sensor['value']} °C")

    # Vendor sensor readings
    if result.sensor_readings:
        print(f"\n  {bold('Sensor Readings')}:")
        col_w = max(len(r["label"]) for r in result.sensor_readings) + 2
        for r in result.sensor_readings:
            val_str = f"{r['value']} {r['unit']}".strip()
            alarm   = r.get("alarm", False)
            line    = f"    {r['label']:<{col_w}} {val_str}"
            print(red(line) if alarm else line)

    # UPS
    _print_bmc_section(result.bmc_data)
    _print_ups_section(result.ups_data)

    # PDU
    _print_pdu_section(result.pdu_data, result.pdu_outlets)

    # Cisco performance (network devices)
    _print_cisco_perf(result.cisco_perf)

    # Switch data
    _print_switch_section(result.switch_data)

    # Router data
    _print_router_section(result.router_data)

    # Summary
    total = len(result.system)
    print(f"\n  {green(str(result.passed))}/{total} system OIDs OK", end="")
    if result.failed:
        print(f"  {red(str(result.failed))} failed", end="")
    print()

    # Interfaces
    if result.interfaces:
        print(f"\n  {bold('Interfaces')} ({len(result.interfaces)} found):")
        fmt = "    {idx:>4}  {descr:<28} {admin:>5}/{oper:<5}  {speed}"
        print(grey(fmt.format(idx="idx", descr="ifDescr", admin="admin", oper="oper", speed="speed")))
        for iface in result.interfaces:
            speed_raw = iface.get("speed", "")
            try:
                speed_bps = int(speed_raw)
                if speed_bps >= 1_000_000_000:
                    speed = f"{speed_bps // 1_000_000_000}G"
                elif speed_bps >= 1_000_000:
                    speed = f"{speed_bps // 1_000_000}M"
                else:
                    speed = speed_raw
            except ValueError:
                speed = speed_raw
            admin = iface.get("admin", "?")
            oper  = iface.get("oper",  "?")
            colour = green if oper == "up" else grey
            print(colour(fmt.format(
                idx   = iface["index"],
                descr = iface.get("descr", "—")[:28],
                admin = admin,
                oper  = oper,
                speed = speed,
            )))

    # LLDP
    if result.lldp_neighbours:
        print(f"\n  {bold('LLDP Neighbours')} ({len(result.lldp_neighbours)} found):")
        fmt2 = "    port={port:<4} {sys_name:<24} chassis={chassis_id:<18} port-id={port_id}"
        for nbr in result.lldp_neighbours:
            print(cyan(fmt2.format(
                port       = nbr.get("port", "?"),
                sys_name   = nbr.get("sys_name",   "—")[:24],
                chassis_id = nbr.get("chassis_id", "—"),
                port_id    = nbr.get("port_id",    "—"),
            )))


def _print_summary(results: List[DeviceResult]) -> None:
    print(bold(f"\n{'═'*64}"))
    print(bold("  SUMMARY"))
    print(bold(f"{'═'*64}"))
    total = len(results)
    ok   = sum(1 for r in results if not r.unreachable and r.failed == 0)
    warn = sum(1 for r in results if not r.unreachable and r.failed  > 0)
    fail = sum(1 for r in results if r.unreachable)
    fmt = "  {ip:<20} {status:<20} {elapsed}"
    print(grey(fmt.format(ip="IP", status="Status", elapsed="ms")))
    for r in results:
        if r.unreachable:
            status = red("UNREACHABLE")
        elif r.failed == 0:
            status = green("OK")
        else:
            status = yellow(f"{r.failed} OID(s) failed")
        print(fmt.format(ip=r.ip, status=status, elapsed=grey(f"{r.elapsed_ms:.0f}")))
    print()
    print(f"  Devices : {total}   "
          f"{green(str(ok))} OK   "
          f"{yellow(str(warn))} partial   "
          f"{red(str(fail))} unreachable")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="test_snmp.py",
        description="Test simulated SNMP devices by IP address.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[1] if "Usage" in __doc__ else "",
    )
    p.add_argument("ips", nargs="+", metavar="IP",
                   help="One or more device IP addresses to test")
    p.add_argument("--agent", "-a", default="127.0.0.1",
                   help="Host where snmpsim is listening (default: 127.0.0.1)")
    p.add_argument("--port",      "-p", type=int, default=161)
    p.add_argument("--community", "-c", default=None,
                   help="Community string (default: same as device IP)")
    p.add_argument("--timeout",   "-t", type=int, default=5)
    p.add_argument("--interfaces","-i", action="store_true",
                   help="Walk IF-MIB interface table")
    p.add_argument("--lldp",      "-l", action="store_true",
                   help="Walk LLDP-MIB remote neighbour table")
    p.add_argument("--metrics",   "-m", action="store_true",
                   help="Collect CPU/memory/disk metrics (servers, UCD-SNMP-MIB)")
    p.add_argument("--sensor",    "-s", action="store_true",
                   help="Query vendor sensor OIDs (Raritan/Vertiv/APC): temp, humidity, dewpoint, airflow, leak")
    p.add_argument("--ups",       "-u", action="store_true",
                   help="Query all UPS data: battery, input/output, enterprise status")
    p.add_argument("--pdu",             action="store_true",
                   help="Query all PDU data: power, status, environment")
    p.add_argument("--switch",          action="store_true",
                   help="Query switch data: MAC table (BRIDGE-MIB), STP, CDP, Cisco CPU/memory")
    p.add_argument("--bmc",             action="store_true",
                   help="query server BMC hardware health (poll the server's MGMT IP — answers even when powered off)")
    p.add_argument("--router",          action="store_true",
                   help="Query router data: BGP4-MIB peers, CDP, Cisco CPU/memory")
    p.add_argument("--full",      "-f", action="store_true",
                   help="Auto-detect device type (UPS/PDU/switch/router/sensor) and fetch all relevant data")
    p.add_argument("--quiet",     "-q", action="store_true",
                   help="Only print failures and summary")
    return p.parse_args()


async def _main(args: argparse.Namespace) -> int:
    do_ifaces  = args.interfaces or args.full
    do_lldp    = args.lldp or args.full
    do_metrics = args.metrics or args.full
    do_sensor  = args.sensor or args.full
    do_ups     = args.ups
    do_pdu     = args.pdu
    do_switch  = args.switch
    do_router  = args.router
    do_bmc     = args.bmc
    do_full    = args.full

    mode_parts = []
    if do_full:    mode_parts.append("full-auto")
    else:
        for flag, name in [(args.ups,"ups"),(args.pdu,"pdu"),(args.switch,"switch"),
                           (args.router,"router"),(args.bmc,"bmc"),(args.sensor,"sensor"),
                           (args.metrics,"metrics"),(args.interfaces,"interfaces"),
                           (args.lldp,"lldp")]:
            if flag: mode_parts.append(name)
    mode_str = " ".join(mode_parts) if mode_parts else "system-only"

    print(bold(f"\ndataCenter SNMP Tester  —  {len(args.ips)} device(s)"))
    print(grey(f"agent={args.agent}  port={args.port}  timeout={args.timeout}s  mode={mode_str}\n"))

    tasks = [
        _test_device(
            ip            = ip,
            agent_ip      = args.agent,
            community     = args.community if args.community else ip,
            port          = args.port,
            timeout       = args.timeout,
            do_interfaces = do_ifaces,
            do_lldp       = do_lldp,
            do_metrics    = do_metrics,
            do_sensor     = do_sensor,
            do_ups        = do_ups,
            do_pdu        = do_pdu,
            do_switch     = do_switch,
            do_router     = do_router,
            do_bmc        = do_bmc,
            do_full       = do_full,
        )
        for ip in args.ips
    ]
    results: List[DeviceResult] = list(await asyncio.gather(*tasks))

    for result in results:
        _print_device(result, quiet=args.quiet)

    _print_summary(results)

    return 1 if any(r.unreachable or r.failed > 0 for r in results) else 0


if __name__ == "__main__":
    args = _parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        rc = asyncio.run(_main(args))
    except KeyboardInterrupt:
        print("\nAborted.")
        rc = 130

    sys.exit(rc)
