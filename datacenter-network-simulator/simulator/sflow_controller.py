"""
sFlow Controller — manages the sFlow v5 agent lifecycle.

Mirrors SNMPSimController / GNMIController in interface so the UI treats all
three simulators uniformly.

How it works
------------
start() spawns a daemon thread that loops every `interval` seconds.
For each simulated device IP it:
  1. Reads live interface counters from DeviceStateStore.get_metrics(ip).
  2. Builds a counter datagram  (one counter sample per interface).
  3. Optionally builds a flow datagram (synthetic flows toward topology neighbours).
  4. Sends both datagrams to the configured UDP collector endpoint.

No subprocess, no external libraries — pure Python socket + struct.

Usage::

    ctrl = SFlowController()
    ctrl.set_state_store(state_store)
    ctrl.set_topology(topology)
    ctrl.set_device_manager(device_manager)
    ctrl.set_log_callback(lambda msg: print(msg))
    ctrl.set_ready_callback(lambda: print("sFlow ready"))
    ctrl.start(device_ips=["10.1.0.1", "10.1.0.2"],
               collector_ip="127.0.0.1", collector_port=6343,
               interval=30, sample_rate=1000)
    ...
    ctrl.stop()
"""
from __future__ import annotations

import logging
import random
import socket
import threading
import time
from typing import Callable, List, Optional, TYPE_CHECKING

from core.sflow_generator import SFlowGenerator, pick_flow_ports, pick_protocol

if TYPE_CHECKING:
    from core.device_state_store import DeviceStateStore
    from core.topology_engine import TopologyEngine
    from core.device_manager import DeviceManager, Device

log = logging.getLogger(__name__)


