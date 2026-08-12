"""Modbus/TCP Controller — lifecycle manager for the facility electrical plane.

Mirrors the BACnetController / GNMIController / SNMPSimController interface so
AppState and the UI treat every protocol uniformly.

Architecture
------------
• ONE listening socket on 0.0.0.0:502. Accepted connections are routed to a
  device by the address the client actually reached — `conn.getsockname()[0]` is
  the local end of the connection, which is the aliased device IP. This is the
  TCP analogue of the BACnet controller's shared recv socket, and it matters at
  fleet scale: 30 slaves cost one listening fd, not thirty.
• A `selectors` loop services established connections. Not thread-per-connection:
  a BMS holding a persistent poll socket per device would otherwise cost one
  thread per device forever.
• Native-TCP devices answer on their own IP with their configured unit id.
  Gateways own an IP and route by unit id to RTU slaves behind them.

Telemetry
---------
`tick(dt, ...)` is called by DeviceStateStore._tick(), next to the BACnet tick.
Each slave re-renders its register banks from the SAME `ext` dict that
snmprec_generator renders into SNMP OIDs, so the two planes cannot disagree.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
1. No unsolicited messaging. Modbus has no traps and no COV — the master polls.
   Nothing here may be wired to TrapEngine.

2. No accumulator persistence. The non-volatile registers (kWh, run hours,
   transfer counts) SHOULD survive a restart the way a real meter's do, and the
   BACnet controller does exactly that for EV2 energy. It is not done here on
   purpose: those values live in the store's `ext`, which is also what SNMP
   serves. Persisting them in this controller would make the Modbus plane report
   a different number from the SNMP plane for the same quantity, which is a worse
   failure than a reset accumulator — a divergence between planes is invisible
   until someone trends both. If restart-persistence is wanted it belongs in
   DeviceStateStore, once, for every plane.
"""
from __future__ import annotations

import logging
import selectors
import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.modbus_register_map import (
    MODBUS_DEVICE_TYPES, get_map, get_probe_map,
)
from simulator.modbus_device import (
    ModbusSlave, EX_GATEWAY_PATH, EX_GATEWAY_NO_RESPONSE,
)

log = logging.getLogger(__name__)

MODBUS_PORT = 502

# MBAP header: transaction id, protocol id, length, unit id.
_MBAP = struct.Struct(">HHHB")
_MBAP_LEN = 7
_MAX_PDU = 253

# Real gear accepts a handful of concurrent masters, not an unbounded number.
# Vertiv/Schneider cards typically cap at 4–8; exceeding it gets you refused.
MAX_CONNS_PER_DEVICE = 8

# RS-485 trunk timing behind a gateway. At 19200 baud a request/response pair of
# ~20 registers is roughly 40–60 ms on the wire, and the gateway serialises the
# whole trunk — which is why an 18-slave trunk cannot be polled in under a couple
# of seconds. Native TCP devices skip this entirely.
SERIAL_TXN_SECONDS = 0.055


class _Conn:
    """One accepted TCP connection and its receive buffer."""
    __slots__ = ("sock", "peer", "local_ip", "buf")

    def __init__(self, sock: socket.socket, peer, local_ip: str):
        self.sock = sock
        self.peer = peer
        self.local_ip = local_ip
        self.buf = bytearray()


