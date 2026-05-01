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

from core.device_manager import Device
from core.trap_definitions import (
    TrapType, TrapDefinition, TRAP_DEFINITIONS, OID_TO_TRAP_TYPE,
)

if TYPE_CHECKING:
    from core.rule_engine import TrapAction, RuleEngine
    from core.device_manager import DeviceManager


# ── Value object emitted on every sent trap ───────────────────────────────────

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
            snmp_config.add_v1_system(snmp_engine, 'trap-comm', device.snmp_community)
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
            snmp_config.add_v1_system(snmp_engine, 'trap-comm', device.snmp_community)
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

        if trap_type == TrapType.CPU_HIGH:
            val = int(kwargs.get("metric_value", device.cpu_usage))
            return [
                (_oid('1.3.6.1.4.1.99999.2.1'), rfc1902.Gauge32(val)),
                (_oid('1.3.6.1.4.1.99999.2.5'), rfc1902.Gauge32(90)),
            ]

        if trap_type == TrapType.MEMORY_HIGH:
            val = int(kwargs.get("metric_value",
                                 device.memory_used * 100 // max(1, device.memory_total)))
            return [
                (_oid('1.3.6.1.4.1.99999.2.2'), rfc1902.Gauge32(val)),
                (_oid('1.3.6.1.4.1.99999.2.6'), rfc1902.Gauge32(85)),
            ]

        if trap_type == TrapType.TEMPERATURE_ALERT:
            temp = int(kwargs.get("metric_value", random.randint(62, 90)))
            return [
                (_oid('1.3.6.1.4.1.99999.2.3'), rfc1902.Gauge32(temp)),
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
        return ""