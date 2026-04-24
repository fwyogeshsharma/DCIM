"""
gNMI Controller — manages the gNMI gRPC server lifecycle.

Mirrors SNMPSimController in interface so the UI treats both uniformly.

Usage:
    ctrl = GNMIController("datasets")
    ctrl.set_log_callback(lambda msg: print(msg))
    ctrl.set_ready_callback(lambda: print("gNMI ready"))
    ctrl.start(device_ips=["10.1.0.2", "10.1.0.3"], port=50051)
    ...
    ctrl.reload_device("10.1.0.2")   # hot-reload after metrics randomize
    ctrl.stop()
"""
from __future__ import annotations

import sys
import threading
import time
import logging
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger(__name__)


def _ensure_stubs() -> bool:
    """
    Compile proto/gnmi.proto → proto/compiled/ if the stubs are missing.
    Returns True if the stubs are (or become) available.
    """
    compiled_dir = Path(__file__).parent.parent / "proto" / "compiled"
    stub = compiled_dir / "gnmi_pb2.py"
    if stub.exists():
        return True

    try:
        from grpc_tools import protoc
    except ImportError:
        return False  # grpcio-tools not installed

    proto_dir = Path(__file__).parent.parent / "proto"
    compiled_dir.mkdir(parents=True, exist_ok=True)
    (compiled_dir / "__init__.py").write_text("# auto-generated\n")

    args = [
        "grpc_tools.protoc",
        f"--proto_path={proto_dir}",
        f"--python_out={compiled_dir}",
        f"--grpc_python_out={compiled_dir}",
        str(proto_dir / "gnmi.proto"),
    ]
    try:
        ret = protoc.main(args)
        if ret == 0:
            log.info("gNMI proto stubs compiled to %s", compiled_dir)
            return True
        log.error("protoc exited with code %d", ret)
        return False
    except Exception as exc:
        log.error("Proto compilation failed: %s", exc)
        return False