class SFlowController:
    """Simulate sFlow v5 agents for every device in the topology."""

    def __init__(self):
        self._store:    Optional["DeviceStateStore"] = None
        self._topology: Optional["TopologyEngine"]   = None
        self._dm:       Optional["DeviceManager"]    = None

        self._log_cb:    Optional[Callable[[str], None]] = None
        self._status_cb: Optional[Callable[[str], None]] = None
        self._ready_cb:  Optional[Callable[[], None]]    = None

        self._thread:   Optional[threading.Thread] = None
        self._stop_ev   = threading.Event()
        self._running   = False

        self._gen = SFlowGenerator()

        # Config — set by start()
        self._device_ips:    List[str] = []
        self._collector_ip   = "127.0.0.1"
        self._collector_port = 6343
        self._interval       = 30
        self._sample_rate    = 1000

    # ------------------------------------------------------------------ #
    #  Dependency injection                                                #
    # ------------------------------------------------------------------ #

    def set_state_store(self, store: "DeviceStateStore"):
        self._store = store

    def set_topology(self, topology: "TopologyEngine"):
        self._topology = topology

    def set_device_manager(self, dm: "DeviceManager"):
        self._dm = dm

    # ------------------------------------------------------------------ #
    #  Callbacks                                                           #
    # ------------------------------------------------------------------ #

    def set_log_callback(self, cb: Callable[[str], None]):
        self._log_cb = cb

    def set_status_callback(self, cb: Callable[[str], None]):
        self._status_cb = cb

    def set_ready_callback(self, cb: Callable[[], None]):
        self._ready_cb = cb

    def _log(self, msg: str, level: str = "info"):
        log.info(msg)
        if self._log_cb:
            self._log_cb(msg)

    def _set_status(self, s: str):
        if self._status_cb:
            self._status_cb(s)

    # ------------------------------------------------------------------ #
    #  State                                                               #
    # ------------------------------------------------------------------ #

    def is_running(self) -> bool:
        return self._running

    def get_collector(self) -> str:
        return f"{self._collector_ip}:{self._collector_port}"

    def get_device_count(self) -> int:
        return len(self._device_ips)

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(
        self,
        device_ips: List[str],
        collector_ip:   str = "127.0.0.1",
        collector_port: int = 6343,
        interval:       int = 30,
        sample_rate:    int = 1000,
    ) -> bool:
        if self._running:
            self._log("[sFlow] Already running.")
            return True

        self._device_ips    = list(device_ips)
        self._collector_ip  = collector_ip
        self._collector_port = collector_port
        self._interval      = interval
        self._sample_rate   = sample_rate

        self._log(
            f"[sFlow] Starting — exporting to {collector_ip}:{collector_port} "
            f"every {interval}s, sample rate 1:{sample_rate}, "
            f"{len(device_ips)} device agent(s)"
        )
        self._set_status("Starting…")

        self._stop_ev.clear()
        self._thread = threading.Thread(
            target=self._send_loop,
            daemon=True,
            name="SFlowController-sender",
        )
        self._running = True
        self._thread.start()

        self._set_status("Running")
        self._log(
            f"[sFlow] Running — {len(self._device_ips)} sFlow agent(s) active, "
            f"collector {collector_ip}:{collector_port}"
        )
        if self._ready_cb:
            self._ready_cb()

        return True

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._stop_ev.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 2)
            self._thread = None
        self._set_status("Stopped")
        self._log("[sFlow] Stopped.")

    # ------------------------------------------------------------------ #
    #  Sender thread                                                       #
    # ------------------------------------------------------------------ #

    def _send_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            seq_ctr: dict = {}   # {ip: int} — counter datagram sequence numbers
            seq_flw: dict = {}   # {ip: int} — flow datagram sequence numbers

            while not self._stop_ev.wait(self._interval):
                uptime_ms = int(time.monotonic() * 1000) & 0xFFFFFFFF
                total_bytes = 0
                for ip in list(self._device_ips):
                    try:
                        sent = self._send_for_device(
                            sock, ip, seq_ctr, seq_flw, uptime_ms
                        )
                        total_bytes += sent
                    except Exception as exc:
                        self._log(f"[sFlow] {ip}: send error — {exc}", "warning")

                if total_bytes:
                    self._log(
                        f"[sFlow] Tick — {len(self._device_ips)} agent(s), "
                        f"{total_bytes} bytes sent to "
                        f"{self._collector_ip}:{self._collector_port}"
                    )
        finally:
            sock.close()

    def _send_for_device(
        self,
        sock: socket.socket,
        ip: str,
        seq_ctr: dict,
        seq_flw: dict,
        uptime_ms: int,
    ) -> int:
        if self._store is None:
            return 0

        metrics = self._store.get_metrics(ip)
        if metrics is None:
            return 0

        device = self._find_device(ip)
        sent   = 0

        # ── Counter datagram ──────────────────────────────────────────────
        iface_data = metrics.get("interfaces", {})
        iface_metrics = []
        for idx, (name, stats) in enumerate(iface_data.items(), start=1):
            speed = self._iface_speed(device, name)
            iface_metrics.append((idx, speed, stats))

        if iface_metrics:
            seq_ctr[ip] = seq_ctr.get(ip, 0) + 1
            dgram = self._gen.build_counter_datagram(
                ip, 0, seq_ctr[ip], uptime_ms,
                iface_metrics[:64],   # cap to keep UDP payload < 9 kB
            )
            sock.sendto(dgram, (self._collector_ip, self._collector_port))
            sent += len(dgram)

        # ── Flow datagram ─────────────────────────────────────────────────
        flow_entries = self._build_flow_entries(ip, device, len(iface_metrics))
        if flow_entries:
            seq_flw[ip] = seq_flw.get(ip, 0) + 1
            dgram = self._gen.build_flow_datagram(
                ip, 1, seq_flw[ip], uptime_ms,
                flow_entries, self._sample_rate,
            )
            sock.sendto(dgram, (self._collector_ip, self._collector_port))
            sent += len(dgram)

        return sent

    # ------------------------------------------------------------------ #
    #  Flow entry synthesis                                                #
    # ------------------------------------------------------------------ #

    def _build_flow_entries(
        self,
        ip: str,
        device: Optional["Device"],
        num_ifaces: int,
    ) -> list:
        """Generate 2–5 synthetic flow records for this device."""
        if not num_ifaces:
            return []

        dtype = device.device_type.value if device else "router"
        neighbors = self._neighbor_ips(device)
        dst_pool  = neighbors if neighbors else [ip]  # loopback if isolated

        entries = []
        count = min(5, max(2, len(dst_pool) * 2))
        for _ in range(count):
            if_index = random.randint(1, max(1, num_ifaces))
            dst_ip   = random.choice(dst_pool)
            src_port, dst_port = pick_flow_ports(dtype)
            protocol = pick_protocol(dtype)
            entries.append((if_index, ip, dst_ip, src_port, dst_port, protocol))

        return entries

    def _neighbor_ips(self, device: Optional["Device"]) -> List[str]:
        if device is None or self._topology is None:
            return []
        try:
            return [n.ip_address for n in self._topology.get_neighbors(device.id)]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _find_device(self, ip: str) -> Optional["Device"]:
        if self._dm is None:
            return None
        for d in self._dm.get_all_devices():
            if d.ip_address == ip:
                return d
        return None

    @staticmethod
    def _iface_speed(device: Optional["Device"], iface_name: str) -> int:
        if device is None:
            return 1_000_000_000
        for iface in device.interfaces:
            if iface.name == iface_name:
                return iface.speed
        return 1_000_000_000