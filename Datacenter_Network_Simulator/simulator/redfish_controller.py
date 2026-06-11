"""
Redfish Controller — lifecycle manager for all simulated server BMCs.

Mirrors SNMPSimController / GNMIController / BACnetController so the UI and
REST API treat Redfish like every other protocol.

Architecture
------------
• One stdlib ThreadingHTTPServer per server, bound to that server's IP on the
  configured port (default 443).  Binding per-IP (not 0.0.0.0) means each BMC
  answers only on its own address, exactly like real hardware.
• Each server serves the Redfish tree for ONE Device via a RedfishDevice
  attached to the HTTPServer instance (``server.redfish_device``).
• Resource bodies are built on demand from the live Device object, so values
  track the DeviceStateStore ticker — no per-tick push needed for MVP.

Plain HTTP only in this MVP (no TLS).  Clients hit ``http://<ip>:443/redfish/v1/``.

Usage::

    ctrl = RedfishController()
    ctrl.set_log_callback(lambda msg, lvl: console.log(msg, lvl))
    ctrl.set_ready_callback(lambda: panel.on_ready())
    ctrl.start(devices=[srv1, srv2], port=443)
    ...
    ctrl.stop()
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from simulator.redfish_device import RedfishDevice

if TYPE_CHECKING:
    from core.device_manager import Device

log = logging.getLogger(__name__)


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
        self.send_response(status)
        self.send_header("OData-Version", "4.0")
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
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

    def __init__(self):
        self._log_cb:   Optional[Callable[[str, str], None]] = None
        self._ready_cb: Optional[Callable[[], None]]         = None

        # ip -> (HTTPServer, thread, RedfishDevice)
        self._servers: Dict[str, tuple] = {}
        self._running = False

        # Config snapshot for the status endpoint.
        self._port = 443
        self._username = "admin"
        self._password = "password"

    # ── callbacks ──────────────────────────────────────────────────────────
    def set_log_callback(self, cb: Callable[[str, str], None]):
        """cb(message, level)  level ∈ {"info","success","warning","error"}"""
        self._log_cb = cb

    def set_ready_callback(self, cb: Callable[[], None]):
        self._ready_cb = cb

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

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(
        self,
        devices: List["Device"],
        port: int = 443,
        username: str = "admin",
        password: str = "password",
        ip_for: Optional[Callable[["Device"], str]] = None,
    ) -> bool:
        """
        Bind one HTTP server per server device and begin serving Redfish.

        devices:  list of Device objects (DeviceType.SERVER).
        port:     TCP port for every BMC (default 443).
        ip_for:   optional fn mapping a Device → bind IP. Defaults to
                  ``device.mgmt_ip or device.ip_address`` (BMC on OOB net).
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
        if ip_for is None:
            ip_for = lambda d: (d.mgmt_ip or d.ip_address)  # noqa: E731

        started, failed = 0, 0
        for dev in devices:
            ip = ip_for(dev)
            if not ip:
                continue
            rdev = RedfishDevice(dev, username=username, password=password)
            try:
                httpd = ThreadingHTTPServer((ip, port), _RedfishHandler)
            except OSError as exc:
                failed += 1
                self._log(f"[Redfish] {ip}:{port} bind failed — {exc}", "warning")
                continue
            httpd.redfish_device = rdev          # type: ignore[attr-defined]
            httpd.daemon_threads = True
            t = threading.Thread(
                target=httpd.serve_forever,
                kwargs={"poll_interval": 0.5},
                daemon=True,
                name=f"Redfish-{ip}",
            )
            t.start()
            self._servers[ip] = (httpd, t, rdev)
            started += 1

        if started == 0:
            self._log("[Redfish] No BMC endpoints could be started.", "error")
            self._running = False
            return False

        self._running = True
        msg = (f"[Redfish] Started — {started} BMC endpoint(s) on port {port}"
               + (f" ({failed} bind failure(s))" if failed else "") + ".")
        self._log(msg, "success")
        if self._ready_cb:
            try:
                self._ready_cb()
            except Exception:
                pass
        return True

    def stop(self):
        if not self._running:
            return
        for ip, (httpd, t, _rdev) in list(self._servers.items()):
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
            t.join(timeout=2.0)
        self._servers.clear()
        self._running = False
        self._log("[Redfish] Stopped.", "info")

    # ── reporting ──────────────────────────────────────────────────────────
    def get_device_summary(self) -> List[dict]:
        """Per-BMC rows for the UI table."""
        rows = []
        for ip, (_httpd, _t, rdev) in self._servers.items():
            d = rdev.device
            rows.append({
                "name":    d.name,
                "ip":      ip,
                "port":    self._port,
                "vendor":  d.vendor.value,
                "model":   d.model_name or d.vendor.value,
                "url":     f"http://{ip}:{self._port}/redfish/v1/",
                "sessions": len(rdev.session_list()),
                "status":  "Active" if self._running else "Stopped",
            })
        return rows

    def get_sessions(self) -> List[dict]:
        """All active sessions across every BMC."""
        out = []
        for ip, (_httpd, _t, rdev) in self._servers.items():
            for s in rdev.session_list():
                out.append({"ip": ip, "device": rdev.device.name, **s})
        return out
