"""
SNMP Device Tester
==================
Tests one or more simulated devices by issuing SNMPv2c GET and WALK requests
and printing a formatted results table.

The simulator listens on 0.0.0.0:161 (one port, all interfaces) and routes
each request to the correct device dataset via the SNMP community string,
which is set to the device IP address.  Use --agent to tell the tester where
snmpsim is actually running (default: 127.0.0.1).

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

  # Also walk LLDP neighbours
  python test_snmp.py 10.0.0.1 --lldp

  # Query all sensor readings (Raritan DPX2-T3H1: inlet/mid/exhaust + humidity)
  python test_snmp.py 192.168.0.209 --sensor

  # Full test (system + interfaces + LLDP + sensors)
  python test_snmp.py 10.0.0.1 --full

  # Quiet — only print failures
  python test_snmp.py 10.0.0.1 10.0.0.2 --quiet
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import warnings

# Silence the pysnmp-lextudio → pysnmp rename noise (emitted as RuntimeWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pysnmp")
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


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

IFACE_TABLE_OID   = "1.3.6.1.2.1.2.2"      # IF-MIB ifTable
LLDP_REM_OID      = "1.0.8802.1.1.2.1.4.1" # LLDP-MIB remote table


PERF_OIDS: List[Tuple[str, str]] = [
    # CPU
    ("1.3.6.1.4.1.2021.11.9.0",   "cpuIdle"),
    ("1.3.6.1.4.1.2021.11.10.0",  "cpuSystem"),
    ("1.3.6.1.4.1.2021.11.11.0",  "cpuUser"),

    # Memory
    ("1.3.6.1.4.1.2021.4.5.0",   "memTotalKB"),
    ("1.3.6.1.4.1.2021.4.6.0",   "memAvailKB"),
    ("1.3.6.1.4.1.2021.4.14.0",  "memBufferKB"),

    # Disk (UCD-SNMP-MIB dskTable index 1 = first mount)
    ("1.3.6.1.4.1.2021.9.1.5.1", "diskPercent"),
    ("1.3.6.1.4.1.2021.9.1.6.1", "diskTotalKB"),
    ("1.3.6.1.4.1.2021.9.1.7.1", "diskAvailKB"),
]

# ENTITY-SENSOR-MIB
# Temperature sensors often appear under:
# 1.3.6.1.2.1.99.1.1.1.4

TEMP_SENSOR_OID      = "1.3.6.1.2.1.99.1.1.1.4"
CISCO_ENVMON_TEMP_OID = "1.3.6.1.4.1.9.9.13.1.3.1.3"  # ciscoEnvMonTemperatureStatusValue

# Vendor sensor OIDs
_RARITAN_SENSOR = "1.3.6.1.4.1.13742.6.5.5.3.1"   # Raritan PX2/DPX2 external sensor table
_GEIST_SENSOR   = "1.3.6.1.4.1.21239.5.1"           # Vertiv Geist probe tables
_APC_NETBOTZ    = "1.3.6.1.4.1.318.1.1.10.4.2.2.1"  # APC NetBotz sensor value table

# Raritan sensorType codes
_RARITAN_TYPES = {"10": "temp", "11": "humidity", "28": "water"}


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
    # system:           List[OIDResult] = field(default_factory=list)
    system: List[OIDResult] = field(default_factory=list)
    performance: List[OIDResult] = field(default_factory=list)
    interfaces:       List[dict]      = field(default_factory=list)
    lldp_neighbours:  List[dict]      = field(default_factory=list)
    unreachable:      bool            = False
    error:            Optional[str]   = None
    temperatures: List[dict] = field(default_factory=list)
    sensor_readings: List[dict] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.system if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.system if not r.ok)


# ── pysnmp async helpers ──────────────────────────────────────────────────────

async def _snmp_get(ip: str, community: str, port: int,
                    oids: List[str], timeout: int) -> Dict[str, str]:
    """Return {oid_str: value_str} for a list of OIDs via SNMPv2c GET."""
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
    """Return [(oid_str, value_str)] for a subtree walk."""
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
            # endOfMibView — snmpsim repeats last OID with this value; stop.
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
        # NextCommandGenerator does NOT auto-continue — chain manually
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


# ── Sensor data query ────────────────────────────────────────────────────────

async def _query_sensor_data(agent_ip: str, community: str, port: int,
                              timeout: int) -> List[dict]:
    """Walk vendor OID trees and return labelled sensor readings.

    Tries Raritan → Vertiv Geist → APC NetBotz in order; returns on first hit.
    Each reading: {"label": str, "value": float|str, "unit": str, "alarm": bool}
    """
    readings: List[dict] = []

    # ── Raritan DPX2 ─────────────────────────────────────────────────────────
    rows = await _snmp_walk(agent_ip, community, port, _RARITAN_SENSOR, timeout, 60)
    if rows:
        pfx = _RARITAN_SENSOR + "."
        slot_types:  Dict[int, str] = {}
        slot_values: Dict[int, str] = {}
        for oid_str, val in rows:
            tail = oid_str[len(pfx):].split(".")  # e.g. ["3","1","1"]
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
                if stype == "10":   # temperature ×10 °C
                    label = _TEMP_LABELS[temp_count] if temp_count < 3 else f"Temp {temp_count+1}"
                    readings.append({"label": label, "value": v / 10.0, "unit": "°C", "alarm": False})
                    temp_count += 1
                elif stype == "11": # humidity ×10 %
                    readings.append({"label": "Humidity", "value": v / 10.0, "unit": "%RH", "alarm": False})
                elif stype == "28": # water detection 0=dry 1=wet
                    readings.append({"label": "Leak Sensor", "value": "WET" if v else "dry",
                                     "unit": "", "alarm": bool(v)})

    if readings:
        return readings

    # ── Vertiv Geist ─────────────────────────────────────────────────────────
    rows = await _snmp_walk(agent_ip, community, port, _GEIST_SENSOR, timeout, 60)
    if rows:
        row_map = {oid: val for oid, val in rows}

        def _g(sub: str, col: str, idx: int) -> Optional[str]:
            return row_map.get(f"{_GEIST_SENSOR}.{sub}.1.{col}.{idx}")

        for probe_idx in range(1, 5):
            t_raw = _g("4", "4", probe_idx)
            if t_raw:
                try:
                    loc = _g("4", "2", probe_idx) or "Inlet"
                    readings.append({"label": f"{loc} Temp", "value": round(int(t_raw) / 10.0, 1),
                                     "unit": "°C", "alarm": False})
                except ValueError:
                    pass
            h_raw = _g("5", "4", probe_idx)
            if h_raw:
                try:
                    loc = _g("5", "2", probe_idx) or "Inlet"
                    readings.append({"label": f"{loc} Humidity", "value": round(int(h_raw) / 10.0, 1),
                                     "unit": "%RH", "alarm": False})
                except ValueError:
                    pass
            d_raw = _g("6", "4", probe_idx)
            if d_raw:
                try:
                    loc = _g("6", "2", probe_idx) or "Inlet"
                    readings.append({"label": f"{loc} Dewpoint", "value": round(int(d_raw) / 10.0, 1),
                                     "unit": "°C", "alarm": False})
                except ValueError:
                    pass

    if readings:
        return readings

    # ── APC NetBotz ──────────────────────────────────────────────────────────
    rows = await _snmp_walk(agent_ip, community, port, _APC_NETBOTZ, timeout, 30)
    if rows:
        row_map = {oid: val for oid, val in rows}
        _NETBOTZ = [
            (f"{_APC_NETBOTZ}.10.1", f"{_APC_NETBOTZ}.2.1", "°C",  0.1),
            (f"{_APC_NETBOTZ}.10.2", f"{_APC_NETBOTZ}.2.2", "%RH", 1.0),
            (f"{_APC_NETBOTZ}.10.3", f"{_APC_NETBOTZ}.2.3", "m/s", 0.1),
        ]
        for val_oid, lbl_oid, unit, scale in _NETBOTZ:
            raw = row_map.get(val_oid)
            label = row_map.get(lbl_oid) or val_oid.rsplit(".", 1)[-1]
            if raw:
                try:
                    readings.append({"label": label, "value": round(int(raw) * scale, 1),
                                     "unit": unit, "alarm": False})
                except (ValueError, TypeError):
                    pass

    return readings


# ── Per-device test ───────────────────────────────────────────────────────────

async def _test_device(ip: str, agent_ip: str, community: str, port: int,
                       timeout: int,
                       do_interfaces: bool,
                       do_lldp: bool,
                       do_metrics: bool,
                       do_sensor: bool = False) -> DeviceResult:
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
        elif (not val or val in ("noSuchInstance", "noSuchObject", "endOfMibView")
              or val.lower().startswith("no such")):
            result.system.append(OIDResult(oid=oid, name=name,
                                            error=val or "no value"))
        else:
            result.system.append(OIDResult(oid=oid, name=name, value=val))

    # Mark unreachable if every system OID failed
    if all(not r.ok for r in result.system):
        result.unreachable = True
        errors = {r.error for r in result.system}
        result.error = next(iter(errors), "no response")
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        return result

    # ── Performance metrics ────────────────────────────────────────────────

    if do_metrics:
        perf_oids = [oid for oid, _ in PERF_OIDS]

        try:
            perf_raw = await _snmp_get(
                agent_ip,
                community,
                port,
                perf_oids,
                timeout,
            )

            for oid, name in PERF_OIDS:
                val = perf_raw.get(oid, "")

                if val.startswith("ERROR:"):
                    result.performance.append(
                        OIDResult(oid=oid, name=name, error=val[7:].strip())
                    )
                elif (
                    not val
                    or val in ("noSuchInstance", "noSuchObject", "endOfMibView")
                    or val.lower().startswith("no such")
                ):
                    result.performance.append(
                        OIDResult(oid=oid, name=name, error=val or "no value")
                    )
                else:
                    result.performance.append(
                        OIDResult(oid=oid, name=name, value=val)
                    )

        except Exception as exc:
            result.performance.append(
                OIDResult(
                    oid="metrics",
                    name="metrics",
                    error=str(exc),
                )
            )

    # ── Temperature sensors ────────────────────────────────────────────────

    if do_metrics:
        try:
            rows = await _snmp_walk(
                agent_ip,
                community,
                port,
                TEMP_SENSOR_OID,
                timeout,
                200,
            )

            for oid_str, val in rows:
                try:
                    temp_c = int(val) / 10
                except (ValueError, TypeError):
                    temp_c = val

                result.temperatures.append({
                    "oid": oid_str,
                    "value": temp_c,
                })

        except (ValueError, TypeError):
            pass

        # CISCO-ENVMON-MIB: walk only when sysObjectID indicates Cisco (1.3.6.1.4.1.9.*)
        sys_oid = next(
            (r.value for r in result.system if r.name == "sysObjectID" and r.ok),
            ""
        )
        if sys_oid.startswith("1.3.6.1.4.1.9."):
            try:
                cisco_rows = await _snmp_walk(
                    agent_ip, community, port,
                    CISCO_ENVMON_TEMP_OID, timeout, 20,
                )
                for oid_str, val in cisco_rows:
                    try:
                        temp_c = float(int(val))
                    except (ValueError, TypeError):
                        temp_c = val
                    result.temperatures.append({"oid": oid_str, "value": temp_c})
            except (ValueError, TypeError):
                pass

    # ── Interface table walk ───────────────────────────────────────────────────
    # IF-MIB stores each column for all interfaces before moving to the next
    # column.  With N interfaces and ~10 useful columns we need up to N*10 rows.
    if do_interfaces:
        rows = await _snmp_walk(agent_ip, community, port, IFACE_TABLE_OID, timeout, 2000)
        iface_map: Dict[int, dict] = {}
        for oid_str, val in rows:
            parts = oid_str.split(".")
            if len(parts) < 2:
                continue
            try:
                col = int(parts[-2])  # column
                idx = int(parts[-1])  # interface index
            except ValueError:
                continue
            iface_map.setdefault(idx, {"index": idx})
            if col == 2:   iface_map[idx]["descr"]       = val
            elif col == 5: iface_map[idx]["speed"]       = val
            elif col == 7: iface_map[idx]["admin"]       = "up" if val == "1" else "down"
            elif col == 8: iface_map[idx]["oper"]        = "up" if val == "1" else "down"
            elif col == 10:iface_map[idx]["in_octets"]   = val
            elif col == 16:iface_map[idx]["out_octets"]  = val
        result.interfaces = sorted(iface_map.values(), key=lambda x: x["index"])

    # ── LLDP walk ─────────────────────────────────────────────────────────────
    if do_lldp:
        rows = await _snmp_walk(agent_ip, community, port, LLDP_REM_OID, timeout, 2000)
        # LLDP remote table OID: 1.0.8802.1.1.2.1.4.1.1.{col}.{timemark}.{port}.{idx}
        # parts[-4]=col  parts[-3]=timemark  parts[-2]=port  parts[-1]=idx
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

    # ── Vendor sensor readings ────────────────────────────────────────────────
    if do_sensor:
        try:
            result.sensor_readings = await _query_sensor_data(
                agent_ip, community, port, timeout)
        except Exception:
            pass

    result.elapsed_ms = (time.perf_counter() - t0) * 1000
    return result


# ── Rendering ─────────────────────────────────────────────────────────────────

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

    # System OIDs
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

    # Performance metrics
    if result.performance:
        print(f"\n  {bold('Performance Metrics')}:")

        perf_map = {
            r.name: r.value
            for r in result.performance
            if r.ok
        }

        try:
            idle_val = perf_map.get("cpuIdle")

            if idle_val is not None:
                try:
                    idle = float(idle_val)
                    cpu_usage = 100 - idle
                    print(f"    CPU Usage      : {cpu_usage:.1f}%")
                except Exception:
                    print("    CPU Usage      : unavailable")
            else:
                print("    CPU Usage      : unavailable")
        except Exception:
            pass

        try:
            total_val = perf_map.get("memTotalKB")
            avail_val = perf_map.get("memAvailKB")

            if total_val and avail_val:
                try:
                    total = int(total_val)
                    avail = int(avail_val)

                    if total > 0:
                        used = total - avail
                        pct = (used / total) * 100

                        print(f"    Memory Usage   : {pct:.1f}%")
                        print(f"    Memory Used    : {used // 1024} MB")
                        print(f"    Memory Total   : {total // 1024} MB")
                except Exception:
                    print("    Memory Usage   : unavailable")
            else:
                print("    Memory Usage   : unavailable")
        except Exception:
            pass

        try:
            dpct_val  = perf_map.get("diskPercent")
            dtot_val  = perf_map.get("diskTotalKB")
            davail_val = perf_map.get("diskAvailKB")

            if dpct_val is not None:
                try:
                    dpct  = int(dpct_val)
                    dtot  = int(dtot_val)  if dtot_val  else 0
                    davail = int(davail_val) if davail_val else 0
                    print(f"    Disk Usage     : {dpct}%")
                    if dtot > 0:
                        print(f"    Disk Used      : {(dtot - davail) // 1024} MB")
                        print(f"    Disk Total     : {dtot // 1024} MB")
                except (ValueError, TypeError):
                    print("    Disk Usage     : unavailable")
            else:
                print("    Disk Usage     : unavailable")
        except (ValueError, TypeError):
            pass

    # Temperature sensors
    if result.temperatures:
        print(f"\n  {bold('Temperature Sensors')}:")

        _TEMP_LABELS = {
            "1.3.6.1.2.1.99.1.1.1.4.1":          "Inlet Temperature",
            "1.3.6.1.2.1.99.1.1.1.4.2":          "CPU Temperature",
            "1.3.6.1.4.1.9.9.13.1.3.1.3.1":      "Cisco Inlet Temp",
            "1.3.6.1.4.1.9.9.13.1.3.1.3.2":      "Cisco CPU Temp",
        }
        for sensor in result.temperatures:
            label = _TEMP_LABELS.get(sensor['oid'], sensor['oid'])
            print(f"    {label:<22} =  {sensor['value']} °C")

    # Vendor sensor readings (Raritan / Vertiv Geist / APC NetBotz)
    if result.sensor_readings:
        print(f"\n  {bold('Sensor Readings')}:")
        col_w = max(len(r["label"]) for r in result.sensor_readings) + 2
        for r in result.sensor_readings:
            val_str = f"{r['value']} {r['unit']}".strip()
            alarm   = r.get("alarm", False)
            line    = f"    {r['label']:<{col_w}} {val_str}"
            print(red(line) if alarm else line)
        if not result.temperatures:
            print()

    # Summary line
    total = len(result.system)
    print(f"\n  {green(str(result.passed))}/{total} system OIDs OK", end="")
    if result.failed:
        print(f"  {red(str(result.failed))} failed", end="")
    print()

    # Interfaces
    if result.interfaces:
        print(f"\n  {bold('Interfaces')} ({len(result.interfaces)} found):")
        fmt = "    {idx:>4}  {descr:<28} {admin:>5}/{oper:<5}  {speed}"
        print(grey(fmt.format(idx="idx", descr="ifDescr", admin="admin",
                              oper="oper", speed="speed")))
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
    ok    = sum(1 for r in results if not r.unreachable and r.failed == 0)
    warn  = sum(1 for r in results if not r.unreachable and r.failed  > 0)
    fail  = sum(1 for r in results if r.unreachable)
    fmt = "  {ip:<20} {status:<20} {elapsed}"
    print(grey(fmt.format(ip="IP", status="Status", elapsed="ms")))
    for r in results:
        if r.unreachable:
            status = red("UNREACHABLE")
        elif r.failed == 0:
            status = green("OK")
        else:
            status = yellow(f"{r.failed} OID(s) failed")
        print(fmt.format(
            ip      = r.ip,
            status  = status,
            elapsed = grey(f"{r.elapsed_ms:.0f}"),
        ))
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
                   help="Host where snmpsim is listening (default: 127.0.0.1). "
                        "The simulator binds to 0.0.0.0 and routes by community "
                        "string, so requests must go to this host not the device IP.")
    p.add_argument("--port", "-p", type=int, default=161,
                   help="SNMP UDP port (default: 161)")
    p.add_argument("--community", "-c", default=None,
                   help="Community string (default: same as device IP)")
    p.add_argument("--timeout", "-t", type=int, default=5,
                   help="Per-OID timeout in seconds (default: 5)")
    p.add_argument("--interfaces", "-i", action="store_true",
                   help="Walk the IF-MIB interface table")
    p.add_argument("--lldp", "-l", action="store_true",
                   help="Walk the LLDP-MIB remote neighbour table")
    p.add_argument("--full", "-f", action="store_true",
                   help="Equivalent to --interfaces --lldp")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="Only print failures and summary")
    p.add_argument("--metrics", "-m", action="store_true",
                   help="Collect CPU, memory and temperature metrics")
    p.add_argument("--sensor", "-s", action="store_true",
                   help="Query vendor sensor OIDs (Raritan DPX2 / Vertiv Geist / APC NetBotz) "
                        "and display all readings (temp/humidity/dewpoint/airflow/leak)")
    return p.parse_args()


async def _main(args: argparse.Namespace) -> int:
    do_ifaces  = args.interfaces or args.full
    do_lldp    = args.lldp or args.full
    do_metrics = args.metrics or args.full
    do_sensor  = args.sensor or args.full

    print(bold(f"\ndataCenter SNMP Tester  —  {len(args.ips)} device(s)"))
    print(grey(f"agent={args.agent}  port={args.port}  timeout={args.timeout}s  "
               f"interfaces={'yes' if do_ifaces else 'no'}  "
               f"lldp={'yes' if do_lldp else 'no'}\n"))

    tasks = [
        _test_device(
            ip        = ip,
            agent_ip  = args.agent,
            community = args.community if args.community else ip,
            port      = args.port,
            timeout   = args.timeout,
            do_interfaces = do_ifaces,
            do_lldp       = do_lldp,
            do_metrics=do_metrics,
            do_sensor=do_sensor,
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

    # Windows requires ProactorEventLoop for asyncio UDP
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        rc = asyncio.run(_main(args))
    except KeyboardInterrupt:
        print("\nAborted.")
        rc = 130

    sys.exit(rc)
