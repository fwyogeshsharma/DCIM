"""
Trap Engine — sends SNMPv2c traps from simulated devices to a trap receiver.

All trap generation is rule-driven: the rule engine evaluates DeviceFact
objects each tick and fires TrapActions; this engine dispatches them as
SNMPv2c UDP packets.  Manual one-shot traps are still supported via send_trap().

Uses a background thread with its own asyncio event loop so Qt's main thread
is never blocked.  Emits Qt signals for the UI to consume.
"""
from __future__ import annotations

import asyncio
import random
import threading
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from core.device_manager import Device, DeviceType
from core.trap_definitions import (
    TrapType, TrapDefinition, TRAP_DEFINITIONS, OID_TO_TRAP_TYPE,
    # sensor trap types imported explicitly for varbind dispatch
)
# Convenience aliases used in _build_extra_varbinds / _format_details
_HUMIDITY_ALERT = TrapType.HUMIDITY_ALERT
_DEWPOINT_ALERT = TrapType.DEWPOINT_ALERT
_AIRFLOW_ALERT  = TrapType.AIRFLOW_ALERT

if TYPE_CHECKING:
    from core.rule_engine import TrapAction, RuleEngine
    from core.device_manager import DeviceManager


# ── Value object emitted on every sent trap ───────────────────────────────────

def _trap_source_ip(device: Device, trap_type: Optional[TrapType] = None) -> str:
    """IP of the agent that conceptually sent this trap.

    BMC platform events come from the server's BMC (mgmt IP). Server OS-agent
    traps come from the production IP — the OS owns the prod NIC. Everything
    else (NOS, UPS, PDU, sensors) answers on its mgmt IP when it has one.
    """
    mgmt = getattr(device, "mgmt_ip", "") or ""
    if trap_type in (TrapType.SERVER_POWER_OFF, TrapType.SERVER_POWER_ON):
        return mgmt or device.ip_address
    if device.device_type == DeviceType.SERVER:
        return device.ip_address or mgmt
    return mgmt or device.ip_address


class TrapEvent:
    def __init__(self, device: Device, trap_type: TrapType, details: str = "",
                 rule_name: str = "", iface_index: Optional[int] = None):
        self.timestamp   = datetime.now()
        self.device      = device
        self.trap_type   = trap_type
        self.defn: TrapDefinition = TRAP_DEFINITIONS[trap_type]
        self.details     = details
        self.rule_name   = rule_name   # "" = manual one-shot trap
        self.iface_index = iface_index
        self.source_ip   = _trap_source_ip(device, trap_type)

    def __repr__(self):
        return (f"<TrapEvent {self.timestamp:%H:%M:%S} "
                f"{self.device.name} {self.trap_type.value}>")


# ── Engine ────────────────────────────────────────────────────────────────────

