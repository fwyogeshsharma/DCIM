"""
BACnet/IP Controller — lifecycle manager for all EV2 device simulations.

Mirrors the GNMIController / SNMPSimController interface so the UI and
DeviceStateStore treat all three protocols uniformly.

Architecture
-----------
• One shared recv socket bound to 0.0.0.0:47808 (receives all traffic:
  broadcasts and unicast to any device IP on this machine).
• Per-device send sockets bound to device_ip:47808 (SO_REUSEADDR) so
  responses arrive from the correct source IP — which is how BACnet
  discovery tools (YABE, Niagara, etc.) identify devices.
• One background recv thread calls recvfrom() in a loop and dispatches
  to the appropriate EV2BACnetDevice handler.
• Who-Is (broadcast) → all devices send I-Am from their own IP.
• ReadProperty / ReadPropertyMultiple (unicast) → routed by device
  instance number encoded in the BACnet PDU itself.
• SubscribeCOV → acked by device; notifications sent after each tick.

Telemetry
---------
BACnetController.tick(dt) is called by DeviceStateStore._tick().
Each EV2TelemetryEngine advances by dt seconds and the updated values
are applied to the device's BACnet object tree.  COV subscriptions are
then checked and notifications dispatched.
"""
from __future__ import annotations

import logging
import select
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.bacnet_object_model import (
    BACNET_PORT, OBJ_DEVICE,
    BVLL_TYPE, BVLC_ORIGINAL_UNICAST, BVLC_ORIGINAL_BROADCAST,
    BVLC_FORWARDED_NPDU,
    parse_bvll, parse_npdu, parse_apdu,
    SVC_WHO_IS, SVC_READ_PROPERTY, SVC_READ_PROPERTY_MULTIPLE,
    SVC_SUBSCRIBE_COV,
    decode_whois,
)
from core.bacnet_ev2_generator import DEFAULT_CIRCUITS
from core.bacnet_telemetry import EV2TelemetryEngine
from core.bacnet_plant_generator import build_plant_object_tree, PlantTelemetryEngine
from simulator.bacnet_device import EV2BACnetDevice

log = logging.getLogger(__name__)