class ModbusController:
    """Start, stop and tick all simulated Modbus/TCP servers."""

    def __init__(self, datasets_dir: str = "datasets/modbus"):
        self._datasets_dir = datasets_dir

        self._log_cb:   Optional[Callable[[str, str], None]] = None
        self._ready_cb: Optional[Callable[[], None]]         = None
        self._write_cb: Optional[Callable[[str, str, float], bool]] = None

        # Native-TCP slaves, keyed by their own IP.
        self._by_ip: Dict[str, ModbusSlave] = {}
        # Gateways: gateway IP -> {unit_id: slave}
        self._gateways: Dict[str, Dict[int, ModbusSlave]] = {}
        # Every slave by name, for tick/introspection.
        self._by_name: Dict[str, ModbusSlave] = {}

        # Guards all four dicts. The accept/recv loop reads them while fleet
        # commissioning mutates them live — same hazard BACnet has.
        self._dev_lock = threading.RLock()

        self._listener: Optional[socket.socket] = None
        self._sel: Optional[selectors.BaseSelector] = None
        self._conns: Dict[int, _Conn] = {}
        self._conn_count: Dict[str, int] = {}

        # Responses held back to model RS-485 trunk latency:
        # [(due_monotonic, fileno, payload)] per gateway IP, served in order.
        self._pending: List[Tuple[float, int, bytes]] = []
        self._trunk_free_at: Dict[str, float] = {}

        self._thread: Optional[threading.Thread] = None
        self._stop_ev = threading.Event()
        self._running = False
        self._port = MODBUS_PORT

        self.stats = {"requests": 0, "exceptions": 0, "connections": 0,
                      "refused": 0}

    # ── Callbacks ────────────────────────────────────────────────────────
    def set_log_callback(self, cb): self._log_cb = cb
    def set_ready_callback(self, cb): self._ready_cb = cb

    def set_write_callback(self, cb: Callable[[str, str, float], bool]):
        """cb(device_name, write_action, value) -> accepted?

        Must route into the same override channel the API and Simulate-Fault menu
        use. Anything else gets clobbered by the next tick.
        """
        self._write_cb = cb

    def _log(self, msg: str, level: str = "info"):
        if self._log_cb:
            try:
                self._log_cb(msg, level)
            except Exception:
                pass
        log.info("[Modbus] %s", msg)

    # ─────────────────────────────────────────────────────────────────────
    #  Lifecycle
    # ─────────────────────────────────────────────────────────────────────
    def start(self, devices: List[dict], port: int = MODBUS_PORT,
              write_enabled: bool = False) -> bool:
        """devices: [{ip, name, device_type, unit_id?, role?, gateway_ip?}]

        role: "server" (native TCP, default) | "gateway" | "rtu_slave".
        An rtu_slave carries gateway_ip and is reached only through it.
        """
        if self._running:
            return True

        self._port = int(port or MODBUS_PORT)
        with self._dev_lock:
            self._by_ip.clear()
            self._gateways.clear()
            self._by_name.clear()

        for d in devices:
            self._install(d, write_enabled)

        with self._dev_lock:
            n_tcp = len(self._by_ip)
            n_gw = len(self._gateways)
            n_rtu = sum(len(v) for v in self._gateways.values())
        if not (n_tcp or n_gw):
            self._log("No Modbus-capable devices to serve", "warning")
            return False

        try:
            lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            lsock.bind(("0.0.0.0", self._port))
            lsock.listen(128)
            lsock.setblocking(False)
        except OSError as e:
            # 13/EACCES on Linux means the port is privileged; 10013 on Windows
            # is usually a second copy of the app (see app/single_instance.py) or
            # a WinNAT reservation.
            self._log(f"Could not bind port {self._port}: {e}", "error")
            return False

        self._listener = lsock
        self._sel = selectors.DefaultSelector()
        self._sel.register(lsock, selectors.EVENT_READ, data=None)
        self._stop_ev.clear()
        self._thread = threading.Thread(target=self._serve_loop,
                                        name="modbus-serve", daemon=True)
        self._thread.start()
        self._running = True

        self._log(f"Started on :{self._port} — {n_tcp} TCP server(s), "
                  f"{n_gw} gateway(s), {n_rtu} RTU slave(s)", "success")
        if self._ready_cb:
            try:
                self._ready_cb()
            except Exception:
                pass
        return True

    def _install(self, d: dict, write_enabled: bool) -> Optional[ModbusSlave]:
        dtype = str(d.get("device_type") or "")
        role  = str(d.get("role") or "server")
        name  = str(d.get("name") or "")
        ip    = str(d.get("ip") or "")

        if role == "gateway":
            with self._dev_lock:
                # An IP is either a device or a gateway, never both: _dispatch
                # resolves direct devices first, so a collision would silently
                # make the whole trunk behind this address unreachable.
                if ip in self._by_ip:
                    self._log(f"{name}: {ip} is already a native Modbus server — "
                              f"gateway not installed", "error")
                    return None
                self._gateways.setdefault(ip, {})
            return None

        # A field transmitter is identified by its plant-header ROLE, not by its
        # device_type — every one of them is DeviceType.SENSOR, and so is every
        # rack air probe. The role comes from the name prefix (CHWS/FLOW/...),
        # exactly as _probe_role derives it for the SNMP side.
        probe_role = str(d.get("probe_role") or "")
        mmap = get_probe_map(probe_role) if probe_role else get_map(dtype)
        if mmap is None:
            return None

        slave = ModbusSlave(
            name=name, device_type=dtype, mmap=mmap,
            unit_id=int(d.get("unit_id") or 1), ip=ip,
            write_cb=self._write_cb,
        )
        slave.write_enabled = bool(write_enabled and mmap.write_enabled)

        with self._dev_lock:
            if role == "rtu_slave":
                gw = str(d.get("gateway_ip") or "")
                if not gw:
                    return None
                self._gateways.setdefault(gw, {})[slave.unit_id] = slave
            else:
                self._by_ip[ip] = slave
            self._by_name[name] = slave
        return slave

    def stop(self):
        if not self._running:
            return
        self._stop_ev.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        for c in list(self._conns.values()):
            try:
                c.sock.close()
            except OSError:
                pass
        self._conns.clear()
        self._conn_count.clear()
        self._pending.clear()
        if self._sel:
            try:
                self._sel.close()
            except Exception:
                pass
        if self._listener:
            try:
                self._listener.close()
            except OSError:
                pass
        self._listener = None
        self._sel = None
        self._running = False
        self._log("Stopped", "info")

    def is_running(self) -> bool:
        return self._running

    def is_ready(self) -> bool:
        """Readiness is a bound socket, not a flag — an unbound simulator that
        reports ready is how a restart silently serves nothing."""
        return bool(self._running and self._listener)

    # ─────────────────────────────────────────────────────────────────────
    #  Serving
    # ─────────────────────────────────────────────────────────────────────
    def _serve_loop(self):
        while not self._stop_ev.is_set():
            timeout = 0.2
            if self._pending:
                timeout = max(0.0, min(timeout, self._pending[0][0] - time.monotonic()))
            try:
                events = self._sel.select(timeout=timeout)
            except OSError:
                break
            for key, _mask in events:
                if key.data is None:
                    self._accept()
                else:
                    self._readable(key.data)
            self._flush_pending()

    def _accept(self):
        try:
            conn, peer = self._listener.accept()
        except OSError:
            return
        local_ip = conn.getsockname()[0]

        with self._dev_lock:
            known = local_ip in self._by_ip or local_ip in self._gateways
        if not known:
            # The listener is on 0.0.0.0, so it also answers on the host's own
            # LAN address. Nothing is bound there — close rather than pretend.
            try:
                conn.close()
            except OSError:
                pass
            self.stats["refused"] += 1
            return

        if self._conn_count.get(local_ip, 0) >= MAX_CONNS_PER_DEVICE:
            try:
                conn.close()
            except OSError:
                pass
            self.stats["refused"] += 1
            return

        conn.setblocking(False)
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        c = _Conn(conn, peer, local_ip)
        self._conns[conn.fileno()] = c
        self._conn_count[local_ip] = self._conn_count.get(local_ip, 0) + 1
        self._sel.register(conn, selectors.EVENT_READ, data=c)
        self.stats["connections"] += 1

    def _close(self, c: _Conn):
        try:
            self._sel.unregister(c.sock)
        except (KeyError, ValueError, OSError):
            pass
        self._conns.pop(c.sock.fileno(), None)
        n = self._conn_count.get(c.local_ip, 1) - 1
        if n <= 0:
            self._conn_count.pop(c.local_ip, None)
        else:
            self._conn_count[c.local_ip] = n
        try:
            c.sock.close()
        except OSError:
            pass

    def _readable(self, c: _Conn):
        try:
            data = c.sock.recv(4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self._close(c)
            return
        if not data:
            self._close(c)
            return

        c.buf += data
        # A TCP stream is not a message boundary. Frames arrive split, and
        # several arrive coalesced — both are normal, and a master is entitled to
        # pipeline. Drain everything the buffer completely holds.
        while len(c.buf) >= _MBAP_LEN:
            txn, proto, length, unit = _MBAP.unpack_from(c.buf, 0)
            if proto != 0:
                self._close(c)
                return
            if length < 1 or length > _MAX_PDU + 1:
                self._close(c)
                return
            total = 6 + length
            if len(c.buf) < total:
                break                      # partial frame; wait for the rest
            pdu = bytes(c.buf[_MBAP_LEN:total])
            del c.buf[:total]
            self._dispatch(c, txn, unit, pdu)

    def _dispatch(self, c: _Conn, txn: int, unit: int, pdu: bytes):
        self.stats["requests"] += 1
        with self._dev_lock:
            direct = self._by_ip.get(c.local_ip)
            trunk = self._gateways.get(c.local_ip)

        delay = 0.0
        if direct is not None:
            if not direct.accept_unit(unit):
                resp = bytes([pdu[0] | 0x80, EX_GATEWAY_PATH])
            elif not direct.online:
                # A native-TCP device with no power has no comm card either, so
                # in reality the socket would never have connected. It can only
                # be reached here if it died mid-session — drop the connection,
                # which is what the master will observe.
                self._close(c)
                return
            else:
                resp = direct.handle_pdu(pdu)
        elif trunk is not None:
            slave = trunk.get(unit)
            if slave is None:
                # The gateway is alive; nothing answers at that unit id.
                resp = bytes([pdu[0] | 0x80, EX_GATEWAY_PATH])
            elif not slave.online:
                # THE distinguishing Modbus failure: the network path is fine,
                # the field device is not. No other plane in this simulator can
                # express that.
                resp = bytes([pdu[0] | 0x80, EX_GATEWAY_NO_RESPONSE])
            else:
                resp = slave.handle_pdu(pdu)
            delay = self._trunk_delay(c.local_ip)
        else:
            return

        if resp and resp[0] & 0x80:
            self.stats["exceptions"] += 1

        frame = _MBAP.pack(txn, 0, len(resp) + 1, unit) + resp
        if delay > 0:
            self._pending.append((time.monotonic() + delay, c.sock.fileno(), frame))
            self._pending.sort(key=lambda t: t[0])
        else:
            self._send(c, frame)

    def _trunk_delay(self, gw_ip: str) -> float:
        """Serialise the RS-485 trunk. Concurrent requests to different units
        behind one gateway queue behind each other, exactly as they do on a real
        multidrop bus — which is why polling 18 slaves is a seconds-scale job."""
        now = time.monotonic()
        free_at = max(self._trunk_free_at.get(gw_ip, now), now)
        self._trunk_free_at[gw_ip] = free_at + SERIAL_TXN_SECONDS
        return (free_at + SERIAL_TXN_SECONDS) - now

    def _flush_pending(self):
        if not self._pending:
            return
        now = time.monotonic()
        still: List[Tuple[float, int, bytes]] = []
        for due, fd, frame in self._pending:
            if due > now:
                still.append((due, fd, frame))
                continue
            c = self._conns.get(fd)
            if c is not None:
                self._send(c, frame)
        self._pending = still

    def _send(self, c: _Conn, frame: bytes):
        try:
            c.sock.sendall(frame)
        except OSError:
            self._close(c)

    # ─────────────────────────────────────────────────────────────────────
    #  Telemetry tick
    # ─────────────────────────────────────────────────────────────────────
    def tick(self, dt: float, unpowered_names: Optional[set] = None):
        """Re-render every slave's banks from the store's live ext state."""
        if not self._running:
            return
        try:
            from core.device_state_store import _get_ext_state
        except Exception:
            return

        dead = unpowered_names or set()
        with self._dev_lock:
            slaves = list(self._by_name.values())
        for s in slaves:
            try:
                s.set_online(s.name not in dead)
                s.apply_ext(_get_ext_state(s.name))
            except Exception:
                log.exception("[Modbus] tick failed for %s", s.name)

    # ─────────────────────────────────────────────────────────────────────
    #  Fleet hot add / remove
    # ─────────────────────────────────────────────────────────────────────
    def add_device(self, ip: str, device_type: str, name: str,
                   unit_id: int = 1, role: str = "server",
                   gateway_ip: str = "") -> bool:
        if device_type not in MODBUS_DEVICE_TYPES and role != "gateway":
            return False
        s = self._install({"ip": ip, "device_type": device_type, "name": name,
                           "unit_id": unit_id, "role": role,
                           "gateway_ip": gateway_ip}, write_enabled=False)
        if s is not None or role == "gateway":
            self._log(f"Commissioned {name} ({device_type}) at {ip}", "info")
            return True
        return False

    def remove_device(self, ip: str = "", name: str = "") -> None:
        with self._dev_lock:
            if ip and ip in self._by_ip:
                s = self._by_ip.pop(ip)
                self._by_name.pop(s.name, None)
            if ip and ip in self._gateways:
                for s in self._gateways.pop(ip).values():
                    self._by_name.pop(s.name, None)
            if name and name in self._by_name:
                s = self._by_name.pop(name)
                self._by_ip.pop(s.ip, None)
                for trunk in self._gateways.values():
                    trunk.pop(s.unit_id, None)

    # ─────────────────────────────────────────────────────────────────────
    #  Introspection
    # ─────────────────────────────────────────────────────────────────────
    def device_count(self) -> int:
        with self._dev_lock:
            return len(self._by_name)

    def get_device_summary(self) -> List[dict]:
        with self._dev_lock:
            slaves = list(self._by_name.values())
            gw_of = {s.name: gw for gw, trunk in self._gateways.items()
                     for s in trunk.values()}
        out = []
        for s in slaves:
            row = s.summary()
            row["role"] = "rtu_slave" if s.name in gw_of else "server"
            row["gateway_ip"] = gw_of.get(s.name, "")
            out.append(row)
        out.sort(key=lambda r: (r["role"], r["name"]))
        return out

    def get_slave(self, name: str) -> Optional[ModbusSlave]:
        with self._dev_lock:
            return self._by_name.get(name)

    def set_write_enabled(self, enabled: bool, name: str = "") -> int:
        """Arm or disarm the write path. Returns how many slaves changed."""
        n = 0
        with self._dev_lock:
            targets = ([self._by_name[name]] if name and name in self._by_name
                       else list(self._by_name.values()))
        for s in targets:
            # Only maps that declare a writable point can be armed at all.
            if enabled and not s.map.points.get("coil") and not any(
                    p.writable for pts in s.map.points.values() for p in pts):
                continue
            s.write_enabled = bool(enabled)
            n += 1
        return n
