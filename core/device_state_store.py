"""
DeviceStateStore — shared in-memory metrics layer.

Both SNMP and gNMI draw live device metrics from the same source so that
polling either protocol returns consistent values for the same device.

How it works
------------
gNMI  →  GNMIServicer.set_state_store(store)
          On every response, store.get_metrics(ip) is called.
          The returned dict overlays live values onto the static JSON template.

SNMP  →  Every `snmp_sync_every` ticks the store calls SNMPRecGenerator to
          rewrite the .snmprec files and then rebuilds the .dbm indexes.
          snmpsim-lextudio detects that the index mtime changed and automatically
          serves the fresh values on the next incoming SNMP request.

Tick behaviour (every `tick_interval` seconds, random-walk):
  cpu_usage     ±3 pp       clamped  1–99
  memory_used   ±2% total   clamped 10%–90%
  sys_uptime    += tick_interval × 100  (centiseconds)
  iface counters: random increments; rare error increments
"""
from __future__ import annotations

import logging
import random
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.device_manager import Device, DeviceManager
    from core.topology_engine import TopologyEngine
    from simulator.snmpsim_controller import SNMPSimController

log = logging.getLogger(__name__)


class DeviceStateStore:
    """
    Single in-memory source of truth for all live device metrics.

    Usage::

        store = DeviceStateStore(device_manager, topology, "datasets", tick_interval=30)
        store.set_log_callback(console.log)
        gnmi_controller.set_state_store(store)       # gNMI reads live
        store.start()

        # When SNMPSim starts:
        store.enable_snmp_sync(snmpsim_controller)

        # When SNMPSim stops:
        store.disable_snmp_sync()

        # When everything stops:
        store.stop()
    """

    def __init__(
        self,
        device_manager: "DeviceManager",
        topology: "TopologyEngine",
        datasets_dir: str,
        tick_interval: float = 30.0,
        snmp_sync_every: int = 1,
    ):
        self._dm              = device_manager
        self._topology        = topology
        self._datasets_dir    = str(Path(datasets_dir).resolve())
        self._tick_interval   = tick_interval
        self._snmp_sync_every = snmp_sync_every

        # Stable boot-time cache: {ip: nanoseconds}.
        # Computed once the first time get_metrics() is called for a device
        # so that boot-time never drifts between gNMI responses.
        self._boot_times: dict = {}

        # SNMP sync
        self._snmp_ctrl: Optional["SNMPSimController"] = None
        self._snmp_enabled: bool = False

        # Background ticker
        self._thread: Optional[threading.Thread] = None
        self._stop_ev = threading.Event()
        self._tick_count: int = 0

        self._log_cb: Optional[Callable[[str, str], None]] = None

    # ------------------------------------------------------------------ #
    #  Configuration                                                       #
    # ------------------------------------------------------------------ #

    def set_log_callback(self, cb: Callable[[str, str], None]):
        """cb(message, level) — level ∈ {"info", "success", "warning", "error"}"""
        self._log_cb = cb

    def enable_snmp_sync(self, snmp_ctrl: "SNMPSimController"):
        """Start regenerating .snmprec + .dbm files every tick so SNMPSim serves live values."""
        self._snmp_ctrl    = snmp_ctrl
        self._snmp_enabled = True
        self._log("[StateStore] SNMP sync enabled — devices will converge on next tick.", "info")

    def disable_snmp_sync(self):
        """Stop regenerating SNMP files (call when SNMPSim stops)."""
        self._snmp_enabled = False
        self._snmp_ctrl    = None
        self._log("[StateStore] SNMP sync disabled.", "info")

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(self):
        """Start the background metrics ticker. Safe to call more than once."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_ev.clear()
        self._thread = threading.Thread(
            target=self._ticker_loop,
            daemon=True,
            name="DeviceStateStore-ticker",
        )
        self._thread.start()
        self._log(
            f"[StateStore] Started — tick every {self._tick_interval}s, "
            f"SNMP sync every {self._snmp_sync_every} tick(s).",
            "info",
        )

    def stop(self):
        """Stop the ticker and clear cached boot times."""
        if self._thread:
            self._stop_ev.set()
            self._thread.join(timeout=self._tick_interval + 2)
            self._thread = None
        self._boot_times.clear()
        self._snmp_enabled = False
        self._tick_count   = 0
        self._log("[StateStore] Stopped.", "info")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------ #
    #  Metrics access (called by GNMIServicer per response)               #
    # ------------------------------------------------------------------ #

    def get_metrics(self, ip: str) -> Optional[dict]:
        """
        Return a live snapshot of all telemetry metrics for *ip*.

        The returned dict is consumed by the gNMI server's
        ``_apply_store_metrics`` function to overlay real-time values onto
        the static JSON-IETF template.

        Returns None if the device is not found.
        """
        device = self._find_device(ip)
        if device is None:
            return None

        # Cache a stable boot-time so it never drifts between responses.
        if ip not in self._boot_times:
            uptime_ns = device.sys_uptime * 10_000_000  # centiseconds → ns
            self._boot_times[ip] = int(time.time() * 1e9) - uptime_ns

        return {
            "cpu_usage":    device.cpu_usage,
            "memory_total": device.memory_total,
            "memory_used":  device.memory_used,
            "boot_time_ns": self._boot_times[ip],
            "cpu_temp":     device.cpu_temp,
            "inlet_temp":   device.inlet_temp,
            "interfaces": {
                iface.name: {
                    "in_octets":        iface.in_octets,
                    "out_octets":       iface.out_octets,
                    "in_errors":        iface.in_errors,
                    "out_errors":       iface.out_errors,
                    "in_discards":      iface.in_discards,
                    "out_discards":     iface.out_discards,
                    "in_unicast_pkts":  iface.in_octets  // 1500,
                    "out_unicast_pkts": iface.out_octets // 1500,
                }
                for iface in device.interfaces
            },
        }

    # ------------------------------------------------------------------ #
    #  Background ticker                                                   #
    # ------------------------------------------------------------------ #

    def _ticker_loop(self):
        while not self._stop_ev.wait(self._tick_interval):
            try:
                self._tick()
            except Exception:
                log.exception("[StateStore] Tick error")

    def _tick(self):
        devices = self._dm.get_all_devices()
        for device in devices:
            self._step_device(device)

        self._tick_count += 1

        if self._snmp_enabled and (self._tick_count % self._snmp_sync_every == 0):
            self._sync_snmp(devices)

    def _step_device(self, device: "Device"):
        """Apply one random-walk step to a single device's metrics."""
        # CPU: ±3 pp, clamped 1–99
        device.cpu_usage = max(1, min(99, device.cpu_usage + random.randint(-3, 3)))

        # Memory: ±2% of total, clamped 10%–90%
        swing = max(1, device.memory_total // 50)
        lo    = device.memory_total // 10
        hi    = int(device.memory_total * 0.9)
        device.memory_used = max(lo, min(hi, device.memory_used + random.randint(-swing, swing)))

        # Uptime: advance by tick_interval (stored in centiseconds)
        device.sys_uptime += int(self._tick_interval * 100)

        # Temperature: track CPU utilisation with slight random noise
        #   CPU/ASIC:  base 40 °C + 0.35 °C per CPU%, clamped 30–95
        #   Inlet:     base 22 °C + 0.12 °C per CPU%, clamped 18–55
        device.cpu_temp   = round(max(30.0, min(95.0,
            40.0 + device.cpu_usage * 0.35 + random.uniform(-1.5, 1.5))), 1)
        device.inlet_temp = round(max(18.0, min(55.0,
            22.0 + device.cpu_usage * 0.12 + random.uniform(-0.5, 0.5))), 1)

        # Interface counters + queue drops — only UP interfaces
        congested  = device.cpu_usage > 70
        moderate   = device.cpu_usage > 50
        for iface in device.interfaces:
            if iface.oper_status != 1:
                continue
            iface.in_octets  += random.randint(5_000, 150_000)
            iface.out_octets += random.randint(5_000, 150_000)
            if random.random() < 0.10:
                iface.in_errors  += 1
            if random.random() < 0.05:
                iface.out_errors += 1
            # Queue drops scale with congestion
            if congested:
                iface.in_discards  += random.randint(0, 5)
                iface.out_discards += random.randint(0, 10)
            elif moderate and random.random() < 0.30:
                iface.in_discards  += random.randint(0, 2)
                iface.out_discards += random.randint(0, 3)

    # ------------------------------------------------------------------ #
    #  SNMP file sync                                                      #
    # ------------------------------------------------------------------ #

    def _sync_snmp(self, devices: list):
        """
        Patch the dynamic metric OIDs (counters, uptime, CPU, memory) in each
        .snmprec file without touching the static data (LLDP, MAC, STP, CDP).
        After patching each file the dbm.dumb index (.dat/.dir) is rebuilt in
        a single O(n) pass with its mtime forced to snmprec_mtime+1, so SNMPSim
        finds a fresh index and skips its own slow internal rebuild entirely.
        Both steps are parallelised across 8 threads to keep total I/O time low.
        """
        try:
            from core.snmprec_generator import SNMPRecGenerator
            from concurrent.futures import ThreadPoolExecutor
            snmp_gen = SNMPRecGenerator(self._datasets_dir)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(snmp_gen.patch_metrics, devices))
            log.debug("[StateStore] SNMP sync — %d file(s) patched.", len(devices))
        except Exception as e:
            log.error("[StateStore] SNMP sync error: %s", e)

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _find_device(self, ip: str) -> Optional["Device"]:
        for d in self._dm.get_all_devices():
            if d.ip_address == ip:
                return d
        return None

    def _log(self, msg: str, level: str = "info"):
        log.info(msg)
        if self._log_cb:
            self._log_cb(msg, level)