class BACnetController:
    """
    Start, stop, and tick all simulated Verdigris EV2 BACnet/IP devices.

    Usage::

        ctrl = BACnetController()
        ctrl.set_log_callback(lambda msg, lvl: console.log_bacnet(msg, lvl))
        ctrl.set_ready_callback(lambda: panel.on_bacnet_ready())
        ctrl.start(
            device_ips=["10.1.0.10", "10.1.0.11"],
            base_instance=40001,
            circuits=42,
            frequency_hz=50.0,
        )
        # DeviceStateStore calls every tick:
        ctrl.tick(30.0)
        ...
        ctrl.stop()
    """

    def __init__(self, datasets_dir: str = "datasets/bacnet"):
        self._datasets_dir = str(Path(datasets_dir).resolve())
        Path(self._datasets_dir).mkdir(parents=True, exist_ok=True)

        self._log_cb:    Optional[Callable[[str, str], None]] = None
        self._ready_cb:  Optional[Callable[[], None]]         = None

        # Devices: keyed by device_instance (int) for quick PDU routing
        self._devices:     Dict[int, EV2BACnetDevice]      = {}
        # Also keyed by IP for Who-Is broadcast
        self._devices_by_ip: Dict[str, EV2BACnetDevice]    = {}

        # Per-device telemetry engines
        self._telemetry: Dict[int, EV2TelemetryEngine]     = {}

        # Shared receive socket (0.0.0.0:47808)
        self._recv_sock: Optional[socket.socket] = None

        # Background receive thread
        self._recv_thread: Optional[threading.Thread] = None
        self._stop_ev = threading.Event()

        self._running = False

        # Config snapshot — set by start(), read by status endpoint
        self._base_instance: int   = 40001
        self._frequency_hz:  float = 50.0
        self._port:          int   = 47808

    # ─────────────────────────────────────────────────────────────
    #  Callbacks
    # ─────────────────────────────────────────────────────────────

    def set_log_callback(self, cb: Callable[[str, str], None]):
        """cb(message, level)  level ∈ {"info","success","warning","error"}"""
        self._log_cb = cb

    def set_ready_callback(self, cb: Callable[[], None]):
        """Called once the recv socket is listening and at least one device ready."""
        self._ready_cb = cb

    # ─────────────────────────────────────────────────────────────
    #  Lifecycle
    # ─────────────────────────────────────────────────────────────

    def start(
        self,
        device_ips:    List[str],
        base_instance: int   = 40001,
        circuits_map:  Optional[Dict[str, int]] = None,
        frequency_hz:  float = 50.0,
        port:          int   = 47808,
        rated_kw_map:  Optional[Dict[str, float]] = None,
        plant_devices: Optional[List[dict]] = None,
    ) -> None:
        """
        Bind sockets and start the recv thread for all device IPs.

        device_ips:    list of already-bound IPs (e.g. from IPBindWorker)
        base_instance: first BACnet device instance number (increments by 1)
        circuits_map:  ip → (total_circuits, active_circuits) or int for legacy.
                       total = EV2 capacity; active = downstream breakers in use.
                       Circuits beyond active output zero (spare/unused breakers).
        frequency_hz:  mains frequency for telemetry engine
        port:          UDP port to bind (default 47808 = 0xBAC0)
        """
        if self._running:
            self._log("[BACnet] Already running.", "warning")
            return
        if not device_ips:
            self._log("[BACnet] No device IPs provided.", "error")
            return

        _cmap = circuits_map or {}
        _kwmap = rated_kw_map or {}

        # Store config for status endpoint
        self._base_instance = base_instance
        self._frequency_hz  = frequency_hz
        self._port          = port

        self._devices.clear()
        self._devices_by_ip.clear()
        self._telemetry.clear()

        self._port = port   # remember for log messages and device socket creation

        # Open shared wildcard recv socket FIRST so that per-device sockets
        # (device_ip:port) can join the same port with SO_REUSEADDR.  On
        # Windows the wildcard binding must pre-exist before specific-IP
        # bindings are added to the same port.
        try:
            recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            recv_sock.settimeout(1.0)
            recv_sock.bind(("", port))
            self._recv_sock = recv_sock
        except OSError as exc:
            self._log(f"[BACnet] Cannot open recv socket on port {port}: {exc}",
                      "error")
            return

        # Build devices (each device binds device_ip:BACNET_PORT with SO_REUSEADDR)
        for i, ip in enumerate(device_ips):
            instance    = base_instance + i
            device_name = f"Verdigris_EV2_{instance}"
            _entry = _cmap.get(ip, DEFAULT_CIRCUITS)
            if isinstance(_entry, tuple):
                capacity, active_circuits = _entry
            else:
                capacity = active_circuits = _entry  # legacy: all circuits active

            # Capacity = EV2 model size (e.g. EV2-240). The BACnet object tree and
            # per-circuit displays are capped at a real panel's breaker count
            # (NOMINAL_CIRCUITS), but the *electrical* size drives load_scale so a
            # large facility/main meter reports proportionally higher kW than the
            # downstream IT sub-meters — making PUE = facility / IT come out > 1.
            NOMINAL_CIRCUITS = 42
            circuits         = min(capacity, NOMINAL_CIRCUITS)
            active_circuits  = min(active_circuits, circuits)
            load_scale       = capacity / float(NOMINAL_CIRCUITS)
            dev = EV2BACnetDevice(
                device_ip=ip,
                device_instance=instance,
                device_name=device_name,
                circuits=circuits,
                log_cb=self._log_cb,
                port=port,
            )
            self._devices[instance]  = dev
            self._devices_by_ip[ip]  = dev
            self._telemetry[instance] = EV2TelemetryEngine(
                circuits=circuits,
                frequency_hz=frequency_hz,
                active_circuits=active_circuits,
                load_scale=load_scale,
                rated_kw=_kwmap.get(ip),
            )

        # ── Chiller-plant BACnet devices (chiller/pump/cooling_tower/valve) ──
        # Instances continue after the EV2 block. Each gets its type-specific
        # object tree + a live PlantTelemetryEngine.
        next_instance = base_instance + len(device_ips)
        for spec in (plant_devices or []):
            ip    = spec["ip"]
            dtype = spec["device_type"]
            name  = spec.get("name") or f"{dtype}_{next_instance}"
            rkw   = float(spec.get("rated_kw", 0.0) or 0.0)
            try:
                tree, n2k = build_plant_object_tree(dtype, rkw)
            except KeyError:
                self._log(f"[BACnet] Unknown plant device_type '{dtype}' — skipped.", "warning")
                continue
            dev = EV2BACnetDevice(
                device_ip=ip,
                device_instance=next_instance,
                device_name=name,
                log_cb=self._log_cb,
                port=port,
                object_tree=tree,
                name_to_key=n2k,
                kind=f"plant:{dtype}",
            )
            self._devices[next_instance] = dev
            self._devices_by_ip[ip]      = dev
            self._telemetry[next_instance] = PlantTelemetryEngine(
                dtype, rated_kw=rkw, seed=(hash(name) & 0xFFFFFFFF),
            )
            next_instance += 1

        # Start recv thread
        self._stop_ev.clear()
        self._recv_thread = threading.Thread(
            target=self._recv_loop,
            daemon=True,
            name="BACnet-recv",
        )
        self._recv_thread.start()
        self._running = True

        self._log(
            f"[BACnet] Started — {len(device_ips)} EV2 device(s), "
            f"instances {base_instance}–{base_instance + len(device_ips) - 1}, "
            f"{circuits} circuits each, port {port}.",
            "success",
        )

        if self._ready_cb:
            try:
                self._ready_cb()
            except Exception:
                pass

    def stop(self) -> None:
        """Graceful shutdown: stop recv thread, close all sockets."""
        if not self._running:
            return

        self._stop_ev.set()

        if self._recv_thread:
            self._recv_thread.join(timeout=3.0)
            self._recv_thread = None

        if self._recv_sock:
            try:
                self._recv_sock.close()
            except Exception:
                pass
            self._recv_sock = None

        for dev in self._devices.values():
            dev.close()

        self._devices.clear()
        self._devices_by_ip.clear()
        self._telemetry.clear()
        self._running = False
        self._log("[BACnet] Stopped.", "info")

    def is_running(self) -> bool:
        return self._running

    def device_count(self) -> int:
        return len(self._devices)

    # ─────────────────────────────────────────────────────────────
    #  Telemetry tick (called by DeviceStateStore)
    # ─────────────────────────────────────────────────────────────

    def tick(self, dt: float) -> None:
        """
        Advance all EV2 telemetry engines by *dt* seconds.

        Applies updated values to BACnet object trees and dispatches
        any pending COV notifications.
        """
        if not self._running:
            return
        for instance, engine in list(self._telemetry.items()):
            dev = self._devices.get(instance)
            if dev is None:
                continue
            try:
                values = engine.tick(dt)
                dev.update_present_values(values)
                dev.dispatch_cov_notifications(
                    send_sock_fallback=self._recv_sock
                )
            except Exception:
                log.exception("[BACnet] tick error for instance %d", instance)

    # ─────────────────────────────────────────────────────────────
    #  Receive loop
    # ─────────────────────────────────────────────────────────────

    def _recv_loop(self) -> None:
        """
        Background thread: multiplex over wildcard + per-device sockets.

        Each EV2BACnetDevice now binds its socket to device_ip:BACNET_PORT.
        On Windows (and Linux), the OS routes unicast packets to the most-
        specific binding, so a ReadProperty sent to device_ip:47808 arrives
        on THAT device's socket — giving us unambiguous routing without
        IP_PKTINFO / recvmsg.

        Broadcast Who-Is still arrives on the wildcard recv socket
        (0.0.0.0:47808); we fan it out to all devices from there.
        """
        wildcard = self._recv_sock
        if wildcard is None:
            return

        # Build socket → device map for per-device sockets
        sock_to_dev: dict = {}
        for dev in self._devices.values():
            if dev._send_sock is not None:
                sock_to_dev[dev._send_sock] = dev

        all_socks = [wildcard] + list(sock_to_dev.keys())

        while not self._stop_ev.is_set():
            try:
                readable, _, _ = select.select(all_socks, [], [], 1.0)
            except OSError:
                break
            for sock in readable:
                try:
                    data, src_addr = sock.recvfrom(4096)
                except OSError:
                    continue
                try:
                    # None → broadcast/wildcard path; Device → per-device path
                    target_dev = sock_to_dev.get(sock)
                    self._dispatch(data, src_addr, target_dev=target_dev)
                except Exception:
                    log.exception("[BACnet] dispatch error from %s", src_addr)

    def _dispatch(self, data: bytes, src_addr,
                  target_dev: 'Optional[EV2BACnetDevice]' = None) -> None:
        """
        Parse BVLL/NPDU/APDU and route to the correct device handler.

        target_dev — the EV2BACnetDevice whose socket received the packet.
          • Non-None: route confirmed requests directly to that device.
            This is the per-device unicast path (clean, unambiguous).
          • None: packet arrived on the wildcard socket (broadcast or
            legacy tool sending without IP-specific routing).  Fall back
            to the old object-instance lookup for Who-Is fan-out and
            device-object confirmed requests.
        """
        bvlc_func, npdu_data = parse_bvll(data)
        if bvlc_func is None:
            return

        apdu_data = parse_npdu(npdu_data)
        if apdu_data is None:
            return

        pdu = parse_apdu(apdu_data)
        if pdu is None:
            return

        pdu_type = pdu['type']
        service  = pdu['service']
        payload  = pdu['data']

        if pdu_type == 'unconfirmed':
            if service == SVC_WHO_IS:
                low, high = decode_whois(payload)
                if target_dev is not None:
                    # Unicast Who-Is arrived on a device socket — only that
                    # device replies (correct BACnet behaviour).
                    try:
                        target_dev.handle_whois(low, high, src_addr)
                    except Exception:
                        pass
                else:
                    # Broadcast Who-Is on wildcard socket — all matching
                    # devices reply (simulator convenience behaviour).
                    for dev in list(self._devices.values()):
                        try:
                            dev.handle_whois(low, high, src_addr)
                        except Exception:
                            pass

        elif pdu_type == 'confirmed':
            invoke_id = pdu['invoke_id']

            if service in (SVC_READ_PROPERTY, SVC_READ_PROPERTY_MULTIPLE,
                           SVC_SUBSCRIBE_COV):
                if target_dev is not None:
                    # Arrived on per-device socket → route directly. This is
                    # the fix for the "always returns first device" bug: every
                    # AI/BI instance exists on all devices, so object-tree
                    # search was returning the wrong device for unicast reads.
                    target_dev.handle_confirmed(invoke_id, service, payload,
                                                src_addr)
                else:
                    # Arrived on wildcard socket — use object-based routing
                    # (works correctly for Device-object reads; falls back to
                    # first-match for AI/BI which is acceptable here since
                    # well-behaved clients send unicast to device IPs).
                    dev = self._route_confirmed(service, payload, src_addr)
                    if dev:
                        dev.handle_confirmed(invoke_id, service, payload,
                                             src_addr)
                    else:
                        from core.bacnet_object_model import build_reject
                        try:
                            if self._recv_sock:
                                self._recv_sock.sendto(
                                    build_reject(invoke_id), src_addr)
                        except Exception:
                            pass

    def _route_confirmed(
        self,
        service: int,
        payload: bytes,
        src_addr,
    ) -> Optional[EV2BACnetDevice]:
        """
        Determine which device should handle a confirmed request.

        Strategy:
          1. Parse the first object-identifier from the payload.
          2. If it's a DEVICE object, look up by device instance number.
          3. For other object types, find the device whose object tree
             contains that (type, instance) key.
          4. Fall back to the device whose IP matches src_addr destination
             (not easily available without recvmsg/IP_PKTINFO) — skip.
        """
        from core.bacnet_object_model import (
            decode_read_property, decode_read_property_multiple,
            decode_subscribe_cov, OBJ_DEVICE,
        )

        try:
            if service == SVC_READ_PROPERTY:
                parsed = decode_read_property(payload)
                if parsed:
                    obj_type, obj_inst = parsed[0], parsed[1]
                    return self._find_device_for_object(obj_type, obj_inst)

            elif service == SVC_READ_PROPERTY_MULTIPLE:
                items = decode_read_property_multiple(payload)
                if items:
                    obj_type = items[0]['obj_type']
                    obj_inst = items[0]['obj_inst']
                    return self._find_device_for_object(obj_type, obj_inst)

            elif service == SVC_SUBSCRIBE_COV:
                parsed = decode_subscribe_cov(payload)
                if parsed:
                    return self._find_device_for_object(
                        parsed['obj_type'], parsed['obj_inst'])
        except Exception:
            pass

        # Last resort: if only one device, return it
        if len(self._devices) == 1:
            return next(iter(self._devices.values()))
        return None

    def _find_device_for_object(
        self, obj_type: int, obj_inst: int
    ) -> Optional[EV2BACnetDevice]:
        """Return the device that owns (obj_type, obj_inst)."""
        if obj_type == OBJ_DEVICE:
            return self._devices.get(obj_inst)
        # Search object trees
        for dev in self._devices.values():
            if (obj_type, obj_inst) in dev._objects:
                return dev
        return None

    # ─────────────────────────────────────────────────────────────
    #  Helpers
    # ─────────────────────────────────────────────────────────────

    def _log(self, msg: str, level: str = "info") -> None:
        log.info(msg)
        if self._log_cb:
            try:
                self._log_cb(msg, level)
            except Exception:
                pass

    def get_all_subscribers(self) -> List[dict]:
        result = []
        for dev in self._devices.values():
            result.extend(dev.get_all_subscribers())
        return result

    def get_cov_events(self) -> List[dict]:
        result = []
        for dev in self._devices.values():
            result.extend(dev.get_cov_events())
        return result[-100:]

    def get_device_summary(self) -> List[dict]:
        """Return list of {ip, instance, name, circuits} for the UI table."""
        result = []
        for dev in self._devices.values():
            result.append({
                "ip":       dev.device_ip,
                "instance": dev.device_instance,
                "name":     dev.device_name,
                "circuits": dev.circuits,
                "status":   "Active" if self._running else "Stopped",
            })
        return result

    def get_telemetry_snapshot(self) -> List[dict]:
        """Return per-device snapshot of all current BACnet present values.

        Each entry: {ip, instance, name, circuits, values: {name: float}}.
        Returns empty list if not running.
        """
        if not self._running:
            return []
        result = []
        for instance, dev in self._devices.items():
            result.append({
                "ip":       dev.device_ip,
                "instance": instance,
                "name":     dev.device_name,
                "circuits": dev.circuits,
                "kind":     getattr(dev, "kind", "ev2"),
                "values":   dev.get_snapshot(),
            })
        return result