"""
Headless Trap Engine — Qt-free replacement for core/trap_engine.py.

Receives TrapAction callbacks from RuleEngine and:
1. Forwards the alert to DCIMBridge (HTTP POST to DCIM Server)
2. Sends an SNMPv2c UDP trap to the configured receiver
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from typing import Callable, Optional, TYPE_CHECKING

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from core.rule_engine import TrapAction
    from core.device_manager import Device, DeviceManager


class HeadlessTrapEngine:
    """
    Drop-in replacement for TrapEngine that works without PySide6.
    Uses a background asyncio loop for non-blocking UDP trap sending.
    """

    def __init__(self):
        self._receiver_ip   = "127.0.0.1"
        self._receiver_port = 162
        self._dm: Optional["DeviceManager"] = None

        # Callbacks replacing Qt signals
        self._on_trap_sent_cb:  Optional[Callable] = None
        self._on_trap_error_cb: Optional[Callable[[str], None]] = None
        self._on_link_change_cb: Optional[Callable] = None

        # Bridge callback to forward alerts via HTTP
        self._alert_cb: Optional[Callable] = None

        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._started = False

    # ── Configuration ──────────────────────────────────────────────────────

    def set_receiver(self, ip: str, port: int = 162):
        self._receiver_ip   = ip
        self._receiver_port = port

    def set_device_manager(self, dm: "DeviceManager"):
        self._dm = dm

    def set_alert_callback(self, cb: Callable):
        """cb(device, trap_type_str, severity, message) called on every fired rule."""
        self._alert_cb = cb

    def set_trap_sent_callback(self, cb: Callable):
        self._on_trap_sent_cb = cb

    def set_trap_error_callback(self, cb: Callable[[str], None]):
        self._on_trap_error_cb = cb

    def set_link_change_callback(self, cb: Callable):
        self._on_link_change_cb = cb

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        if self._started:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="TrapEngine-loop"
        )
        self._thread.start()
        self._started = True
        log.info("HeadlessTrapEngine started (receiver=%s:%d)",
                 self._receiver_ip, self._receiver_port)

    def stop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=3)
        self._started = False

    # ── Rule engine integration ────────────────────────────────────────────

    def on_rule_action(self, action: "TrapAction", device: "Device"):
        """Called by the rule engine callback on every fired rule."""
        rule      = action.rule
        trap_oid  = getattr(rule, "trap_oid",  "")
        severity  = getattr(rule, "severity",  "major")
        rule_name = getattr(rule, "rule_name", "")
        extra     = action.extra or {}

        # Forward to DCIM bridge
        if self._alert_cb:
            try:
                self._alert_cb(
                    device,
                    trap_oid,
                    severity,
                    extra.get("details", extra.get("message", "")),
                    rule_name,
                    extra.get("iface_index", None),
                )
            except Exception as e:
                log.warning("alert_cb error: %s", e)

        # Handle link state for logging
        if self._on_link_change_cb:
            trap_type_str = rule_name
            if "LINK_DOWN" in trap_type_str.upper():
                try:
                    self._on_link_change_cb(device,
                                            getattr(action, "iface_index", 0),
                                            False)
                except Exception:
                    pass
            elif "LINK_UP" in trap_type_str.upper():
                try:
                    self._on_link_change_cb(device,
                                            getattr(action, "iface_index", 0),
                                            True)
                except Exception:
                    pass

        # Send SNMP trap asynchronously
        if self._loop and self._started:
            asyncio.run_coroutine_threadsafe(
                self._send_snmp_trap(device, action),
                self._loop
            )

    # ── SNMP trap sending ──────────────────────────────────────────────────

    async def _send_snmp_trap(self, device: "Device", action: "TrapAction"):
        try:
            from pysnmp.hlapi.asyncio import (
                SnmpEngine, CommunityData, UdpTransportTarget,
                ContextData, ObjectType, ObjectIdentity,
                sendNotification
            )
            from pysnmp.proto.rfc1902 import Integer, OctetString, TimeTicks

            trap_oid = getattr(action.rule, "trap_oid",
                               "1.3.6.1.6.3.1.1.5.1")

            engine    = SnmpEngine()
            community = CommunityData("public", mpModel=1)
            transport = await UdpTransportTarget.create(
                (self._receiver_ip, self._receiver_port)
            )
            uptime_cs = int(getattr(device, "sys_uptime", 0))

            var_binds = [
                ObjectType(ObjectIdentity("1.3.6.1.2.1.1.3.0"),
                           TimeTicks(uptime_cs)),
                ObjectType(ObjectIdentity("1.3.6.1.6.3.1.1.4.1.0"),
                           ObjectIdentity(trap_oid)),
                ObjectType(ObjectIdentity("1.3.6.1.2.1.1.5.0"),
                           OctetString(device.name)),
            ]

            error_indication, _, _, _ = await sendNotification(
                engine, community, transport,
                ContextData(),
                "trap",
                var_binds
            )
            if error_indication:
                log.debug("SNMP trap error: %s", error_indication)
                if self._on_trap_error_cb:
                    self._on_trap_error_cb(str(error_indication))
            else:
                log.debug("Trap sent: %s → %s:%d",
                          trap_oid, self._receiver_ip, self._receiver_port)
                if self._on_trap_sent_cb:
                    self._on_trap_sent_cb(device, str(action.trap_type))
        except Exception as e:
            log.debug("Trap send exception: %s", e)
