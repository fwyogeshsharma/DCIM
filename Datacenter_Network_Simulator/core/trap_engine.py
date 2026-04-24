"""
Trap Engine — sends SNMPv2c traps from simulated devices to a trap receiver.

Uses a background thread with its own asyncio event loop so Qt's main thread
is never blocked.  Emits Qt signals for the UI to consume.
"""
from __future__ import annotations

import asyncio
import random
import threading
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, Signal

from core.device_manager import Device
from core.trap_definitions import (
    TrapType, TrapDefinition, TRAP_DEFINITIONS, APPLICABLE_TRAPS,
    get_applicable_traps,
)


# ── Value object emitted on every sent trap ───────────────────────────────────

class TrapEvent:
    def __init__(self, device: Device, trap_type: TrapType, details: str = ""):
        self.timestamp  = datetime.now()
        self.device     = device
        self.trap_type  = trap_type
        self.defn: TrapDefinition = TRAP_DEFINITIONS[trap_type]
        self.details    = details

    def __repr__(self):
        return (f"<TrapEvent {self.timestamp:%H:%M:%S} "
                f"{self.device.name} {self.trap_type.value}>")


# ── Engine ────────────────────────────────────────────────────────────────────

class TrapEngine(QObject):
    """
    Signals
    -------
    trap_sent(TrapEvent)   — emitted after each trap is successfully dispatched
    trap_error(str)        — emitted when pysnmp reports an error
    """

    trap_sent  = Signal(object)   # TrapEvent
    trap_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._receiver_ip   = "127.0.0.1"
        self._receiver_port = 162
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._simulating = False
        self._sim_devices: list[Device] = []

    # ── Configuration ─────────────────────────────────────────────────────────

    def configure(self, ip: str, port: int):
        self._receiver_ip   = ip
        self._receiver_port = port

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
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
        self._simulating = False
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

    # ── Simulation ────────────────────────────────────────────────────────────

    def start_simulation(self, devices: list[Device]):
        """Randomly generate plausible traps in the background."""
        self._sim_devices = list(devices)
        self._simulating  = True
        if not self._loop or not self._loop.is_running():
            self.start()
        asyncio.run_coroutine_threadsafe(self._simulation_loop(), self._loop)

    def stop_simulation(self):
        self._simulating = False

    def update_sim_devices(self, devices: list[Device]):
        self._sim_devices = list(devices)

    async def _simulation_loop(self):
        # Randomised weights — link events are most common; temperature is rare
        weights = {
            TrapType.LINK_DOWN:         12,
            TrapType.LINK_UP:           10,
            TrapType.AUTH_FAILURE:       6,
            TrapType.CPU_HIGH:           8,
            TrapType.TEMPERATURE_ALERT:  3,
            TrapType.COLD_START:         2,
            TrapType.BGP_DOWN:           4,
        }
        while self._simulating and self._sim_devices:
            await asyncio.sleep(random.uniform(6, 18))
            if not self._simulating or not self._sim_devices:
                break

            device = random.choice(self._sim_devices)
            applicable = get_applicable_traps(device.device_type.value, device.vendor.value, device.model_name)
            population = [t for t in applicable if t in weights]
            trap_type  = random.choices(
                population, weights=[weights[t] for t in population], k=1
            )[0]

            kwargs: dict = {}

            # Only send CPU high when CPU is genuinely high
            if trap_type == TrapType.CPU_HIGH and device.cpu_usage < 75:
                continue

            # Pick a random interface for link traps
            if trap_type in (TrapType.LINK_DOWN, TrapType.LINK_UP):
                if device.interfaces:
                    kwargs["iface_index"] = random.choice(device.interfaces).index

            if trap_type == TrapType.TEMPERATURE_ALERT:
                kwargs["temperature"] = random.randint(62, 92)

            if trap_type == TrapType.BGP_DOWN:
                kwargs["peer_addr"] = (
                    f"10.{random.randint(0,255)}.{random.randint(0,255)}.1"
                )

            await self._send_async(device, trap_type, **kwargs)

    # ── Async internals ───────────────────────────────────────────────────────

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

            # Build PDU directly so we bypass VACM access checks in send_varbinds
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

            await asyncio.sleep(0.3)  # yield to event loop to flush UDP send

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

        details = self._format_details(device, trap_type, **kwargs)
        self.trap_sent.emit(TrapEvent(device, trap_type, details))

    @staticmethod
    def _build_extra_varbinds(device: Device, trap_type: TrapType, **kwargs):
        """Return the type-specific extra varbinds (excluding sysUpTime and snmpTrapOID)."""
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

        if trap_type == TrapType.COLD_START:
            return [
                (_oid('1.3.6.1.2.1.1.1.0'), rfc1902.OctetString(device.sys_descr)),
            ]

        if trap_type in (TrapType.AUTH_FAILURE,):
            return []

        if trap_type == TrapType.CPU_HIGH:
            return [
                (_oid('1.3.6.1.4.1.9999.1.1'), rfc1902.Gauge32(device.cpu_usage)),
                (_oid('1.3.6.1.4.1.9999.1.4'), rfc1902.Gauge32(80)),
            ]

        if trap_type == TrapType.TEMPERATURE_ALERT:
            temp = kwargs.get("temperature", random.randint(62, 90))
            return [
                (_oid('1.3.6.1.4.1.9999.1.2'), rfc1902.Gauge32(temp)),
                (_oid('1.3.6.1.4.1.9999.1.3'), rfc1902.Gauge32(60)),
            ]

        if trap_type == TrapType.BGP_DOWN:
            peer = kwargs.get("peer_addr", "10.0.0.1")
            return [
                (_oid('1.3.6.1.2.1.15.3.1.7.0'), rfc1902.OctetString(peer)),
                (_oid('1.3.6.1.2.1.15.3.1.14.0'), rfc1902.Integer32(1)),
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
            return f"CPU {device.cpu_usage}%  (threshold 80%)"
        if trap_type == TrapType.TEMPERATURE_ALERT:
            return f"Temperature {kwargs.get('temperature', '—')}°C  (threshold 60°C)"
        if trap_type == TrapType.BGP_DOWN:
            return f"Peer {kwargs.get('peer_addr', '?')} → Idle"
        if trap_type == TrapType.AUTH_FAILURE:
            return "Incorrect community string"
        if trap_type == TrapType.COLD_START:
            return "Device restarted"
        return ""