class GNMIController:
    """Start, stop, and manage the gNMI gRPC server process."""

    def __init__(self, datasets_dir: str = "datasets/gnmi"):
        self.datasets_dir = str(Path(datasets_dir).resolve())

        self._log_cb:        Optional[Callable[[str], None]] = None
        self._status_cb:     Optional[Callable[[str], None]] = None
        self._ready_cb:      Optional[Callable[[], None]]    = None
        self._auto_proxy_cb: Optional[Callable[[], None]]    = None

        self._server    = None           # proxy GNMIServer (port 50051)
        self._servicer  = None           # shared GNMIServicer (all devices)
        self._proxy_svc = None           # GNMIProxyServicer wrapping _servicer
        # Per-device servers: ip → (GNMIServicer, GNMIServer)
        self._per_device: dict = {}
        self._thread:   Optional[threading.Thread] = None
        self._running   = False
        self._ready     = False
        self._port      = 50051
        self._device_port = 57400        # port for per-device gRPC servers
        self._device_ips: List[str] = []
        self._store     = None   # shared DeviceStateStore

    # ------------------------------------------------------------------ #
    #  Callbacks                                                           #
    # ------------------------------------------------------------------ #

    def set_state_store(self, store):
        """Attach a DeviceStateStore so gNMI servicers serve live metrics."""
        self._store = store
        if self._servicer is not None:
            self._servicer.set_state_store(store)
        for svc, _ in self._per_device.values():
            svc.set_state_store(store)

    def set_log_callback(self, cb: Callable[[str], None]):
        self._log_cb = cb

    def set_status_callback(self, cb: Callable[[str], None]):
        self._status_cb = cb

    def set_ready_callback(self, cb: Callable[[], None]):
        self._ready_cb = cb

    def set_auto_proxy_callback(self, cb: Callable[[], None]):
        """Called (from background thread) when proxy is auto-started after
        per-device binding completely fails.  The callback must be thread-safe
        (use QTimer.singleShot(0, ...) on the Qt side)."""
        self._auto_proxy_cb = cb

    def _log(self, msg: str):
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

    def is_proxy_running(self) -> bool:
        return self._server is not None

    def is_ready(self) -> bool:
        return self._ready

    def get_port(self) -> int:
        return self._port

    def get_active_targets(self) -> List[str]:
        if self._servicer:
            return self._servicer.get_targets()
        return []

    def target_counts(self) -> dict:
        if self._servicer:
            return self._servicer.target_counts()
        return {"switch": 0, "router": 0}

    def get_per_device_count(self) -> int:
        """Number of currently running per-device gRPC servers."""
        return len(self._per_device)

    # ------------------------------------------------------------------ #
    #  Start                                                               #
    # ------------------------------------------------------------------ #

    def start(self, device_ips: List[str], port: int = 50051,
              bound_ip_ports: dict = None) -> bool:
        """
        Start device simulation (per-device gRPC servers).

        The proxy server is NOT started here — call start_proxy() separately
        after the simulation is running if a single aggregated endpoint is needed.

        Parameters
        ----------
        device_ips      : All simulated device IPs (used to know what data exists).
        port            : Default proxy port stored for later use by start_proxy().
        bound_ip_ports  : {ip: gnmi_port} for devices whose IP is bound to the adapter.
                          A dedicated per-device gRPC server is started on each IP.
        """
        if self._running:
            self._log("gNMI server is already running.")
            return True

        self._log("Checking gNMI proto stubs…")
        if not _ensure_stubs():
            msg = (
                "gNMI proto stubs not found and could not be compiled.\n"
                "Run:  pip install grpcio-tools  then  python compile_protos.py"
            )
            self._log(f"ERROR: {msg}")
            self._set_status("Error: stubs missing")
            return False

        try:
            import grpc  # noqa — just verify grpcio is importable
        except ImportError:
            self._log("ERROR: grpcio not installed. Run: pip install grpcio")
            self._set_status("Error: grpcio not installed")
            return False

        self._port       = port
        self._device_ips = list(device_ips)

        from simulator.gnmi_server import GNMIServicer, GNMIServer, GNMIProxyServicer

        # ── Shared servicer (all device data) ────────────────────────────
        self._servicer = GNMIServicer(self.datasets_dir)
        if self._store is not None:
            self._servicer.set_state_store(self._store)
        loaded = self._servicer.load_all()
        self._log(f"[gNMI] Loaded {loaded} device dataset(s) from '{self.datasets_dir}/'")

        if loaded == 0:
            self._log("WARNING: No .gnmi.json files found — generate datasets first.")

        # ── Build proxy servicer (used when proxy is later enabled) ───────
        self._proxy_svc = GNMIProxyServicer(self._servicer)

        # ── Per-device servers (one gRPC server per bound IP:port) ────────
        if bound_ip_ports:
            self._start_per_device_servers(bound_ip_ports, GNMIServicer, GNMIServer)

        self._running = True
        self._ready   = True

        counts   = self._servicer.target_counts()
        n_direct = len(self._per_device)
        self._log(
            f"[gNMI] Device simulation ready — "
            f"{counts.get('switch', 0)} switches, {counts.get('router', 0)} routers"
            + (f" | {n_direct} direct server(s)" if n_direct else "")
        )
        self._set_status("Running")
        if self._ready_cb:
            self._ready_cb()
        return True

    def start_proxy(self, port: int = None) -> bool:
        """Start the aggregating proxy gRPC server on 0.0.0.0:port.

        Device simulation must already be running (call start() first).
        """
        if not self._running:
            self._log("[gNMI] Cannot start proxy — device simulation is not running.")
            return False
        if self._server is not None:
            self._log("[gNMI] Proxy is already running.")
            return True

        if port is not None:
            self._port = port

        from simulator.gnmi_server import GNMIServer

        self._server = GNMIServer(self._proxy_svc, port=self._port)
        ok = self._server.start()
        if not ok:
            self._server = None
            self._log("[gNMI] Proxy server failed to start.")
            return False

        self._log(f"[gNMI] Proxy started on port {self._port}")
        return True

    def stop_proxy(self):
        """Stop the proxy server without stopping per-device simulation."""
        if self._server is not None:
            self._server.stop()
            self._server = None
            self._log("[gNMI] Proxy stopped.")

    def _start_per_device_servers(self, bound_ip_ports: dict, GNMIServicer, GNMIServer):
        """Start one gRPC server per bound IP.  Failed binds are retried in a
        background thread (up to 5 attempts, 2 s apart) so the UI is not blocked
        while Windows finishes activating the newly-added loopback IPs."""
        started, failed_ips, first_err = self._try_bind_servers(
            bound_ip_ports, GNMIServicer, GNMIServer
        )
        if started:
            self._log(f"[gNMI] {started} direct server(s) started")
        if failed_ips:
            self._log(
                f"[gNMI] {len(failed_ips)} server(s) not yet ready — "
                "retrying in background (loopback IPs may need a moment to activate)…"
            )
            t = threading.Thread(
                target=self._retry_bind_servers,
                args=(failed_ips, GNMIServicer, GNMIServer, 1, first_err),
                daemon=True,
            )
            t.start()

    @staticmethod
    def _port_is_winnat_blocked(ip: str, port: int) -> bool:
        """Return True if WinError 10013 (WSAEACCES) prevents binding ip:port.
        That specific error means Windows Hyper-V / WinNAT has dynamically
        reserved the port range — it is NOT a generic 'port in use' error."""
        import socket as _sock
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        try:
            s.bind((ip, port))
            return False          # bind worked — not blocked
        except OSError as e:
            return getattr(e, "winerror", None) == 10013
        finally:
            try:
                s.close()
            except Exception:
                pass

    def _fix_winnat_port_reservation(self, port: int) -> bool:
        """
        Attempt to free port *port* from Windows dynamic-port allocation
        (Hyper-V / WinNAT).  Returns True if the exclusion was added.

        Sequence:
          1. net stop winnat          — release current reservations
          2. netsh add excludedportrange — lock this port for our use
          3. net start winnat         — resume NAT with updated ranges
        """
        import subprocess
        CREATE_NO_WINDOW = 0x08000000

        def _run(cmd):
            return subprocess.run(
                cmd, capture_output=True, text=True,
                creationflags=CREATE_NO_WINDOW, timeout=30,
            )

        self._log(
            f"[gNMI] WinNAT has reserved port {port} — "
            "attempting automatic fix (stop WinNAT → exclude port → restart)…"
        )
        try:
            _run(["net", "stop", "winnat"])
            r = _run([
                "netsh", "int", "ipv4", "add", "excludedportrange",
                "protocol=tcp",
                f"startport={port}",
                "numberofports=1",
                "store=persistent",
            ])
            _run(["net", "start", "winnat"])
            if r.returncode == 0:
                self._log(
                    f"[gNMI] Port {port} successfully excluded from Windows "
                    "dynamic range.  This fix persists across restarts."
                )
                return True
            self._log(
                f"[gNMI] Could not add port exclusion "
                f"(exit {r.returncode}): {(r.stdout + r.stderr).strip()}"
            )
        except Exception as exc:
            self._log(f"[gNMI] WinNAT fix failed: {exc}")
        return False

    def _try_bind_servers(self, ip_ports: dict, GNMIServicer, GNMIServer):
        """Attempt to start one gRPC server per entry in *ip_ports*.
        Returns (started_count, failed_dict, first_error_str)."""
        started = 0
        failed: dict = {}
        first_err = ""

        # Check once whether the per-device port is blocked by WinNAT.
        # If so, run the automatic fix before attempting any gRPC binds.
        if ip_ports:
            sample_ip, sample_port = next(iter(ip_ports.items()))
            if self._port_is_winnat_blocked(sample_ip, sample_port):
                fixed = self._fix_winnat_port_reservation(sample_port)
                if fixed:
                    time.sleep(2)   # let WinNAT fully restart

        for ip, device_port in ip_ports.items():
            try:
                svc = GNMIServicer(self.datasets_dir)
                if self._store is not None:
                    svc.set_state_store(self._store)
                if not svc.load_device(ip):
                    self._log(f"[gNMI] No dataset for {ip} — skipping")
                    continue
                srv = GNMIServer(svc, port=device_port)
                if srv.start(bind_address=f"{ip}:{device_port}"):
                    self._per_device[ip] = (svc, srv)
                    self._proxy_svc.add_device(ip, device_port)
                    started += 1
                else:
                    failed[ip] = device_port
                    if not first_err:
                        first_err = getattr(srv, "last_error", "")
            except Exception as exc:
                failed[ip] = device_port
                if not first_err:
                    first_err = str(exc)
        return started, failed, first_err

    def _retry_bind_servers(self, failed_ips: dict, GNMIServicer, GNMIServer,
                            attempt: int, first_err: str):
        """Background retry loop — called from a daemon thread."""
        MAX_ATTEMPTS = 5
        RETRY_DELAY  = 2          # seconds between attempts

        time.sleep(RETRY_DELAY)
        if not self._running:
            return

        newly_started, still_failed, new_err = self._try_bind_servers(
            failed_ips, GNMIServicer, GNMIServer
        )

        if newly_started:
            total = len(self._per_device)
            self._log(
                f"[gNMI] Retry {attempt}: {newly_started} more direct server(s) started "
                f"({total} total)"
            )

        if not still_failed:
            self._log("[gNMI] All direct servers are running.")
            return

        if attempt >= MAX_ATTEMPTS:
            n = len(still_failed)
            self._log(
                f"[gNMI] WARNING: {n} per-device gRPC server(s) could not bind after "
                f"{MAX_ATTEMPTS} attempts — Windows secondary-IP TCP limitation."
                + (f"\n        Cause: {new_err or first_err}" if (new_err or first_err) else "")
            )
            # Auto-start proxy so devices are reachable immediately.
            if self._server is None:
                ok = self.start_proxy()
                if ok:
                    self._log(
                        f"[gNMI] Proxy auto-started on port {self._port} — "
                        "connect with:  --host 127.0.0.1 "
                        f"--port {self._port} --target <device-ip>"
                    )
                    if self._auto_proxy_cb:
                        self._auto_proxy_cb()
            return

        # Schedule next retry
        self._retry_bind_servers(
            still_failed, GNMIServicer, GNMIServer,
            attempt + 1, new_err or first_err
        )

    def _stop_per_device_servers(self):
        """Stop all per-device gRPC servers and close proxy channels."""
        if self._proxy_svc:
            self._proxy_svc.close_all()
        for ip, (svc, srv) in list(self._per_device.items()):
            try:
                srv.stop(grace=1.0)
            except Exception:
                pass
        self._per_device.clear()

    # ------------------------------------------------------------------ #
    #  Stop                                                                #
    # ------------------------------------------------------------------ #

    def stop(self):
        self.stop_proxy()
        self._stop_per_device_servers()
        self._servicer  = None
        self._proxy_svc = None
        self._running   = False
        self._ready     = False
        self._set_status("Stopped")
        self._log("[gNMI] Server stopped.")

    # ------------------------------------------------------------------ #
    #  Hot reload (called after Randomize Metrics)                         #
    # ------------------------------------------------------------------ #

    def reload_device(self, ip: str):
        """Hot-reload a single device's JSON data without restarting."""
        if self._servicer:
            ok = self._servicer.reload_device(ip)
            if ok:
                self._log(f"[gNMI] Hot-reloaded data for {ip}")

    def get_clients(self) -> list:
        """Return a snapshot of connected Subscribe clients from all servers.

        Aggregates:
          • shared servicer  — proxy clients without a routed target
          • per-device servicers — direct connections and proxy-routed clients
        For per-device servers the device IP is used as the target when the
        client didn't specify one (common for direct-connect usage).
        """
        clients = []
        # Proxy-tracked clients (routed connections) + local-servicer clients
        if self._proxy_svc:
            clients.extend(self._proxy_svc.get_clients())
        # Direct connections to per-device servers
        for ip, (svc, _srv) in list(self._per_device.items()):
            for c in svc.get_clients():
                # Label with device IP when client didn't set a target
                if c.get("target") in ("(all)", "", None):
                    c = dict(c)
                    c["target"] = ip
                clients.append(c)
        return clients

    def reload_all(self):
        """Reload all device data files (e.g. after full re-generation)."""
        if self._servicer:
            n = self._servicer.load_all()
            self._log(f"[gNMI] Reloaded {n} device(s)")