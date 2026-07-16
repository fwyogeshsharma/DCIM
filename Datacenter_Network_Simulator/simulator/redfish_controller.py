"""
Redfish Controller — lifecycle manager for all simulated server BMCs.

Mirrors SNMPSimController / GNMIController / BACnetController so the UI and
REST API treat Redfish like every other protocol.

Architecture
------------
• One non-blocking listen socket per server, bound to that server's IP on the
  configured port (default 8443).  Binding per-IP (not 0.0.0.0) means each BMC
  answers only on its own address, exactly like real hardware.
• A SINGLE acceptor thread runs a selectors (epoll) loop over every BMC socket;
  accepted connections are dispatched to a small bounded worker pool, one HTTP
  request each.  This replaces the former one-serve_forever-thread-per-BMC model,
  which at a few-thousand-server fleet spawned thousands of GIL-thrashing threads
  that starved the API (~40 s responses).  Now 2000 idle BMCs cost ~0 CPU.
• Each connection serves the Redfish tree for ONE Device via a RedfishDevice
  carried on a lightweight _ConnServer handed to the request handler.
• Resource bodies are built on demand from the live Device object, so values
  track the DeviceStateStore ticker — no per-tick push needed for MVP.

Plain HTTP only in this MVP (no TLS).  Clients hit ``http://<ip>:8443/redfish/v1/``.

Usage::

    ctrl = RedfishController()
    ctrl.set_log_callback(lambda msg, lvl: console.log(msg, lvl))
    ctrl.set_ready_callback(lambda: panel.on_ready())
    ctrl.start(devices=[srv1, srv2], port=8443)
    ...
    ctrl.stop()
"""
from __future__ import annotations

import json
import logging
import selectors
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from simulator.redfish_device import RedfishDevice

if TYPE_CHECKING:
    from core.device_manager import Device


def _bind_hint(exc: OSError) -> str:
    """Return a human-readable hint for a bind() failure, or "" if none.

    WSAEACCES (WinError 10013) on a specific-IP bind almost always means the
    port is already owned by another process (a second copy of this app, or
    another tool on the same port) rather than a true permissions problem.
    """
    if getattr(exc, "winerror", None) == 10013:
        return (" — port already in use by another process "
                "(another instance of this app or a tool on the same port). "
                "Close it, then start Redfish again.")
    return ""

log = logging.getLogger(__name__)


class _ConnServer:
    """Minimal stand-in for the socketserver a BaseHTTPRequestHandler expects.
    Carries the RedfishDevice the handler dispatches to; the acceptor pool
    instantiates the handler directly on an accepted connection, so no
    ThreadingHTTPServer (and no per-device serve_forever thread) is needed."""
    __slots__ = ("redfish_device",)

    def __init__(self, rdev):
        self.redfish_device = rdev


class _RedfishHandler(BaseHTTPRequestHandler):
    """Bridges one HTTP request to the server's RedfishDevice.dispatch()."""

    server_version = "RedfishSim/1.0"
    protocol_version = "HTTP/1.1"

    # ── request body ───────────────────────────────────────────────────────
    def _read_body(self) -> Optional[dict]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return None

    # ── response writer ────────────────────────────────────────────────────
    def _respond(self, method: str):
        dev: RedfishDevice = getattr(self.server, "redfish_device", None)
        if dev is None:
            self.send_error(503, "Redfish device not attached")
            return
        body = self._read_body() if method in ("POST", "PATCH") else None
        try:
            status, extra, payload = dev.dispatch(
                method, self.path, self.headers, body)
        except Exception:
            log.exception("[Redfish] dispatch error for %s %s", method, self.path)
            status, extra, payload = 500, {}, {
                "error": {"code": "Base.1.0.InternalError",
                          "message": "Internal simulator error."}}

        data = b"" if payload is None else json.dumps(payload).encode("utf-8")
        # One request per connection: the shared acceptor pool has a bounded set
        # of workers, so a kept-alive connection would pin a worker idle. Close
        # after each response (Content-Length is always set, so this is clean).
        self.close_connection = True
        self.send_response(status)
        self.send_header("OData-Version", "4.0")
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):    self._respond("GET")
    def do_POST(self):   self._respond("POST")
    def do_DELETE(self): self._respond("DELETE")
    def do_PATCH(self):  self._respond("PATCH")

    # Silence the default stderr access log.
    def log_message(self, *args):  # noqa: D401
        return


