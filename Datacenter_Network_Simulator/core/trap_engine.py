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
from core import vendor_oids
from core import sim_settings
from core.vendor_oids import APC, CISCO, DELL, HPE, LENOVO, LIEBERT, RARITAN, PET
# Convenience aliases used in _build_extra_varbinds / _format_details
_HUMIDITY_ALERT = TrapType.HUMIDITY_ALERT
_DEWPOINT_ALERT = TrapType.DEWPOINT_ALERT
_AIRFLOW_ALERT  = TrapType.AIRFLOW_ALERT

if TYPE_CHECKING:
    from core.rule_engine import TrapAction, RuleEngine
    from core.device_manager import DeviceManager


def _num(value, scale: int = 1) -> int:
    """Coerce a metric to the integer a varbind needs, defaulting to 0.

    Varbind construction happens before the PDU is sent, so an uncoercible value
    here costs the whole trap, not just its description text. Manual injection
    supplies no metric_value and a device may not carry the attribute the trap
    normally reads (a PDU sent a SENSOR_* trap, say), so neither is trusted.
    """
    try:
        return int(round(float(value) * scale))
    except (TypeError, ValueError):
        return 0


def _uptime_ticks(device) -> int:
    """sysUpTime.0 for the trap's first varbind, in TimeTicks (1/100 s).

    RFC 3416 makes sysUpTime.0 mandatory as varbind 1, and receivers use it to
    spot agent restarts and to order events that share a wall-clock second.
    Sending a constant 0 — as this did — reads as "the agent just rebooted" on
    every single trap. The agent's poll side already serves a real sys_uptime,
    so the trap must agree with it.
    """
    try:
        return max(0, int(getattr(device, "sys_uptime", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _fmt(value, decimals: int = 1) -> str:
    """Format a metric for a trap description, degrading to an em dash.

    Rule-driven traps always carry a numeric metric_value, but manual injection
    (the topology context menu) and the /traps/send endpoint do not. Formatting
    runs inside the trap dispatch path, so a raw float() on a missing value
    raised there and cost the trap its history record — see the PDU voltageHigh
    failures. Every numeric description goes through this instead.
    """
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


# ── Value object emitted on every sent trap ───────────────────────────────────

def _trap_source_ip(device: Device, trap_type: Optional[TrapType] = None) -> str:
    """IP of the agent that conceptually sent this trap.

    BMC platform events come from the server's BMC (mgmt IP). Server OS-agent
    traps come from the production IP — the OS owns the prod NIC. Everything
    else (NOS, UPS, PDU, sensors) answers on its mgmt IP when it has one.

    A Modbus RTU field transmitter has neither, and that is not an omission:
    Modbus has no unsolicited messaging at all, so a thermowell physically
    cannot raise a trap. On a real site the BMS/gateway polls it and raises the
    alarm on its behalf, which is exactly what falling back to the gateway IP
    models. The condition is still detected on the instrument's own reading —
    only the notification's source moves to the thing that can actually send it.

    The same holds for a device behind a BACnet/IP router and for a DPX2 probe on
    a PDU's sensor port: the carrier sends on its behalf, because the probe is an
    RJ-12 lead with no agent of its own.
    """
    mgmt = getattr(device, "mgmt_ip", "") or ""
    if trap_type in (TrapType.SERVER_POWER_OFF, TrapType.SERVER_POWER_ON):
        return mgmt or device.ip_address
    if device.device_type == DeviceType.SERVER:
        return device.ip_address or mgmt
    return (mgmt or device.ip_address
            or getattr(device, "modbus_gateway_ip", "")
            or getattr(device, "mstp_router_ip", "")
            or getattr(device, "host_pdu_ip", "") or "")


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


class _RawDefn:
    """Stand-in TrapDefinition for an OID with no TrapType, built from the rule."""
    def __init__(self, oid: str, display_name: str, severity: str):
        self.oid = oid
        self.display_name = display_name
        self.severity = severity


class RawTrapEvent:
    """History record for a trap whose OID has no TrapType mapping.

    Duck-types TrapEvent for AppState.record_trap. Without it those traps went out
    on the wire but never reached the UI's trap log, because only the typed path
    emitted trap_sent — so an operator saw an alarm raised and never saw it clear.
    That silence covered 27 of the 29 unmapped rules, nearly all of them recovery
    rules on enterprise OIDs.

    trap_type stays None (there genuinely isn't one); the panel falls back to
    display_name, which carries the rule name."""
    def __init__(self, device: Device, oid: str, details: str = "",
                 rule_name: str = "", severity: str = "informational"):
        self.timestamp   = datetime.now()
        self.device      = device
        self.trap_type   = None
        self.defn        = _RawDefn(oid, rule_name or oid, severity)
        self.details     = details
        self.rule_name   = rule_name
        self.iface_index = None
        self.source_ip   = _trap_source_ip(device, None)

    def __repr__(self):
        return (f"<RawTrapEvent {self.timestamp:%H:%M:%S} "
                f"{self.device.name} {self.defn.display_name}>")


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
        # Restored from the saved settings, exactly as a real NMC restores its
        # trap receivers from NVRAM at boot. Without this the default silently
        # redirected every trap to 127.0.0.1:162 on each restart, which reads
        # downstream as "the alarm path is broken" while telemetry stays healthy.
        self._receiver_ip   = str(sim_settings.get("trap_receiver_ip", "127.0.0.1"))
        self._receiver_port = int(sim_settings.get("trap_receiver_port", 162))
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # Rule engine integration
        self._rule_engine: Optional["RuleEngine"] = None
        self._device_manager: Optional["DeviceManager"] = None
        self._rule_engine_enabled: bool = False

        # Shared pysnmp stack. Building an SnmpEngine costs ~0.2 s because pysnmp
        # reloads its whole MIB tree from disk, so one is built lazily on the trap
        # loop and reused for every trap; only the per-community target registration
        # is incremental. All of these are touched ONLY from the trap loop thread.
        self._snmp_engine = None
        self._dispatcher  = None
        self._targets: dict[str, str] = {}   # community → pysnmp target-address name
        self._engine_lock: Optional[asyncio.Lock] = None
        self._engine_epoch = 0    # bumped by configure() to force a rebuild
        self._built_epoch  = -1

    # ── Configuration ─────────────────────────────────────────────────────────

    def configure(self, ip: str, port: int):
        self._receiver_ip   = ip
        self._receiver_port = port
        # Targets embed the receiver address, so the shared stack must be rebuilt.
        self._engine_epoch += 1
        # Persist here rather than in the REST handler: this is the one choke
        # point every caller goes through, so the desktop trap panel and the web
        # UI save the same way and neither can set a receiver that a restart
        # would then quietly drop.
        sim_settings.set_many({"trap_receiver_ip": ip, "trap_receiver_port": port})

    @property
    def receiver_ip(self) -> str:
        """Where traps are actually being sent — the restored or configured value."""
        return self._receiver_ip

    @property
    def receiver_port(self) -> int:
        return self._receiver_port

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
            # Close the shared transport on the loop thread that owns it, then
            # stop the loop. Ordering matters: call_soon_threadsafe callbacks run
            # in FIFO order, so the teardown lands before the loop halts.
            self._loop.call_soon_threadsafe(self._teardown_stack)
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop   = None
        self._thread = None
        self._engine_lock = None
        self._built_epoch = -1

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
                                          action.rule.rule_name,
                                          action.rule.severity),
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
        if "pdu_outlet_current" in action.extra:
            kwargs["outlet_current"] = action.extra["pdu_outlet_current"]
        if "pdu_outlet_instance" in action.extra:
            kwargs["outlet_label"] = action.extra["pdu_outlet_instance"]
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

    # ── Shared pysnmp stack ───────────────────────────────────────────────────

    def _teardown_stack(self):
        """Drop the shared engine/dispatcher. Trap loop thread only."""
        try:
            if self._dispatcher is not None:
                self._dispatcher.close_dispatcher()
            if self._snmp_engine is not None:
                self._snmp_engine.unregister_transport_dispatcher()
        except Exception:
            pass
        self._snmp_engine = None
        self._dispatcher  = None
        self._targets     = {}

    async def _ensure_target(self, community: str) -> str:
        """Return the pysnmp target-address name for `community`, building the
        shared engine/dispatcher/transport on first use.

        The trap source IP doubles as the v1 community (same convention as the
        poll side), so one target is registered per distinct source IP — bounded
        by device count, and far cheaper than a fresh SnmpEngine per trap.
        """
        from pysnmp.entity.engine import SnmpEngine
        from pysnmp.entity import config as snmp_config
        from pysnmp.carrier.asyncio.dispatch import AsyncioDispatcher
        from pysnmp.carrier.asyncio.dgram import udp as udp_mod

        if self._engine_lock is None:
            self._engine_lock = asyncio.Lock()

        async with self._engine_lock:
            if self._built_epoch != self._engine_epoch:
                self._teardown_stack()
                self._built_epoch = self._engine_epoch

            if self._snmp_engine is None:
                engine = SnmpEngine()
                dispatcher = AsyncioDispatcher(loop=asyncio.get_running_loop())
                engine.register_transport_dispatcher(dispatcher)
                snmp_config.add_transport(
                    engine, udp_mod.DOMAIN_NAME,
                    udp_mod.UdpAsyncioTransport().open_client_mode(),
                )
                self._snmp_engine = engine
                self._dispatcher  = dispatcher
                self._targets     = {}

            name = self._targets.get(community)
            if name is None:
                idx = len(self._targets)
                sec, params, addr = f'tc{idx}', f'tp{idx}', f'tt{idx}'
                snmp_config.add_v1_system(self._snmp_engine, sec, community)
                snmp_config.add_target_parameters(
                    self._snmp_engine, params, sec, 'noAuthNoPriv', 1,
                )
                snmp_config.add_target_address(
                    self._snmp_engine, addr, udp_mod.DOMAIN_NAME,
                    (self._receiver_ip, self._receiver_port),
                    params, tagList='trap-tag',
                    timeout=100, retryCount=0,
                )
                self._targets[community] = addr
                name = addr
            return name

    # ── Async send internals ──────────────────────────────────────────────────

    async def _send_async(self, device: Device, trap_type: TrapType, **kwargs):
        defn = TRAP_DEFINITIONS[trap_type]
        try:
            from pysnmp.entity.rfc3413 import ntforg
            from pysnmp.proto.api import v2c as proto_v2c
            from pysnmp.proto import rfc1902
            from pyasn1.type import univ

            # Community mirrors the firing agent's IP (server OS → prod IP,
            # BMC → mgmt IP) — same convention as the poll side.
            community = (_trap_source_ip(device, trap_type)
                         or device.snmp_community)
            target = await self._ensure_target(community)

            def _oid(s: str):
                return univ.ObjectIdentifier(tuple(int(x) for x in s.split('.')))

            # A trap's OID depends on WHO is sending it: the same over-current is
            # rPDUOverload (318.0.276) from an APC rPDU and
            # overCurrentProtectorSensorStateChange (13742.6.0.65) from a Raritan
            # PX. Resolve against the device's vendor, falling back to the
            # simulator's own tree when no vendor MIB was verified for it.
            send_oid = vendor_oids.trap_oid(
                trap_type, device.vendor, device.device_type, defn.oid)

            pdu = proto_v2c.SNMPv2TrapPDU()
            proto_v2c.apiPDU.set_defaults(pdu)
            all_varbinds = (
                [(_oid('1.3.6.1.2.1.1.3.0'), rfc1902.TimeTicks(_uptime_ticks(device))),
                 (_oid('1.3.6.1.6.3.1.1.4.1.0'), _oid(send_oid))]
                + self._build_extra_varbinds(device, trap_type, **kwargs)
            )
            proto_v2c.apiPDU.set_varbinds(pdu, all_varbinds)

            ntforg.NotificationOriginator().send_pdu(
                self._snmp_engine, target, None, b'', pdu,
            )

        except Exception as ex:
            self.trap_error.emit(
                f"Trap exception ({device.name} / {trap_type.value}): {ex}"
            )
            return

        if not kwargs.get("no_table"):
            # Guarded: a formatting error here must not swallow the trap record
            # nor escape into the loop as an unretrieved task exception.
            rule_name = kwargs.get("rule_name", "")
            try:
                details = self._format_details(device, trap_type, **kwargs)
            except Exception as ex:
                self.trap_error.emit(
                    f"Trap detail format failed ({device.name} / {trap_type.value}): {ex}"
                )
                details = ""
            self.trap_sent.emit(TrapEvent(device, trap_type, details, rule_name,
                                          iface_index=kwargs.get("iface_index")))

    async def _send_raw_trap_async(self, device: Device, oid: str, extra: dict,
                                   rule_name: str = "",
                                   severity: str = "informational"):
        """Send a trap for an OID that has no TrapType mapping."""
        try:
            from pysnmp.entity.rfc3413 import ntforg
            from pysnmp.proto.api import v2c as proto_v2c
            from pysnmp.proto import rfc1902
            from pyasn1.type import univ

            # Raw OIDs are rule-driven → always the OS/NOS agent, never BMC.
            community = _trap_source_ip(device, None) or device.snmp_community
            target = await self._ensure_target(community)

            def _oid(s: str):
                return univ.ObjectIdentifier(tuple(int(x) for x in s.split('.')))

            # Rule-driven traps arrive here as a bare OID. Map it back to its
            # TrapType so the vendor registry can rewrite it the same way the
            # typed path does — otherwise every rule would still emit 99999.
            mapped = OID_TO_TRAP_TYPE.get(oid)
            send_oid = oid
            if mapped is not None:
                send_oid = vendor_oids.trap_oid(
                    mapped, device.vendor, device.device_type, oid)

            pdu = proto_v2c.SNMPv2TrapPDU()
            proto_v2c.apiPDU.set_defaults(pdu)
            varbinds = [
                (_oid('1.3.6.1.2.1.1.3.0'), rfc1902.TimeTicks(_uptime_ticks(device))),
                (_oid('1.3.6.1.6.3.1.1.4.1.0'), _oid(send_oid)),
                (_oid('1.3.6.1.2.1.1.5.0'), rfc1902.OctetString(device.name)),
            ]
            proto_v2c.apiPDU.set_varbinds(pdu, varbinds)
            ntforg.NotificationOriginator().send_pdu(
                self._snmp_engine, target, None, b'', pdu,
            )
        except Exception as ex:
            self.trap_error.emit(f"Raw trap error ({device.name} / {oid}): {ex}")
            return

        # Record it the same way the typed path does, so the trap log shows the
        # clear as well as the alarm. Formatting the detail must not be able to
        # cost the record: build it defensively.
        detail_bits = ", ".join(f"{k}={v}" for k, v in (extra or {}).items()
                                if v is not None and k != "down_devices")
        self.trap_sent.emit(RawTrapEvent(device, oid, detail_bits, rule_name, severity))

    # ── Varbind builders ──────────────────────────────────────────────────────

    # ── Vendor varbind sets ───────────────────────────────────────────────────

    @staticmethod
    def _pet_record(sensor_type: int, event_type: int, offset: int,
                    severity: int, sensor_number: int = 1) -> bytes:
        """Minimal IPMI Platform Event Trap event record (PET spec v1.0 §2).

        Field offsets follow the spec so a PET decoder (ipmi-pet, most NMS BMC
        integrations) parses the fields the simulator actually models; the rest
        — GUID, manufacturer/system ID, OEM block — are zero-filled rather than
        invented, which decodes as "not supplied" instead of as wrong data.
        """
        rec = bytearray(47)
        rec[0:16] = b"\x00" * 16          # GUID (not modelled)
        rec[16:18] = b"\x00\x00"          # sequence number / cookie
        rec[18:22] = b"\x00" * 4          # local timestamp
        rec[22:24] = b"\x00\x00"          # UTC offset
        rec[24] = 0x00                    # trap source type: platform firmware
        rec[25] = 0x00                    # event source type
        rec[26] = severity & 0xFF
        rec[27] = 0x20                    # sensor device (BMC)
        rec[28] = sensor_number & 0xFF
        rec[29] = 0x00                    # entity
        rec[30] = 0x00                    # entity instance
        rec[31] = ((event_type & 0x7F))   # event dir | type
        rec[32] = offset & 0xFF
        rec[33] = sensor_type & 0xFF
        return bytes(rec)

    @staticmethod
    def _vendor_varbinds(device: Device, trap_type: TrapType, **kwargs):
        """Varbinds the real vendor MIB defines for this notification.

        Returns None when the device's vendor has no verified mapping, which
        leaves the synthetic varbind set in place (see core/vendor_oids.py for
        why unmapped gear is deliberately left alone).
        """
        from pyasn1.type import univ
        from pysnmp.proto import rfc1902

        dt = getattr(device.device_type, "value", device.device_type)
        if dt in vendor_oids.NON_SNMP_DEVICE_TYPES:
            return None
        key = vendor_oids.vendor_key(device.vendor)
        if not key:
            return None

        def _oid(s: str):
            return univ.ObjectIdentifier(tuple(int(x) for x in s.split('.')))

        def _s(oid, val):
            return (_oid(oid), rfc1902.OctetString(str(val)))

        def _i(oid, val):
            return (_oid(oid), rfc1902.Integer32(int(val)))

        def _g(oid, val):
            return (_oid(oid), rfc1902.Gauge32(max(0, int(val))))

        # Which receptacle this notification is about, when it is about one.
        # A metered-by-outlet strip names the outlet; a phase imbalance has none
        # to name, and inventing one would be worse than leaving it out.
        _outlet_name = str(kwargs.get("outlet_label", "") or "")
        _outlet_no = 0
        if _outlet_name:
            _digits = "".join(c for c in _outlet_name if c.isdigit())
            _outlet_no = int(_digits) if _digits else 0

        mv = kwargs.get("metric_value", None)
        _PDU_LOAD = (TrapType.PDU_LOAD_HIGH, TrapType.PDU_LOAD_CRITICAL,
                     TrapType.PDU_LOAD_NORMAL, TrapType.PDU_OUTLET_CURRENT_HIGH,
                     TrapType.PDU_BREAKER_TRIPPED)
        _PDU_ENV = (TrapType.PDU_TEMP_HIGH, TrapType.PDU_TEMP_NORMAL,
                    TrapType.PDU_HUMIDITY_HIGH, TrapType.PDU_HUMIDITY_NORMAL)
        _AMBIENT = (TrapType.SENSOR_AMBIENT_TEMP_HIGH,
                    TrapType.SENSOR_AMBIENT_TEMP_CRITICAL,
                    TrapType.SENSOR_AMBIENT_TEMP_NORMAL,
                    TrapType.SENSOR_MID_TEMP_HIGH, TrapType.SENSOR_MID_TEMP_NORMAL,
                    TrapType.SENSOR_OUTLET_TEMP_HIGH, TrapType.SENSOR_OUTLET_TEMP_NORMAL,
                    TrapType.TEMPERATURE_ALERT, TrapType.TEMPERATURE_NORMAL,
                    TrapType.CPU_TEMP_CRITICAL)
        _HUMID = (TrapType.SENSOR_HIGH_HUMIDITY, TrapType.SENSOR_CRITICAL_HUMIDITY,
                  TrapType.SENSOR_LOW_HUMIDITY, TrapType.SENSOR_HUMIDITY_NORMAL,
                  _HUMIDITY_ALERT)

        # ── APC / PowerNet-MIB ───────────────────────────────────────────────
        if key == "apc":
            if trap_type in _PDU_LOAD:
                # rPDULoadStatusLoad is tenths of an amp; rPDULoadStatusLoadState
                # is lowLoad(1)/normal(2)/nearOverload(3)/overload(4).
                amps10 = _num(kwargs.get("outlet_current",
                                         getattr(device, "pdu_outlet_current", 0)), scale=10)
                state = {TrapType.PDU_LOAD_HIGH: 3, TrapType.PDU_LOAD_CRITICAL: 4,
                         TrapType.PDU_OUTLET_CURRENT_HIGH: 4,
                         TrapType.PDU_BREAKER_TRIPPED: 4}.get(trap_type, 2)
                vbs = [
                    _s(APC["identName"], device.name),
                    _s(APC["identSerial"], f"SN-{device.name}"),
                    _g(APC["loadStatusLoad"], amps10),
                    _i(APC["loadStatusState"], state),
                    _s(APC["trapArgs"], f"{trap_type.value} on {device.name}"),
                ]
                # An over-current on a metered-by-outlet SKU belongs to a
                # receptacle, not to the strip. A whole-strip load condition
                # carries no outlet and must not pretend to.
                if _outlet_no:
                    vbs[4:4] = [_i(APC["rpdu2OutletNumber"], _outlet_no),
                                _s(APC["rpdu2OutletName"], _outlet_name)]
                return vbs
            if trap_type in _PDU_ENV or trap_type in _AMBIENT or trap_type in _HUMID:
                temp = _num(mv if trap_type in (_PDU_ENV[:2] + _AMBIENT) else
                            getattr(device, "pdu_temperature", 0))
                humid = _num(mv if trap_type in _HUMID else
                             getattr(device, "pdu_humidity", 0))
                return [
                    _s(APC["identName"], device.name),
                    _g(APC["probeTemp"], temp),
                    _g(APC["probeHumidity"], humid),
                    _s(APC["trapArgs"], f"{trap_type.value} on {device.name}"),
                ]
            if trap_type in (TrapType.PDU_OUTLET_ON, TrapType.PDU_OUTLET_OFF,
                             TrapType.PDU_OUTLET_FAILURE):
                # A switched-outlet notification is ABOUT one receptacle - that is
                # the entire content of the event - so the identity columns are not
                # optional here the way they are on a load condition.
                vbs = [_s(APC["identName"], device.name)]
                if _outlet_no:
                    vbs += [_i(APC["rpdu2OutletNumber"], _outlet_no),
                            _s(APC["rpdu2OutletName"], _outlet_name)]
                vbs += [
                    _i(APC["rpdu2OutletState"],
                       1 if trap_type == TrapType.PDU_OUTLET_ON else 2),
                    _s(APC["trapArgs"],
                       f"{trap_type.value} on {_outlet_name or device.name}"),
                ]
                return vbs
            return None

        # ── Raritan / PDU2-MIB ───────────────────────────────────────────────
        if key == "raritan":
            st = vendor_oids.RARITAN_SENSOR_TYPE
            ss = vendor_oids.RARITAN_SENSOR_STATE
            table = "external"
            sensor, state, value = st["temperature"], ss["normal"], 0
            if trap_type in _PDU_LOAD:
                table = "inlet" if trap_type != TrapType.PDU_OUTLET_CURRENT_HIGH else "outlet"
                sensor = st["current"]
                value = _num(kwargs.get("outlet_current",
                                        getattr(device, "pdu_outlet_current", 0)), scale=10)
                state = {TrapType.PDU_LOAD_HIGH: ss["aboveUpperWarning"],
                         TrapType.PDU_LOAD_CRITICAL: ss["aboveUpperCritical"],
                         TrapType.PDU_OUTLET_CURRENT_HIGH: ss["aboveUpperCritical"],
                         TrapType.PDU_BREAKER_TRIPPED: ss["open"],
                         TrapType.PDU_LOAD_NORMAL: ss["normal"]}.get(trap_type, ss["normal"])
                if trap_type == TrapType.PDU_BREAKER_TRIPPED:
                    table, sensor = "unit", st["trip"]
            elif trap_type in (TrapType.PDU_VOLTAGE_HIGH, TrapType.PDU_VOLTAGE_LOW):
                table, sensor = "inlet", st["voltage"]
                value = _num(mv if mv is not None else getattr(device, "pdu_voltage", 0), scale=10)
                state = (ss["aboveUpperCritical"] if trap_type == TrapType.PDU_VOLTAGE_HIGH
                         else ss["belowLowerCritical"])
            elif trap_type in (TrapType.PDU_FREQUENCY_FAULT, TrapType.PDU_FREQUENCY_NORMAL):
                table, sensor = "inlet", st["frequency"]
                value = _num(mv if mv is not None else getattr(device, "pdu_frequency", 0), scale=10)
                state = (ss["normal"] if trap_type == TrapType.PDU_FREQUENCY_NORMAL
                         else ss["aboveUpperWarning"])
            elif trap_type in (TrapType.PDU_OUTLET_ON, TrapType.PDU_OUTLET_OFF):
                table, sensor = "outlet", st["onOff"]
                state = ss["on"] if trap_type == TrapType.PDU_OUTLET_ON else ss["off"]
            elif trap_type == TrapType.PDU_SMOKE_DETECTED:
                sensor, state = st["smokeDetection"], ss["alarmed"]
            elif trap_type == TrapType.PDU_GROUND_FAULT:
                table, sensor, state = "unit", st["residualCurrent"], ss["alarmed"]
            elif trap_type in _HUMID or trap_type in (TrapType.PDU_HUMIDITY_HIGH,
                                                      TrapType.PDU_HUMIDITY_NORMAL):
                sensor = st["humidity"]
                value = _num(mv if mv is not None else getattr(device, "humidity", 0), scale=10)
                state = (ss["normal"] if trap_type in (TrapType.SENSOR_HUMIDITY_NORMAL,
                                                       TrapType.PDU_HUMIDITY_NORMAL)
                         else ss["aboveUpperWarning"])
            elif trap_type in (TrapType.SENSOR_HIGH_AIRFLOW, TrapType.SENSOR_LOW_AIRFLOW,
                               TrapType.SENSOR_AIRFLOW_NORMAL, _AIRFLOW_ALERT):
                sensor = st["airFlow"]
                value = _num(mv if mv is not None else getattr(device, "airflow", 0), scale=10)
                state = (ss["belowLowerWarning"] if trap_type == TrapType.SENSOR_LOW_AIRFLOW
                         else ss["normal"] if trap_type == TrapType.SENSOR_AIRFLOW_NORMAL
                         else ss["aboveUpperWarning"])
            else:
                value = _num(mv if mv is not None else getattr(device, "inlet_temp", 0), scale=10)
                state = (ss["normal"] if trap_type in (TrapType.SENSOR_AMBIENT_TEMP_NORMAL,
                                                       TrapType.PDU_TEMP_NORMAL,
                                                       TrapType.SENSOR_MID_TEMP_NORMAL,
                                                       TrapType.SENSOR_OUTLET_TEMP_NORMAL)
                         else ss["aboveUpperCritical"]
                         if trap_type == TrapType.SENSOR_AMBIENT_TEMP_CRITICAL
                         else ss["aboveUpperWarning"])
            # State sensors (trip, on/off, smoke) have no numeric reading —
            # PDU2-MIB says the value field does not apply to them.
            if sensor in (st["trip"], st["onOff"], st["smokeDetection"]):
                value = 0
            return [
                _s(RARITAN["pduName"], device.name),
                _s(RARITAN["pduSerial"], f"SN-{device.name}"),
                _i(RARITAN["typeOfSensor"], sensor),
                _g(RARITAN[f"{table}Value"], value),
                _i(RARITAN[f"{table}State"], state),
                _i(RARITAN["oldSensorState"], ss["normal"]),
            ]

        # ── Liebert / Vertiv ─────────────────────────────────────────────────
        if key == "liebert":
            defn = TRAP_DEFINITIONS.get(trap_type)
            descr = defn.display_name if defn else trap_type.value
            if mv is not None:
                descr = f"{descr} ({_fmt(mv)})"
            return [
                _s(LIEBERT["conditionDescr"], f"{device.name}: {descr}"),
                _s(LIEBERT["conditionTime"], ""),
            ]

        # ── Cisco ────────────────────────────────────────────────────────────
        if key == "cisco":
            if trap_type in (TrapType.CPU_HIGH, TrapType.CPU_SUSTAINED, TrapType.CPU_NORMAL):
                return [
                    _g(CISCO["cpu5min"], _num(mv if mv is not None
                                              else getattr(device, "cpu_usage", 0))),
                    _g(CISCO["cpuRisingThresh"], 90),
                ]
            if trap_type in _AMBIENT:
                state = vendor_oids.CISCO_ENV_STATE[
                    "normal" if trap_type == TrapType.TEMPERATURE_NORMAL
                    else "critical" if trap_type == TrapType.CPU_TEMP_CRITICAL
                    else "warning"]
                return [
                    _s(CISCO["envTempDescr"], f"{device.name} inlet"),
                    _g(CISCO["envTempValue"], _num(mv if mv is not None
                                                   else getattr(device, "cpu_temp", 0))),
                    _i(CISCO["envTempState"], state),
                ]
            return None

        # ── Dell iDRAC ───────────────────────────────────────────────────────
        if key == "dell":
            if trap_type in _AMBIENT or trap_type in (TrapType.SERVER_POWER_OFF,
                                                      TrapType.SERVER_POWER_ON):
                if trap_type in (TrapType.SERVER_POWER_OFF, TrapType.SERVER_POWER_ON):
                    msg_id = "SYS1003" if trap_type == TrapType.SERVER_POWER_ON else "SYS1000"
                    msg = ("The system has been powered on."
                           if trap_type == TrapType.SERVER_POWER_ON
                           else "The system has been powered off.")
                    status = vendor_oids.DELL_STATUS["ok"]
                else:
                    msg_id = "TMP0118"
                    msg = f"Temperature sensor reading {_fmt(mv)} C"
                    status = vendor_oids.DELL_STATUS[
                        "ok" if trap_type == TrapType.TEMPERATURE_NORMAL
                        else "critical" if trap_type == TrapType.CPU_TEMP_CRITICAL
                        else "warning"]
                return [
                    _s(DELL["alertMessageID"], msg_id),
                    _s(DELL["alertMessage"], msg),
                    _i(DELL["alertCurrentStatus"], status),
                    _s(DELL["alertServiceTag"], f"SVC{abs(hash(device.name)) % 10**7:07d}"),
                ]
            return None

        # ── HPE iLO / Insight ────────────────────────────────────────────────
        if key == "hpe":
            if trap_type in _AMBIENT:
                # cpqHeThermalTempStatus: other(1) ok(2) degraded(3) failed(4)
                status = (2 if trap_type == TrapType.TEMPERATURE_NORMAL
                          else 4 if trap_type == TrapType.CPU_TEMP_CRITICAL else 3)
                return [
                    _i(HPE["thermalTempStatus"], status),
                    _i(HPE["thermalDegradedAct"], 2),   # continue(2)
                ]
            return None

        # ── Lenovo XCC ───────────────────────────────────────────────────────
        if key == "lenovo":
            if trap_type in _AMBIENT or trap_type in (TrapType.SERVER_POWER_OFF,
                                                      TrapType.SERVER_POWER_ON):
                defn = TRAP_DEFINITIONS.get(trap_type)
                text = defn.display_name if defn else trap_type.value
                if mv is not None:
                    text = f"{text} ({_fmt(mv)})"
                return [
                    _s(LENOVO["spTxtId"], f"{device.name}: {text}"),
                    _s(LENOVO["sysSern"], f"SN-{device.name}"),
                ]
            return None

        # ── Supermicro / IBM BMCs → IPMI PET ─────────────────────────────────
        if key in ("supermicro", "ibm"):
            if trap_type in (TrapType.SERVER_POWER_OFF, TrapType.SERVER_POWER_ON):
                rec = TrapEngine._pet_record(
                    sensor_type=0x09, event_type=0x6F,
                    offset=0 if trap_type == TrapType.SERVER_POWER_OFF else 1,
                    severity=0x02)          # information
            elif trap_type in _AMBIENT:
                rec = TrapEngine._pet_record(
                    sensor_type=0x01, event_type=0x01, offset=0x01,
                    severity=0x10 if trap_type == TrapType.CPU_TEMP_CRITICAL
                    else 0x04 if trap_type == TrapType.TEMPERATURE_NORMAL else 0x08)
            else:
                return None
            return [(_oid(PET["eventData"]), rfc1902.OctetString(rec))]

        return None

    @staticmethod
    def _build_extra_varbinds(device: Device, trap_type: TrapType, **kwargs):
        from pyasn1.type import univ
        from pysnmp.proto import rfc1902

        def _oid(s: str):
            return univ.ObjectIdentifier(tuple(int(x) for x in s.split('.')))

        # A vendor trap OID with simulator-private varbinds hanging off it is
        # still undecodable: PowerNet's rPDUOverload is defined to carry
        # rPDUIdentName/rPDULoadStatusLoad, so those are what an APC-aware NMS
        # reads. When the registry has a varbind set for this vendor+trap, it
        # replaces the synthetic one below wholesale.
        vendor_vbs = TrapEngine._vendor_varbinds(device, trap_type, **kwargs)
        if vendor_vbs is not None:
            return vendor_vbs

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
            val = _num(kwargs.get("metric_value", getattr(device, "cpu_usage", 0)))
            return [
                (_oid('1.3.6.1.4.1.99999.2.1'), rfc1902.Gauge32(val)),
                (_oid('1.3.6.1.4.1.99999.2.5'), rfc1902.Gauge32(90)),
            ]

        if trap_type in (TrapType.MEMORY_HIGH, TrapType.MEMORY_NORMAL):
            val = _num(kwargs.get("metric_value",
                                  getattr(device, "memory_used", 0) * 100
                                  // max(1, getattr(device, "memory_total", 0) or 1)))
            return [
                (_oid('1.3.6.1.4.1.99999.2.2'), rfc1902.Gauge32(val)),
                (_oid('1.3.6.1.4.1.99999.2.6'), rfc1902.Gauge32(85)),
            ]

        if trap_type in (TrapType.TEMPERATURE_ALERT, TrapType.TEMPERATURE_NORMAL,
                         TrapType.SENSOR_AMBIENT_TEMP_HIGH,
                         TrapType.SENSOR_AMBIENT_TEMP_CRITICAL,
                         TrapType.SENSOR_AMBIENT_TEMP_NORMAL):
            temp = _num(kwargs.get("metric_value", getattr(device, "cpu_temp", 0)
                                   or random.randint(90, 95)))
            return [
                (_oid('1.3.6.1.4.1.99999.2.3'), rfc1902.Gauge32(max(0, temp))),
                (_oid('1.3.6.1.4.1.99999.2.7'), rfc1902.Gauge32(90)),
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

        if trap_type in (TrapType.BGP_DOWN, TrapType.BGP_UP):
            peer = kwargs.get("peer_addr", "10.0.0.1")
            state = kwargs.get("bgp_state",
                               "established" if trap_type == TrapType.BGP_UP else "idle")
            state_code = {"idle": 1, "connect": 2, "active": 3,
                          "opensent": 4, "openconfirm": 5, "established": 6}.get(state, 1)
            return [
                (_oid('1.3.6.1.2.1.15.3.1.7.0'),  rfc1902.OctetString(peer)),
                (_oid('1.3.6.1.2.1.15.3.1.14.0'), rfc1902.Integer32(state_code)),
            ]

        if trap_type in (_HUMIDITY_ALERT, TrapType.SENSOR_HIGH_HUMIDITY,
                         TrapType.SENSOR_CRITICAL_HUMIDITY, TrapType.SENSOR_LOW_HUMIDITY,
                         TrapType.SENSOR_HUMIDITY_NORMAL):
            val = _num(kwargs.get("metric_value", None) or getattr(device, "humidity", 0))
            return [
                (_oid('1.3.6.1.4.1.99999.2.12'), rfc1902.Gauge32(max(0, val))),   # humidity %
                (_oid('1.3.6.1.4.1.99999.2.15'), rfc1902.Gauge32(70)),             # threshold %
            ]

        if trap_type in (_DEWPOINT_ALERT, TrapType.SENSOR_DEWPOINT_NORMAL):
            # Encode as ×10 integer (e.g. 21.5°C → 215)
            val = _num(kwargs.get("metric_value", None) or getattr(device, "dewpoint", 0), scale=10)
            return [
                (_oid('1.3.6.1.4.1.99999.2.13'), rfc1902.Integer32(val)),   # dewpoint ×10 °C
                (_oid('1.3.6.1.4.1.99999.2.16'), rfc1902.Integer32(210)),   # threshold 21.0°C ×10
            ]

        if trap_type in (_AIRFLOW_ALERT, TrapType.SENSOR_HIGH_AIRFLOW,
                         TrapType.SENSOR_LOW_AIRFLOW, TrapType.SENSOR_AIRFLOW_NORMAL):
            # Encode as ×10 integer (e.g. 3.5 m/s → 35)
            val = _num(kwargs.get("metric_value", None) or getattr(device, "airflow", 0), scale=10)
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
            return f"Temperature {kwargs.get('metric_value', '—')}°C  (threshold 90°C)"
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
        if trap_type == TrapType.BGP_UP:
            return f"Peer {kwargs.get('peer_addr', '?')} → established"
        if trap_type == TrapType.AUTH_FAILURE:
            return "Incorrect community string"
        if trap_type in (TrapType.COLD_START, TrapType.WARM_START):
            return "Device restarted"
        if trap_type == _HUMIDITY_ALERT:
            return f"Humidity {_fmt(kwargs.get('metric_value', device.humidity))}%  (threshold 70%)"
        if trap_type == _DEWPOINT_ALERT:
            return f"Dew point {_fmt(kwargs.get('metric_value', device.dewpoint))}°C  (threshold 21°C)"
        if trap_type == _AIRFLOW_ALERT:
            return f"Airflow {_fmt(kwargs.get('metric_value', device.airflow), 2)} m/s  (range 0.3–3.5 m/s)"
        # PDU load / power
        if trap_type in (TrapType.PDU_LOAD_HIGH, TrapType.PDU_LOAD_CRITICAL):
            return f"PDU load {_fmt(kwargs.get('metric_value'))}%"
        if trap_type in (TrapType.PDU_VOLTAGE_HIGH, TrapType.PDU_VOLTAGE_LOW):
            return f"Input voltage {_fmt(kwargs.get('metric_value'))} V"
        if trap_type == TrapType.PDU_PHASE_IMBALANCE:
            return f"Phase imbalance {_fmt(kwargs.get('metric_value'))}%"
        if trap_type == TrapType.PDU_POWER_FACTOR_LOW:
            return f"Power factor {_fmt(kwargs.get('metric_value'), 2)}"
        if trap_type == TrapType.PDU_OUTLET_CURRENT_HIGH:
            return f"Outlet current {_fmt(kwargs.get('metric_value'))} A"
        if trap_type == TrapType.PDU_FREQUENCY_FAULT:
            return f"Frequency {_fmt(kwargs.get('metric_value'), 2)} Hz  (normal 49.5–50.5 Hz)"
        if trap_type == TrapType.PDU_TEMP_HIGH:
            return f"PDU temp {_fmt(kwargs.get('metric_value'))}°C  (threshold 35°C)"
        if trap_type == TrapType.PDU_HUMIDITY_HIGH:
            return f"PDU humidity {_fmt(kwargs.get('metric_value'))}%  (threshold 70%)"
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
            return f"Battery health {_fmt(kwargs.get('metric_value'))}%  (threshold 50%)"
        if trap_type == TrapType.UPS_BYPASS_ACTIVE:
            return "UPS switched to bypass mode"
        if trap_type == TrapType.UPS_BYPASS_CLEARED:
            return "UPS exited bypass mode"
        if trap_type == TrapType.UPS_BATTERY_HEALTH_RESTORED:
            return f"Battery health recovered to {_fmt(kwargs.get('metric_value'))}%"
        if trap_type in (TrapType.UPS_INPUT_VOLTAGE_HIGH, TrapType.UPS_INPUT_VOLTAGE_LOW,
                         TrapType.UPS_INPUT_VOLTAGE_NORMAL,
                         TrapType.UPS_INPUT_VOLTAGE_LOW_CLEARED):
            val = _fmt(kwargs.get("metric_value"))
            if trap_type == TrapType.UPS_INPUT_VOLTAGE_HIGH:
                return f"Input voltage {val} V L-L  (threshold 440 V)"
            if trap_type == TrapType.UPS_INPUT_VOLTAGE_LOW:
                return f"Input voltage {val} V L-L  (threshold 360 V)"
            # Each clear states the reset point it crossed. Saying "back in band"
            # would be wrong when an over-voltage clears straight into a sag —
            # the reading can be under the low threshold on the very same tick.
            if trap_type == TrapType.UPS_INPUT_VOLTAGE_NORMAL:
                return f"Input voltage {val} V L-L  (over-voltage reset 430 V)"
            return f"Input voltage {val} V L-L  (under-voltage reset 370 V)"
        # Sensor mid/outlet temp
        if trap_type == TrapType.SENSOR_MID_TEMP_HIGH:
            return f"Mid-rack temp {_fmt(kwargs.get('metric_value'))}°C  (threshold 38°C)"
        if trap_type == TrapType.SENSOR_OUTLET_TEMP_HIGH:
            return f"Exhaust temp {_fmt(kwargs.get('metric_value'))}°C  (threshold 45°C)"
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
            val = kwargs.get("metric_value", None) or getattr(device, "cpu_temp", None)
            return f"Temperature {_fmt(val)}°C  (recovered <85°C)"
        # Sensor ambient temp variants + recovery
        if trap_type == TrapType.SENSOR_AMBIENT_TEMP_HIGH:
            val = kwargs.get("metric_value", None) or getattr(device, "inlet_temp", None)
            return f"Ambient temp {_fmt(val)}°C  (threshold 32°C)"
        if trap_type == TrapType.SENSOR_AMBIENT_TEMP_CRITICAL:
            val = kwargs.get("metric_value", None) or getattr(device, "inlet_temp", None)
            return f"Ambient temp {_fmt(val)}°C  (threshold 38°C)"
        if trap_type == TrapType.SENSOR_AMBIENT_TEMP_NORMAL:
            val = kwargs.get("metric_value", None) or getattr(device, "inlet_temp", None)
            return f"Ambient temp {_fmt(val)}°C  (recovered <28°C)"
        # Sensor humidity variants + recovery
        if trap_type == TrapType.SENSOR_HIGH_HUMIDITY:
            val = kwargs.get("metric_value", None) or getattr(device, "humidity", None)
            return f"Humidity {_fmt(val)}%  (threshold 70%)"
        if trap_type == TrapType.SENSOR_CRITICAL_HUMIDITY:
            val = kwargs.get("metric_value", None) or getattr(device, "humidity", None)
            return f"Humidity {_fmt(val)}%  (threshold 80%)"
        if trap_type == TrapType.SENSOR_LOW_HUMIDITY:
            val = kwargs.get("metric_value", None) or getattr(device, "humidity", None)
            return f"Humidity {_fmt(val)}%  (threshold 30%)"
        if trap_type == TrapType.SENSOR_HUMIDITY_NORMAL:
            val = kwargs.get("metric_value", None) or getattr(device, "humidity", None)
            return f"Humidity {_fmt(val)}%  (recovered 30–70%)"
        # Sensor airflow variants + recovery
        if trap_type == TrapType.SENSOR_HIGH_AIRFLOW:
            val = kwargs.get("metric_value", None) or getattr(device, "airflow", None)
            return f"Airflow {_fmt(val, 2)} m/s  (threshold 3.5 m/s)"
        if trap_type == TrapType.SENSOR_LOW_AIRFLOW:
            val = kwargs.get("metric_value", None) or getattr(device, "airflow", None)
            return f"Airflow {_fmt(val, 2)} m/s  (threshold 0.3 m/s)"
        if trap_type == TrapType.SENSOR_AIRFLOW_NORMAL:
            val = kwargs.get("metric_value", None) or getattr(device, "airflow", None)
            return f"Airflow {_fmt(val, 2)} m/s  (recovered 0.3–3.5 m/s)"
        if trap_type == TrapType.SENSOR_DEWPOINT_NORMAL:
            val = kwargs.get("metric_value", None) or getattr(device, "dewpoint", None)
            return f"Dew point {_fmt(val)}°C  (recovered <17°C)"
        # PDU load recovery
        if trap_type == TrapType.PDU_LOAD_NORMAL:
            mv = kwargs.get("metric_value", None)
            return (f"PDU load {_fmt(mv)}%  (recovered <70%)" if mv is not None
                    else "PDU load recovered (<70%)")
        # Fallback: use trap definition description
        defn = TRAP_DEFINITIONS.get(trap_type)
        return defn.description if defn else ""