class TrapEngine(QObject):
    """
    Signals
    -------
    trap_sent(TrapEvent)       — emitted after each trap is successfully dispatched
    trap_error(str)            — emitted when pysnmp reports an error
    link_state_changed(object, int, bool)
                               — emitted immediately when a rule fires for a link
                                 state change: (Device, iface_index, is_up)
                                 Does NOT wait for SNMP delivery.
    """

    trap_sent           = Signal(object)         # TrapEvent
    trap_error          = Signal(str)
    link_state_changed  = Signal(object, int, bool)  # Device, iface_index, is_up

    def __init__(self, parent=None):
        super().__init__(parent)
        self._receiver_ip   = "127.0.0.1"
        self._receiver_port = 162
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # Rule engine integration
        self._rule_engine: Optional["RuleEngine"] = None
        self._device_manager: Optional["DeviceManager"] = None
        self._rule_engine_enabled: bool = False

    # ── Configuration ─────────────────────────────────────────────────────────

    def configure(self, ip: str, port: int):
        self._receiver_ip   = ip
        self._receiver_port = port

    def set_rule_engine(self, engine: "RuleEngine", device_manager: "DeviceManager"):
        """Attach a rule engine and device manager for rule-driven trap dispatch."""
        self._rule_engine = engine
        self._device_manager = device_manager
        engine.set_action_callback(self._on_rule_action)

    def set_rule_engine_enabled(self, enabled: bool):
        self._rule_engine_enabled = enabled
        if self._rule_engine is not None:
            self._rule_engine.set_enabled(enabled)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        # Guard: thread alive means the loop was created and run_forever() is
        # imminent — a second start() call before is_running() becomes True
        # would overwrite self._loop and orphan all already-queued coroutines.
        if self._thread and self._thread.is_alive():
            return
        if self._loop and self._loop.is_running():
            return
        import sys
        self._loop = (asyncio.ProactorEventLoop() if sys.platform == "win32"
                      else asyncio.new_event_loop())
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="TrapEngine"
        )
        self._thread.start()

    def stop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop   = None
        self._thread = None

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ── Public send API ───────────────────────────────────────────────────────

    def send_trap(self, device: Device, trap_type: TrapType, **kwargs):
        """Queue a trap for immediate dispatch (non-blocking)."""
        if not self._loop or not self._loop.is_running():
            self.start()
        asyncio.run_coroutine_threadsafe(
            self._send_async(device, trap_type, **kwargs), self._loop
        )

    # ── Rule engine action handler ────────────────────────────────────────────

    def _on_rule_action(self, action: "TrapAction"):
        """Called by the rule engine when a rule fires. Thread-safe."""
        if not self._rule_engine_enabled:
            return

        device = action.device_ref
        if device is None and self._device_manager:
            # Fallback: look up by device_id (device.name)
            device = next(
                (d for d in self._device_manager.get_all_devices()
                 if d.name == action.device_id),
                None,
            )
        if device is None:
            return

        if not self._loop or not self._loop.is_running():
            self.start()

        oid = action.rule.trap_oid
        trap_type = OID_TO_TRAP_TYPE.get(oid)

        if trap_type is None:
            # Unknown OID — send raw trap with auto-generated varbinds
            asyncio.run_coroutine_threadsafe(
                self._send_raw_trap_async(device, oid, action.extra,
                                         action.rule.rule_name),
                self._loop,
            )
            return

        # Map extra kwargs to expected send_trap kwargs
        kwargs = {}
        if "iface_index" in action.extra:
            kwargs["iface_index"] = action.extra["iface_index"]
        if "peer_addr" in action.extra:
            kwargs["peer_addr"] = action.extra["peer_addr"]
        if "bgp_state" in action.extra:
            kwargs["bgp_state"] = action.extra["bgp_state"]
        if "flap_count" in action.extra:
            kwargs["flap_count"] = action.extra["flap_count"]
        if "rack_id" in action.extra:
            kwargs["rack_id"] = action.extra["rack_id"]
            kwargs["down_count"] = action.extra.get("down_count", 0)
        if "metric_value" in action.extra:
            kwargs["metric_value"] = action.extra["metric_value"]
        elif "cpu_usage" in action.extra:
            kwargs["metric_value"] = action.extra["cpu_usage"]
        elif "temperature" in action.extra:
            kwargs["metric_value"] = action.extra["temperature"]
        elif "memory_usage" in action.extra:
            kwargs["metric_value"] = action.extra["memory_usage"]
        else:
            # Generic: extract the triggering metric's value from extra
            cond = getattr(action.rule, "condition", None)
            metric_name = getattr(cond, "metric", None) if cond else None
            if metric_name and metric_name in action.extra:
                val = action.extra[metric_name]
                if isinstance(val, (int, float)):
                    kwargs["metric_value"] = val

        kwargs["rule_name"] = action.rule.rule_name

        # Emit link_state_changed immediately — before SNMP delivery — so the
        # topology graph updates even if the SNMP send fails or times out.
        if trap_type == TrapType.LINK_DOWN and "iface_index" in action.extra:
            self.link_state_changed.emit(device, action.extra["iface_index"], False)
        elif trap_type == TrapType.LINK_UP and "iface_index" in action.extra:
            self.link_state_changed.emit(device, action.extra["iface_index"], True)

        asyncio.run_coroutine_threadsafe(
            self._send_async(device, trap_type, **kwargs), self._loop
        )

    # ── Async send internals ──────────────────────────────────────────────────

    async def _send_async(self, device: Device, trap_type: TrapType, **kwargs):
        defn = TRAP_DEFINITIONS[trap_type]
        snmp_engine = None
        dispatcher = None
        try:
            from pysnmp.entity.engine import SnmpEngine
            from pysnmp.entity import config as snmp_config
            from pysnmp.entity.rfc3413 import ntforg
            from pysnmp.carrier.asyncio.dispatch import AsyncioDispatcher
            from pysnmp.carrier.asyncio.dgram import udp as udp_mod
            from pysnmp.proto.api import v2c as proto_v2c
            from pysnmp.proto import rfc1902
            from pyasn1.type import univ

            loop = asyncio.get_running_loop()

            snmp_engine = SnmpEngine()
            dispatcher = AsyncioDispatcher(loop=loop)
            snmp_engine.register_transport_dispatcher(dispatcher)
            snmp_config.add_transport(
                snmp_engine, udp_mod.DOMAIN_NAME,
                udp_mod.UdpAsyncioTransport().open_client_mode(),
            )
            # Community mirrors the firing agent's IP (server OS → prod IP,
            # BMC → mgmt IP) — same convention as the poll side.
            snmp_config.add_v1_system(
                snmp_engine, 'trap-comm',
                _trap_source_ip(device, trap_type) or device.snmp_community)
            snmp_config.add_target_parameters(
                snmp_engine, 'trap-params', 'trap-comm', 'noAuthNoPriv', 1,
            )
            snmp_config.add_target_address(
                snmp_engine, 'trap-target', udp_mod.DOMAIN_NAME,
                (self._receiver_ip, self._receiver_port),
                'trap-params', tagList='trap-tag',
                timeout=100, retryCount=0,
            )

            def _oid(s: str):
                return univ.ObjectIdentifier(tuple(int(x) for x in s.split('.')))

            pdu = proto_v2c.SNMPv2TrapPDU()
            proto_v2c.apiPDU.set_defaults(pdu)
            all_varbinds = (
                [(_oid('1.3.6.1.2.1.1.3.0'), rfc1902.TimeTicks(0)),
                 (_oid('1.3.6.1.6.3.1.1.4.1.0'), _oid(defn.oid))]
                + self._build_extra_varbinds(device, trap_type, **kwargs)
            )
            proto_v2c.apiPDU.set_varbinds(pdu, all_varbinds)

            ntforg.NotificationOriginator().send_pdu(
                snmp_engine, 'trap-target', None, b'', pdu,
            )
            await asyncio.sleep(0.3)

        except Exception as ex:
            self.trap_error.emit(
                f"Trap exception ({device.name} / {trap_type.value}): {ex}"
            )
            return
        finally:
            try:
                if dispatcher is not None:
                    dispatcher.close_dispatcher()
                if snmp_engine is not None:
                    snmp_engine.unregister_transport_dispatcher()
            except Exception:
                pass

        if not kwargs.get("no_table"):
            rule_name = kwargs.get("rule_name", "")
            details = self._format_details(device, trap_type, **kwargs)
            self.trap_sent.emit(TrapEvent(device, trap_type, details, rule_name,
                                          iface_index=kwargs.get("iface_index")))

    async def _send_raw_trap_async(self, device: Device, oid: str, extra: dict,
                                   rule_name: str = ""):
        """Send a trap for an OID that has no TrapType mapping."""
        snmp_engine = None
        dispatcher = None
        try:
            from pysnmp.entity.engine import SnmpEngine
            from pysnmp.entity import config as snmp_config
            from pysnmp.entity.rfc3413 import ntforg
            from pysnmp.carrier.asyncio.dispatch import AsyncioDispatcher
            from pysnmp.carrier.asyncio.dgram import udp as udp_mod
            from pysnmp.proto.api import v2c as proto_v2c
            from pysnmp.proto import rfc1902
            from pyasn1.type import univ

            loop = asyncio.get_running_loop()
            snmp_engine = SnmpEngine()
            dispatcher = AsyncioDispatcher(loop=loop)
            snmp_engine.register_transport_dispatcher(dispatcher)
            snmp_config.add_transport(
                snmp_engine, udp_mod.DOMAIN_NAME,
                udp_mod.UdpAsyncioTransport().open_client_mode(),
            )
            # Raw OIDs are rule-driven → always the OS/NOS agent, never BMC.
            snmp_config.add_v1_system(
                snmp_engine, 'trap-comm',
                _trap_source_ip(device, None) or device.snmp_community)
            snmp_config.add_target_parameters(
                snmp_engine, 'trap-params', 'trap-comm', 'noAuthNoPriv', 1,
            )
            snmp_config.add_target_address(
                snmp_engine, 'trap-target', udp_mod.DOMAIN_NAME,
                (self._receiver_ip, self._receiver_port),
                'trap-params', tagList='trap-tag',
                timeout=100, retryCount=0,
            )

            def _oid(s: str):
                return univ.ObjectIdentifier(tuple(int(x) for x in s.split('.')))

            pdu = proto_v2c.SNMPv2TrapPDU()
            proto_v2c.apiPDU.set_defaults(pdu)
            varbinds = [
                (_oid('1.3.6.1.2.1.1.3.0'), rfc1902.TimeTicks(0)),
                (_oid('1.3.6.1.6.3.1.1.4.1.0'), _oid(oid)),
                (_oid('1.3.6.1.2.1.1.5.0'), rfc1902.OctetString(device.name)),
            ]
            proto_v2c.apiPDU.set_varbinds(pdu, varbinds)
            ntforg.NotificationOriginator().send_pdu(
                snmp_engine, 'trap-target', None, b'', pdu,
            )
            await asyncio.sleep(0.3)
        except Exception as ex:
            self.trap_error.emit(f"Raw trap error ({device.name} / {oid}): {ex}")
            return
        finally:
            try:
                if dispatcher is not None:
                    dispatcher.close_dispatcher()
                if snmp_engine is not None:
                    snmp_engine.unregister_transport_dispatcher()
            except Exception:
                pass

    # ── Varbind builders ──────────────────────────────────────────────────────

    @staticmethod
    def _build_extra_varbinds(device: Device, trap_type: TrapType, **kwargs):
        from pyasn1.type import univ
        from pysnmp.proto import rfc1902

        def _oid(s: str):
            return univ.ObjectIdentifier(tuple(int(x) for x in s.split('.')))

        if trap_type in (TrapType.LINK_DOWN, TrapType.LINK_UP):
            idx   = kwargs.get("iface_index", 1)
            iface = next((i for i in device.interfaces if i.index == idx), None)
            oper  = 2 if trap_type == TrapType.LINK_DOWN else 1
            return [
                (_oid('1.3.6.1.2.1.2.2.1.1.1'), rfc1902.Integer32(idx)),
                (_oid('1.3.6.1.2.1.2.2.1.7.1'), rfc1902.Integer32(1)),
                (_oid('1.3.6.1.2.1.2.2.1.8.1'), rfc1902.Integer32(oper)),
                (_oid('1.3.6.1.2.1.2.2.1.2.1'),
                 rfc1902.OctetString(iface.name if iface else f"iface{idx}")),
            ]

        if trap_type in (TrapType.COLD_START, TrapType.WARM_START):
            return [(_oid('1.3.6.1.2.1.1.1.0'), rfc1902.OctetString(device.sys_descr))]

        if trap_type == TrapType.AUTH_FAILURE:
            return []

        if trap_type in (TrapType.CPU_HIGH, TrapType.CPU_SUSTAINED,
                         TrapType.CPU_TEMP_CRITICAL, TrapType.CPU_NORMAL):
            val = int(kwargs.get("metric_value", device.cpu_usage))
            return [
                (_oid('1.3.6.1.4.1.99999.2.1'), rfc1902.Gauge32(val)),
                (_oid('1.3.6.1.4.1.99999.2.5'), rfc1902.Gauge32(90)),
            ]

        if trap_type in (TrapType.MEMORY_HIGH, TrapType.MEMORY_NORMAL):
            val = int(kwargs.get("metric_value",
                                 device.memory_used * 100 // max(1, device.memory_total)))
            return [
                (_oid('1.3.6.1.4.1.99999.2.2'), rfc1902.Gauge32(val)),
                (_oid('1.3.6.1.4.1.99999.2.6'), rfc1902.Gauge32(85)),
            ]

        if trap_type in (TrapType.TEMPERATURE_ALERT, TrapType.TEMPERATURE_NORMAL,
                         TrapType.SENSOR_AMBIENT_TEMP_HIGH,
                         TrapType.SENSOR_AMBIENT_TEMP_CRITICAL,
                         TrapType.SENSOR_AMBIENT_TEMP_NORMAL):
            temp = int(kwargs.get("metric_value", getattr(device, "cpu_temp", 0)
                                  or random.randint(62, 90)))
            return [
                (_oid('1.3.6.1.4.1.99999.2.3'), rfc1902.Gauge32(max(0, temp))),
                (_oid('1.3.6.1.4.1.99999.2.7'), rfc1902.Gauge32(40)),
            ]

        if trap_type == TrapType.LINK_FLAP:
            count = int(kwargs.get("flap_count", 3))
            window = int(kwargs.get("window_sec", 60))
            return [
                (_oid('1.3.6.1.4.1.99999.2.4'), rfc1902.Counter32(count)),
                (_oid('1.3.6.1.4.1.99999.2.8'), rfc1902.Integer32(window)),
            ]

        if trap_type == TrapType.RACK_FAILURE:
            rack = kwargs.get("rack_id", "unknown")
            count = int(kwargs.get("down_count", 3))
            return [
                (_oid('1.3.6.1.4.1.99999.2.9'),  rfc1902.OctetString(str(rack))),
                (_oid('1.3.6.1.4.1.99999.2.10'), rfc1902.Integer32(count)),
            ]

        if trap_type in (TrapType.SERVER_POWER_OFF, TrapType.SERVER_POWER_ON):
            state = 2 if trap_type == TrapType.SERVER_POWER_OFF else 1
            return [
                # BMC chassis power state (matches the BMC SNMP dataset OID)
                (_oid('1.3.6.1.4.1.99999.26.1.1.0'), rfc1902.Integer32(state)),
                (_oid('1.3.6.1.4.1.99999.26.0.10'),
                 rfc1902.OctetString(str(kwargs.get("reset_type", "")))),
            ]

        if trap_type == TrapType.UPS_ON_BATTERY:
            return [(_oid('1.3.6.1.2.1.33.1.2.1.0'), rfc1902.Integer32(2))]

        if trap_type == TrapType.UPS_LOW_BATTERY:
            return [
                (_oid('1.3.6.1.2.1.33.1.2.1.0'), rfc1902.Integer32(3)),
                (_oid('1.3.6.1.2.1.33.1.2.4.0'), rfc1902.Integer32(5)),
            ]

        if trap_type == TrapType.BGP_DOWN:
            peer = kwargs.get("peer_addr", "10.0.0.1")
            state = kwargs.get("bgp_state", "idle")
            state_code = {"idle": 1, "connect": 2, "active": 3,
                          "opensent": 4, "openconfirm": 5, "established": 6}.get(state, 1)
            return [
                (_oid('1.3.6.1.2.1.15.3.1.7.0'),  rfc1902.OctetString(peer)),
                (_oid('1.3.6.1.2.1.15.3.1.14.0'), rfc1902.Integer32(state_code)),
            ]

        if trap_type in (_HUMIDITY_ALERT, TrapType.SENSOR_HIGH_HUMIDITY,
                         TrapType.SENSOR_CRITICAL_HUMIDITY, TrapType.SENSOR_LOW_HUMIDITY,
                         TrapType.SENSOR_HUMIDITY_NORMAL):
            val = int(round(float(kwargs.get("metric_value", None) or device.humidity)))
            return [
                (_oid('1.3.6.1.4.1.99999.2.12'), rfc1902.Gauge32(max(0, val))),   # humidity %
                (_oid('1.3.6.1.4.1.99999.2.15'), rfc1902.Gauge32(70)),             # threshold %
            ]

        if trap_type in (_DEWPOINT_ALERT, TrapType.SENSOR_DEWPOINT_NORMAL):
            # Encode as ×10 integer (e.g. 21.5°C → 215)
            raw = float(kwargs.get("metric_value", None) or device.dewpoint)
            val = int(round(raw * 10))
            return [
                (_oid('1.3.6.1.4.1.99999.2.13'), rfc1902.Integer32(val)),   # dewpoint ×10 °C
                (_oid('1.3.6.1.4.1.99999.2.16'), rfc1902.Integer32(210)),   # threshold 21.0°C ×10
            ]

        if trap_type in (_AIRFLOW_ALERT, TrapType.SENSOR_HIGH_AIRFLOW,
                         TrapType.SENSOR_LOW_AIRFLOW, TrapType.SENSOR_AIRFLOW_NORMAL):
            # Encode as ×10 integer (e.g. 3.5 m/s → 35)
            raw = float(kwargs.get("metric_value", None) or device.airflow)
            val = int(round(raw * 10))
            return [
                (_oid('1.3.6.1.4.1.99999.2.14'), rfc1902.Integer32(max(0, val))), # airflow ×10 m/s
                (_oid('1.3.6.1.4.1.99999.2.17'), rfc1902.Integer32(35)),           # threshold 3.5×10
            ]

        return []

    @staticmethod
    def _format_details(device: Device, trap_type: TrapType, **kwargs) -> str:
        if trap_type == TrapType.LINK_DOWN:
            idx   = kwargs.get("iface_index", 1)
            iface = next((i for i in device.interfaces if i.index == idx), None)
            return f"Interface {iface.name if iface else idx} went down"
        if trap_type == TrapType.LINK_UP:
            idx   = kwargs.get("iface_index", 1)
            iface = next((i for i in device.interfaces if i.index == idx), None)
            return f"Interface {iface.name if iface else idx} came up"
        if trap_type == TrapType.CPU_HIGH:
            val = kwargs.get("metric_value", device.cpu_usage)
            return f"CPU {val}%  (threshold 90%)"
        if trap_type == TrapType.MEMORY_HIGH:
            val = kwargs.get("metric_value",
                             device.memory_used * 100 // max(1, device.memory_total))
            return f"Memory {val}%  (threshold 85%)"
        if trap_type == TrapType.TEMPERATURE_ALERT:
            return f"Temperature {kwargs.get('metric_value', '—')}°C  (threshold 40°C)"
        if trap_type == TrapType.LINK_FLAP:
            return (f"Interface flapped {kwargs.get('flap_count', 3)}× "
                    f"in {kwargs.get('window_sec', 60):.0f}s")
        if trap_type == TrapType.RACK_FAILURE:
            return (f"Rack {kwargs.get('rack_id', '?')}: "
                    f"{kwargs.get('down_count', 3)} devices impaired")
        if trap_type == TrapType.SERVER_POWER_OFF:
            rt = kwargs.get("reset_type", "")
            return f"Chassis powered OFF{f' ({rt})' if rt else ''} — BMC platform event"
        if trap_type == TrapType.SERVER_POWER_ON:
            rt = kwargs.get("reset_type", "")
            return f"Chassis powered ON{f' ({rt})' if rt else ''} — BMC platform event"
        if trap_type == TrapType.UPS_ON_BATTERY:
            return "UPS switched to battery power"
        if trap_type == TrapType.UPS_LOW_BATTERY:
            return "UPS battery critically low"
        if trap_type == TrapType.BGP_DOWN:
            return f"Peer {kwargs.get('peer_addr', '?')} → {kwargs.get('bgp_state', 'idle')}"
        if trap_type == TrapType.AUTH_FAILURE:
            return "Incorrect community string"
        if trap_type in (TrapType.COLD_START, TrapType.WARM_START):
            return "Device restarted"
        if trap_type == _HUMIDITY_ALERT:
            val = kwargs.get("metric_value", device.humidity)
            return f"Humidity {float(val):.1f}%  (threshold 70%)"
        if trap_type == _DEWPOINT_ALERT:
            val = kwargs.get("metric_value", device.dewpoint)
            return f"Dew point {float(val):.1f}°C  (threshold 21°C)"
        if trap_type == _AIRFLOW_ALERT:
            val = kwargs.get("metric_value", device.airflow)
            return f"Airflow {float(val):.2f} m/s  (range 0.3–3.5 m/s)"
        # PDU load / power
        if trap_type in (TrapType.PDU_LOAD_HIGH, TrapType.PDU_LOAD_CRITICAL):
            val = kwargs.get("metric_value", "—")
            return f"PDU load {float(val):.1f}%"
        if trap_type in (TrapType.PDU_VOLTAGE_HIGH, TrapType.PDU_VOLTAGE_LOW):
            val = kwargs.get("metric_value", "—")
            return f"Input voltage {float(val):.1f} V"
        if trap_type == TrapType.PDU_PHASE_IMBALANCE:
            val = kwargs.get("metric_value", "—")
            return f"Phase imbalance {float(val):.1f}%"
        if trap_type == TrapType.PDU_POWER_FACTOR_LOW:
            val = kwargs.get("metric_value", "—")
            return f"Power factor {float(val):.2f}"
        if trap_type == TrapType.PDU_OUTLET_CURRENT_HIGH:
            val = kwargs.get("metric_value", "—")
            return f"Outlet current {float(val):.1f} A"
        if trap_type == TrapType.PDU_FREQUENCY_FAULT:
            val = kwargs.get("metric_value", "—")
            return f"Frequency {float(val):.2f} Hz  (normal 49.5–50.5 Hz)"
        if trap_type == TrapType.PDU_TEMP_HIGH:
            val = kwargs.get("metric_value", "—")
            return f"PDU temp {float(val):.1f}°C  (threshold 35°C)"
        if trap_type == TrapType.PDU_HUMIDITY_HIGH:
            val = kwargs.get("metric_value", "—")
            return f"PDU humidity {float(val):.1f}%  (threshold 70%)"
        if trap_type == TrapType.PDU_OUTLET_ON:
            return "Outlet switched on"
        if trap_type == TrapType.PDU_OUTLET_OFF:
            return "Outlet switched off"
        if trap_type == TrapType.PDU_BREAKER_TRIPPED:
            return "Circuit breaker tripped"
        if trap_type == TrapType.PDU_SMOKE_DETECTED:
            return "Smoke sensor triggered"
        if trap_type == TrapType.PDU_GROUND_FAULT:
            return "Ground fault detected"
        if trap_type == TrapType.PDU_OUTLET_FAILURE:
            return "Outlet hardware fault"
        # UPS extended
        if trap_type == TrapType.UPS_BATTERY_LOW_HEALTH:
            val = kwargs.get("metric_value", "—")
            return f"Battery health {float(val):.1f}%  (threshold 50%)"
        if trap_type == TrapType.UPS_BYPASS_ACTIVE:
            return "UPS switched to bypass mode"
        if trap_type == TrapType.UPS_BYPASS_CLEARED:
            return "UPS exited bypass mode"
        if trap_type == TrapType.UPS_BATTERY_HEALTH_RESTORED:
            val = kwargs.get("metric_value", "—")
            return f"Battery health recovered to {float(val):.1f}%"
        # Sensor mid/outlet temp
        if trap_type == TrapType.SENSOR_MID_TEMP_HIGH:
            val = kwargs.get("metric_value", "—")
            return f"Mid-rack temp {float(val):.1f}°C  (threshold 38°C)"
        if trap_type == TrapType.SENSOR_OUTLET_TEMP_HIGH:
            val = kwargs.get("metric_value", "—")
            return f"Exhaust temp {float(val):.1f}°C  (threshold 45°C)"
        # Generator
        if trap_type == TrapType.GEN_RUNNING:
            return "Generator started — utility power lost"
        if trap_type == TrapType.GEN_STOPPED:
            return "Generator stopped — utility power restored"
        if trap_type == TrapType.GEN_LOW_FUEL:
            return "Fuel tank below 20%"
        if trap_type == TrapType.GEN_LOW_COOLANT:
            return "Coolant level low"
        if trap_type == TrapType.GEN_BATTERY_FAILURE:
            return "Starting battery fault"
        if trap_type == TrapType.GEN_TRANSFER_SWITCH:
            return "ATS relay failure"
        if trap_type == TrapType.GEN_OVERCRANK:
            return "Failed to start — max crank attempts exceeded"
        # CPU / memory / temperature variants + recoveries
        if trap_type == TrapType.CPU_SUSTAINED:
            return f"CPU {kwargs.get('metric_value', device.cpu_usage)}% sustained >5 min  (threshold 90%)"
        if trap_type == TrapType.CPU_TEMP_CRITICAL:
            return f"CPU {kwargs.get('metric_value', device.cpu_usage)}% with temperature critical (both high)"
        if trap_type == TrapType.CPU_NORMAL:
            return f"CPU {kwargs.get('metric_value', device.cpu_usage)}%  (recovered <70%)"
        if trap_type == TrapType.MEMORY_NORMAL:
            val = kwargs.get("metric_value", device.memory_used * 100 // max(1, device.memory_total))
            return f"Memory {val}%  (recovered <70%)"
        if trap_type == TrapType.TEMPERATURE_NORMAL:
            val = kwargs.get("metric_value", None) or device.cpu_temp
            return f"Temperature {float(val):.1f}°C  (recovered <55°C)"
        # Sensor ambient temp variants + recovery
        if trap_type == TrapType.SENSOR_AMBIENT_TEMP_HIGH:
            val = kwargs.get("metric_value", None) or device.inlet_temp
            return f"Ambient temp {float(val):.1f}°C  (threshold 32°C)"
        if trap_type == TrapType.SENSOR_AMBIENT_TEMP_CRITICAL:
            val = kwargs.get("metric_value", None) or device.inlet_temp
            return f"Ambient temp {float(val):.1f}°C  (threshold 38°C)"
        if trap_type == TrapType.SENSOR_AMBIENT_TEMP_NORMAL:
            val = kwargs.get("metric_value", None) or device.inlet_temp
            return f"Ambient temp {float(val):.1f}°C  (recovered <28°C)"
        # Sensor humidity variants + recovery
        if trap_type == TrapType.SENSOR_HIGH_HUMIDITY:
            val = kwargs.get("metric_value", None) or device.humidity
            return f"Humidity {float(val):.1f}%  (threshold 70%)"
        if trap_type == TrapType.SENSOR_CRITICAL_HUMIDITY:
            val = kwargs.get("metric_value", None) or device.humidity
            return f"Humidity {float(val):.1f}%  (threshold 80%)"
        if trap_type == TrapType.SENSOR_LOW_HUMIDITY:
            val = kwargs.get("metric_value", None) or device.humidity
            return f"Humidity {float(val):.1f}%  (threshold 30%)"
        if trap_type == TrapType.SENSOR_HUMIDITY_NORMAL:
            val = kwargs.get("metric_value", None) or device.humidity
            return f"Humidity {float(val):.1f}%  (recovered 30–70%)"
        # Sensor airflow variants + recovery
        if trap_type == TrapType.SENSOR_HIGH_AIRFLOW:
            val = kwargs.get("metric_value", None) or device.airflow
            return f"Airflow {float(val):.2f} m/s  (threshold 3.5 m/s)"
        if trap_type == TrapType.SENSOR_LOW_AIRFLOW:
            val = kwargs.get("metric_value", None) or device.airflow
            return f"Airflow {float(val):.2f} m/s  (threshold 0.3 m/s)"
        if trap_type == TrapType.SENSOR_AIRFLOW_NORMAL:
            val = kwargs.get("metric_value", None) or device.airflow
            return f"Airflow {float(val):.2f} m/s  (recovered 0.3–3.5 m/s)"
        if trap_type == TrapType.SENSOR_DEWPOINT_NORMAL:
            val = kwargs.get("metric_value", None) or device.dewpoint
            return f"Dew point {float(val):.1f}°C  (recovered <17°C)"
        # PDU load recovery
        if trap_type == TrapType.PDU_LOAD_NORMAL:
            mv = kwargs.get("metric_value", None)
            return f"PDU load {float(mv):.1f}%  (recovered <70%)" if mv else "PDU load recovered (<70%)"
        # Fallback: use trap definition description
        defn = TRAP_DEFINITIONS.get(trap_type)
        return defn.description if defn else ""