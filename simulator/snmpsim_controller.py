"""
SNMPSim Controller - Manages the snmpsim process lifecycle.

Each simulated device gets its own IP:161 endpoint so external monitoring
tools can poll them as if they were real hardware.  SNMPSim is started with
one --agent-udpv4-endpoint flag per device IP.

Dataset directory layout expected by snmpsim-lextudio:
    datasets/
        <device_ip>/
            <community>.snmprec     # e.g. 192.168.1.10/public.snmprec
"""
from __future__ import annotations
import os
import sys
import subprocess
import threading
import shutil
from pathlib import Path
from typing import Optional, Callable, List


class SNMPSimController:
    """Start, stop, and monitor the snmpsim process."""

    def __init__(self, datasets_dir: str = "datasets"):
        self.datasets_dir = str(Path(datasets_dir).resolve())
        self._process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._log_callback: Optional[Callable[[str], None]] = None
        self._status_callback: Optional[Callable[[str], None]] = None
        self._running = False
        self._active_endpoints: List[str] = []

    # ------------------------------------------------------------------ #
    #  Callbacks                                                           #
    # ------------------------------------------------------------------ #

    def set_log_callback(self, cb: Callable[[str], None]):
        self._log_callback = cb

    def set_status_callback(self, cb: Callable[[str], None]):
        self._status_callback = cb

    def _log(self, msg: str):
        if self._log_callback:
            self._log_callback(msg)

    def _set_status(self, status: str):
        if self._status_callback:
            self._status_callback(status)

    # ------------------------------------------------------------------ #
    #  State                                                               #
    # ------------------------------------------------------------------ #

    def is_running(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None

    def get_pid(self) -> Optional[int]:
        return self._process.pid if self._process else None

    def get_active_endpoints(self) -> List[str]:
        return list(self._active_endpoints)

    # ------------------------------------------------------------------ #
    #  Executable discovery                                                #
    # ------------------------------------------------------------------ #

    def _find_snmpsim(self) -> Optional[str]:
        candidates = [
            "snmpsim-command-responder",
            "snmpsim-command-responder.exe",
            "snmpsimd",
            "snmpsimd.py",
            "snmpsimd.exe",
        ]
        for name in candidates:
            path = shutil.which(name)
            if path:
                return path
        python_scripts = Path(sys.executable).parent / "Scripts"
        for name in candidates:
            p = python_scripts / name
            if p.exists():
                return str(p)
        return None

    # ------------------------------------------------------------------ #
    #  Start                                                               #
    # ------------------------------------------------------------------ #

    def start(self, device_ips: List[str], port: int = 161) -> bool:
        """
        Start SNMPSim listening on *device_ip*:*port* for every IP in
        *device_ips*.  The caller is responsible for having already bound
        those IPs to the network interface via netsh.
        """
        if self.is_running():
            self._log("SNMPSim is already running.")
            return True

        if not device_ips:
            self._log("ERROR: No device IPs supplied — nothing to simulate.")
            self._set_status("Error: no IPs")
            return False

        snmpsim_path = self._find_snmpsim()
        if not snmpsim_path:
            self._log("ERROR: snmpsim not found. Install with:  pip install snmpsim-lextudio")
            self._set_status("Error: snmpsim not found")
            return False

        if not os.path.isdir(self.datasets_dir):
            self._log(f"ERROR: Datasets directory not found: {self.datasets_dir}")
            self._set_status("Error: no datasets directory")
            return False

        cmd = self._build_command(snmpsim_path, device_ips, port)
        self._log(f"Starting SNMPSim with {len(device_ips)} device(s) on port {port}")
        self._log(f"  Executable: {snmpsim_path}")
        self._log(f"  Data dir:   {self.datasets_dir}")
        self._log(f"  Listening:  0.0.0.0:{port}  (community string routes each request to its device)")
        self._log(f"  Devices:    {device_ips[0]} … {device_ips[-1]}  ({len(device_ips)} total)")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self._running = True
            self._active_endpoints = [f"{ip}:{port}" for ip in device_ips]
            self._set_status("Running")
            self._start_monitor()
            return True
        except Exception as e:
            self._log(f"ERROR starting SNMPSim: {e}")
            self._set_status(f"Error: {e}")
            return False

    def _build_command(self, snmpsim_path: str, device_ips: List[str], port: int) -> List[str]:
        # Use a single wildcard endpoint instead of one flag per device IP.
        # Passing hundreds of --agent-udpv4-endpoint flags would exceed the
        # Windows 32 KB command-line limit (WinError 206).
        # Routing still works because snmpsim matches requests to .snmprec
        # files via the SNMP community string, which is set to the device IP
        # in every generated dataset (e.g. community "10.1.1.1" →
        # datasets/10.1.1.1.snmprec).  The netsh-bound virtual IPs ensure
        # that traffic for each device IP reaches this process.
        base_cmd = (
            [sys.executable, snmpsim_path]
            if snmpsim_path.endswith(".py")
            else [snmpsim_path]
        )

        return base_cmd + [
            f"--data-dir={self.datasets_dir}",
            "--log-level=info",
            f"--agent-udpv4-endpoint=0.0.0.0:{port}",
        ]

    # ------------------------------------------------------------------ #
    #  Stop                                                                #
    # ------------------------------------------------------------------ #

    def stop(self):
        if self._process and self._process.poll() is None:
            self._log("Stopping SNMPSim...")
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            except Exception as e:
                self._log(f"Error stopping SNMPSim: {e}")
        self._running = False
        self._process = None
        self._active_endpoints = []
        self._set_status("Stopped")
        self._log("SNMPSim stopped.")

    # ------------------------------------------------------------------ #
    #  Log monitor thread                                                  #
    # ------------------------------------------------------------------ #

    def _start_monitor(self):
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._monitor_thread.start()

    def _monitor_loop(self):
        if not self._process:
            return
        try:
            for line in self._process.stdout:
                line = line.rstrip()
                if line:
                    self._log(f"[snmpsim] {line}")
            self._process.wait()
            if self._running:
                self._set_status("Stopped unexpectedly")
                self._log("SNMPSim process ended unexpectedly.")
            self._running = False
        except Exception as e:
            self._log(f"Monitor error: {e}")

    # ------------------------------------------------------------------ #
    #  Utility                                                             #
    # ------------------------------------------------------------------ #

    def get_snmp_walk_command(self, device_ip: str = "127.0.0.1",
                              port: int = 161,
                              community: str = "public") -> str:
        return f"snmpwalk -v2c -c {community} {device_ip}:{port} 1.3.6.1.2.1.1"