class RedfishController:
    """Start, stop, and report all simulated server Redfish/BMC endpoints."""

    # Seconds between health-monitor sweeps (fault-alarm push evaluation).
    MONITOR_INTERVAL = 5.0

    def __init__(self):
        self._log_cb:   Optional[Callable[[str, str], None]] = None
        self._ready_cb: Optional[Callable[[], None]]         = None
        # cb(device, is_on, reset_type) — BMC power-transition trap hook.
        self._trap_cb = None

        # ip -> (listen_socket, None, RedfishDevice). One non-blocking listen
        # socket per BMC (each answers on its own IP, like real hardware), all
        # serviced by ONE selectors acceptor thread + a bounded worker pool —
        # instead of one serve_forever thread per BMC (which, at a few-thousand-
        # server fleet, spawned thousands of threads that thrashed the GIL and
        # starved the API to ~40 s responses). See [[fleet-fd-scaling]] option B.
        self._servers: Dict[str, tuple] = {}
        self._running = False
        self._srv_lock = threading.Lock()

        # Shared acceptor: epoll over every BMC listen socket; accepted
        # connections are handled on a small pool (one request each, no keep-
        # alive), so 2000 idle BMCs cost ~0 CPU and only live requests use threads.
        self._sel: Optional[selectors.BaseSelector] = None
        self._sel_dirty = False
        self._acceptor_thread: Optional[threading.Thread] = None
        self._acceptor_stop = threading.Event()
        self._pool: Optional[ThreadPoolExecutor] = None
        self._ACCEPT_WORKERS = 32

        # Background health monitor: polls every BMC's telemetry against
        # thresholds and pushes Warning/Critical events on alarm transitions.
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()

        # Config snapshot for the status endpoint.
        self._port = 8443
        self._username = "admin"
        self._password = "password"
        # Live TopologyEngine, supplied at start(). Lets each BMC report the PSU
        # feed it is corded to; None means Power falls back to a nominal voltage.
        self._topology = None

    # ── callbacks ──────────────────────────────────────────────────────────
    def set_log_callback(self, cb: Callable[[str, str], None]):
        """cb(message, level)  level ∈ {"info","success","warning","error"}"""
        self._log_cb = cb

    def set_ready_callback(self, cb: Callable[[], None]):
        self._ready_cb = cb

    def set_trap_callback(self, cb):
        """cb(device, is_on: bool, reset_type: str) — fired by each BMC on a
        chassis power transition (wire to TrapEngine for SNMP platform traps)."""
        self._trap_cb = cb
        # Propagate to BMCs that are already running. Snapshot under the lock —
        # churn mutates self._servers concurrently.
        with self._srv_lock:
            rdevs = [entry[2] for entry in self._servers.values()]
        for rdev in rdevs:
            rdev._power_trap_cb = cb

    def _log(self, msg: str, level: str = "info"):
        log.info(msg)
        if self._log_cb:
            try:
                self._log_cb(msg, level)
            except Exception:
                pass

    # ── state ──────────────────────────────────────────────────────────────
    def is_running(self) -> bool:
        return self._running

    def device_count(self) -> int:
        return len(self._servers)

    def get_port(self) -> int:
        return self._port

    def get_credentials(self) -> tuple[str, str]:
        return (self._username, self._password)

    # ── shared acceptor (one thread + pool for every BMC socket) ─────────────
    def _make_listener(self, ip: str) -> Optional[socket.socket]:
        """Bind + listen a non-blocking TCP socket for one BMC IP. Binding the
        raw socket avoids HTTPServer.server_bind's getfqdn() reverse-DNS (which
        was ~1.5 s/BMC on Windows). Returns the socket, or None on bind failure."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((ip, self._port))
            s.listen(64)
            s.setblocking(False)
        except OSError:
            try: s.close()
            except OSError: pass
            raise
        return s

    def _start_acceptor(self) -> None:
        if self._acceptor_thread is not None:
            return
        self._sel = selectors.DefaultSelector()
        self._pool = ThreadPoolExecutor(max_workers=self._ACCEPT_WORKERS,
                                        thread_name_prefix="Redfish-req")
        self._acceptor_stop.clear()
        self._sel_dirty = True
        self._acceptor_thread = threading.Thread(
            target=self._acceptor_loop, daemon=True, name="Redfish-acceptor")
        self._acceptor_thread.start()

    def _reregister(self) -> None:
        """Sync the selector to the current listen-socket set (called when a BMC
        is hot-added/removed). Registers each socket with its IP as key data."""
        sel = self._sel
        if sel is None:
            return
        with self._srv_lock:
            self._sel_dirty = False
            want = {ip: entry[0] for ip, entry in self._servers.items()}
        registered = {k.data: k.fileobj for k in list(sel.get_map().values())}
        for ip, fileobj in registered.items():
            if ip not in want or want[ip] is not fileobj:
                try: sel.unregister(fileobj)
                except (KeyError, ValueError): pass
        for ip, sock in want.items():
            if ip not in registered or registered[ip] is not sock:
                try: sel.register(sock, selectors.EVENT_READ, data=ip)
                except (KeyError, ValueError, OSError): pass

    def _acceptor_loop(self) -> None:
        sel = self._sel
        while not self._acceptor_stop.is_set():
            if self._sel_dirty:
                self._reregister()
            try:
                events = sel.select(timeout=0.5)
            except OSError:
                if self._sel_dirty:
                    continue
                break
            for key, _mask in events:
                lsock = key.fileobj
                try:
                    conn, addr = lsock.accept()
                except OSError:
                    continue
                entry = self._servers.get(key.data)
                if entry is None:
                    try: conn.close()
                    except OSError: pass
                    continue
                rdev = entry[2]
                conn.setblocking(True)
                try:
                    self._pool.submit(self._handle_conn, conn, addr, rdev)
                except RuntimeError:                 # pool shutting down
                    try: conn.close()
                    except OSError: pass

    @staticmethod
    def _handle_conn(conn, addr, rdev) -> None:
        """Serve one Redfish HTTP request on an accepted connection, then close.
        Runs on a pool worker; _RedfishHandler drives request parse + dispatch."""
        try:
            conn.settimeout(15.0)            # a stalled client can't pin a worker
            _RedfishHandler(conn, addr, _ConnServer(rdev))
        except Exception:
            log.exception("[Redfish] connection handler error from %s", addr)
        finally:
            try: conn.close()
            except OSError: pass

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(
        self,
        devices: List["Device"],
        port: int = 8443,
        username: str = "admin",
        password: str = "password",
        ip_for: Optional[Callable[["Device"], str]] = None,
        topology=None,
    ) -> bool:
        """
        Bind one HTTP server per server device and begin serving Redfish.

        devices:  list of Device objects (DeviceType.SERVER).
        port:     TCP port for every BMC (default 8443).
        ip_for:   optional fn mapping a Device → bind IP. Defaults to
                  ``device.mgmt_ip or device.ip_address`` (BMC on OOB net).
        topology: optional live TopologyEngine. Lets each BMC report the PSU feed
                  it is actually corded to (input voltage, source PDU/outlet).
                  Without it the Power resource falls back to a 230V nominal.
        """
        if self._running:
            self._log("[Redfish] Already running.", "warning")
            return True
        if not devices:
            self._log("[Redfish] No server devices provided.", "error")
            return False

        self._port = port
        self._username = username
        self._password = password
        # Kept so add_device() gives a hot-commissioned BMC the same topology
        # view as the ones bound at start — otherwise a fleet-added server would
        # silently report a 230V nominal while its rack peers report the real feed.
        self._topology = topology
        if ip_for is None:
            ip_for = lambda d: (d.mgmt_ip or d.ip_address)  # noqa: E731

        started, failed = 0, 0
        for dev in devices:
            ip = ip_for(dev)
            if not ip:
                continue
            rdev = RedfishDevice(dev, username=username, password=password,
                                 power_trap_cb=self._trap_cb, topology=topology)
            try:
                sock = self._make_listener(ip)
            except OSError as exc:
                failed += 1
                self._log(f"[Redfish] {ip}:{port} bind failed — {exc}{_bind_hint(exc)}", "warning")
                continue
            with self._srv_lock:
                self._servers[ip] = (sock, None, rdev)
            started += 1

        if started == 0:
            self._log("[Redfish] No BMC endpoints could be started.", "error")
            self._running = False
            return False

        self._running = True
        self._start_acceptor()
        self._start_monitor()
        msg = (f"[Redfish] Started — {started} BMC endpoint(s) on port {port}"
               + (f" ({failed} bind failure(s))" if failed else "") + ".")
        self._log(msg, "success")
        if self._ready_cb:
            try:
                self._ready_cb()
            except Exception:
                pass
        return True

    # ── hot add / remove (fleet lifecycle — commission a churned server) ──────
    def add_device(self, device, ip_for=None) -> bool:
        """Spin up ONE BMC for a server commissioned into a running Redfish sim.
        The new BMC joins the health monitor automatically (it iterates
        self._servers). Returns True if the endpoint bound."""
        if not self._running:
            return False
        resolve = ip_for or (lambda d: (getattr(d, "mgmt_ip", "") or d.ip_address))
        ip = resolve(device)
        if not ip or ip in self._servers:
            return False
        rdev = RedfishDevice(device, username=self._username, password=self._password,
                             power_trap_cb=self._trap_cb,
                             topology=getattr(self, "_topology", None))
        try:
            sock = self._make_listener(ip)
        except OSError as exc:
            self._log(f"[Redfish] hot-add {ip}:{self._port} bind failed — {exc}{_bind_hint(exc)}", "warning")
            return False
        with self._srv_lock:
            self._servers[ip] = (sock, None, rdev)
        self._sel_dirty = True               # acceptor picks up the new socket
        self._log(f"[Redfish] hot-added BMC {ip}:{self._port}", "success")
        return True

    def remove_device(self, ip: str) -> None:
        """Tear down a decommissioned server's BMC."""
        with self._srv_lock:
            entry = self._servers.pop(ip, None)
        if entry is not None:
            self._sel_dirty = True           # acceptor unregisters the socket
            try:
                if self._sel is not None:
                    self._sel.unregister(entry[0])
            except (KeyError, ValueError):
                pass
            try:
                entry[0].close()
            except OSError:
                pass

    # ── health monitor ──────────────────────────────────────────────────────
    def _start_monitor(self):
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="Redfish-Monitor")
        self._monitor_thread.start()

    def _monitor_loop(self):
        # wait() returns True when signalled to stop → exit promptly.
        while not self._monitor_stop.wait(self.MONITOR_INTERVAL):
            with self._srv_lock:                   # snapshot — churn mutates concurrently
                entries = list(self._servers.items())
            for _ip, (_httpd, _t, rdev) in entries:
                try:
                    rdev.evaluate_alerts()
                except Exception:
                    log.exception("[Redfish] alarm eval failed for %s", _ip)

    def stop(self):
        if not self._running:
            return
        self._monitor_stop.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2.0)
            self._monitor_thread = None
        # Stop the single acceptor thread, then the worker pool, then close every
        # listen socket — no per-BMC serve loops to shut down anymore.
        self._acceptor_stop.set()
        if self._acceptor_thread is not None:
            self._acceptor_thread.join(timeout=2.0)
            self._acceptor_thread = None
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None
        if self._sel is not None:
            try: self._sel.close()
            except Exception: pass
            self._sel = None
        with self._srv_lock:
            entries = list(self._servers.items())
            self._servers.clear()
        for _ip, (sock, _t, _rdev) in entries:
            try:
                sock.close()
            except OSError:
                pass
        self._running = False
        self._log("[Redfish] Stopped.", "info")

    # ── reporting ──────────────────────────────────────────────────────────
    def get_device_summary(self) -> List[dict]:
        """Per-BMC rows for the UI table."""
        # Snapshot under the lock: fleet-lifecycle churn hot-adds/removes BMCs in
        # self._servers on another thread, so iterating it live races (RuntimeError:
        # dictionary changed size during iteration). Copy the entries, then build
        # rows off the stable snapshot.
        with self._srv_lock:
            entries = list(self._servers.items())
        rows = []
        for ip, (_httpd, _t, rdev) in entries:
            d = rdev.device
            rows.append({
                "name":    d.name,
                "ip":      ip,
                "port":    self._port,
                "vendor":  d.vendor.value,
                "model":   d.model_name or d.vendor.value,
                "url":     f"http://{ip}:{self._port}/redfish/v1/",
                "sessions": len(rdev.session_list()),
                "power_state":   rdev.power_state,
                "indicator_led": rdev.indicator_led,
                "sel_count":     len(rdev.sel),
                "subscriptions": len(rdev._subs),
                "status":  "Active" if self._running else "Stopped",
            })
        return rows

    def get_sessions(self) -> List[dict]:
        """All active sessions across every BMC."""
        with self._srv_lock:                       # snapshot — churn mutates concurrently
            entries = list(self._servers.items())
        out = []
        for ip, (_httpd, _t, rdev) in entries:
            for s in rdev.session_list():
                out.append({"ip": ip, "device": rdev.device.name, **s})
        return out

    # ── server operations (driven by the panels / REST control) ─────────────
    def _find(self, ip: str):
        entry = self._servers.get(ip)
        return entry[2] if entry else None     # RedfishDevice

    def perform_action(self, ip: str, action: str) -> Optional[dict]:
        """Run a server operation on the BMC at *ip*. Returns result or None."""
        rdev = self._find(ip)
        if rdev is None:
            return None
        ok, msg = rdev.perform(action)
        return {
            "ok": ok,
            "message": msg,
            "ip": ip,
            "device": rdev.device.name,
            "power_state": rdev.power_state,
            "indicator_led": rdev.indicator_led,
            "sel_count": len(rdev.sel),
        }

    def get_sel(self, ip: str) -> Optional[List[dict]]:
        """Return the System Event Log entries for the BMC at *ip*."""
        rdev = self._find(ip)
        return list(rdev.sel) if rdev else None

    def get_power_state(self, ip: str) -> Optional[str]:
        """Power state ("On"/"Off") of the BMC at *ip*, or None if no BMC."""
        rdev = self._find(ip)
        return rdev.power_state if rdev else None

    # ── push-model event subscriptions ──────────────────────────────────────
    def get_subscriptions(self) -> List[dict]:
        """All push subscriptions across every BMC (for the REST/UI status)."""
        with self._srv_lock:                       # snapshot — churn mutates concurrently
            entries = list(self._servers.items())
        out = []
        for ip, (_httpd, _t, rdev) in entries:
            for sub in rdev._subs.values():
                out.append({
                    "ip": ip,
                    "device": rdev.device.name,
                    "id": sub["id"],
                    "destination": sub["Destination"],
                    "context": sub.get("Context", ""),
                    "event_types": sub.get("EventTypes") or "all",
                })
        return out

    def add_subscription(self, ip: str, destination: str,
                         context: str = "", event_types=None) -> Optional[dict]:
        """Register a push subscriber on the BMC at *ip*.

        Returns {"ok", "id"/"error", ...}, or None if no BMC at *ip*.
        """
        rdev = self._find(ip)
        if rdev is None:
            return None
        try:
            sub = rdev.register_subscription(
                destination, context=context, event_types=event_types)
        except rdev.SubscriptionError as e:
            return {"ok": False, "error": e.msg, "ip": ip}
        return {
            "ok": True,
            "ip": ip,
            "device": rdev.device.name,
            "id": sub["id"],
            "destination": sub["Destination"],
        }

    def remove_subscription(self, ip: str, sub_id: str) -> Optional[dict]:
        """Delete a subscription by id from the BMC at *ip*."""
        rdev = self._find(ip)
        if rdev is None:
            return None
        ok = rdev.unregister_subscription(sub_id)
        return {"ok": ok, "ip": ip, "id": sub_id}

    def submit_test_event(self, ip: str,
                          message: str = "Test event",
                          severity: str = "OK",
                          event_type: str = "Alert") -> Optional[dict]:
        """Fire a test event from the BMC at *ip* to all its subscribers."""
        rdev = self._find(ip)
        if rdev is None:
            return None
        sid = rdev.member_id
        rdev._deliver(severity, message, event_type,
                      "Simulator.1.0.TestEvent",
                      f"/redfish/v1/Systems/{sid}")
        return {
            "ok": True,
            "ip": ip,
            "device": rdev.device.name,
            "subscribers": len(rdev._subs),
        }
