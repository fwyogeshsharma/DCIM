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
import selectors
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
from core.bacnet_ev2_generator import DEFAULT_CIRCUITS, MAX_CIRCUITS, clamp_circuit_count
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

        # Guards the device/telemetry dicts against concurrent mutation: the recv
        # thread and the ticker iterate them while fleet-lifecycle commissioning
        # (add_plant_device / remove_plant_device) mutates them live.
        self._dev_lock = threading.RLock()
        # Set by a hot add/remove so the recv loop rebuilds its per-device socket
        # select set to include/drop the changed device.
        self._sockets_dirty = False

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

        # kWh register persistence — EV2 energy accumulators survive restarts the
        # way a real meter's non-volatile register does. Flushed periodically and
        # on stop(); restored (keyed by device IP) at start().
        self._energy_path = Path(self._datasets_dir) / "ev2_energy.json"
        self._tick_count = 0
        self._energy_save_every = 300   # ticks (~5 min at the 1 s tick interval)

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
    ) -> bool:
        """
        Bind sockets and start the recv thread for all device IPs.

        Returns True if the recv socket bound and the controller started,
        False on any failure (already running, no IPs, bind error). Callers
        must check this before reporting success to the UI.

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
            return False
        if not device_ips:
            self._log("[BACnet] No device IPs provided.", "error")
            return False

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
            hint = ""
            # WSAEACCES (10013) with SO_REUSEADDR set almost always means the
            # port is already owned by another process (e.g. a second copy of
            # this app, YABE, or another BACnet stack) rather than a true
            # permissions problem.
            if getattr(exc, "winerror", None) == 10013:
                hint = (" — port already in use by another process "
                        "(another instance of this app or a BACnet tool). "
                        "Close it, then start BACnet again.")
            self._log(
                f"[BACnet] Cannot open recv socket on port {port}: {exc}{hint}",
                "error")
            return False

        # Restore persisted kWh registers (keyed by device IP) so lifetime energy
        # continues across restarts instead of reseeding.
        saved_energy = self._load_energy()

        # Build devices (each device binds device_ip:BACNET_PORT with SO_REUSEADDR)
        for i, ip in enumerate(device_ips):
            instance    = base_instance + i
            device_name = f"Verdigris_EV2_{instance}"
            _entry = _cmap.get(ip, DEFAULT_CIRCUITS)
            if isinstance(_entry, tuple):
                capacity, active_circuits = _entry
            else:
                capacity = active_circuits = _entry  # legacy: all circuits active

            # capacity = the meter's physical CT-channel count (Verdigris EV2 ships
            # 24/42/84). Honor it up to the largest real model (84) rather than a
            # hard 42 cap, so an 84-channel meter on a large RPP builds an 84-circuit
            # object tree and meters every branch. Branches beyond the channel count
            # can't be clamped (physical limit) — the fleet spills them to a second
            # RPP+meter instead. load_scale keeps the synthetic/unpopulated fallback
            # proportional to a nominal 42-channel panel (unused once rated_kw lands).
            circuits         = clamp_circuit_count(min(capacity, MAX_CIRCUITS))
            active_circuits  = min(active_circuits, circuits)
            load_scale       = circuits / float(DEFAULT_CIRCUITS)
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
            _eng = EV2TelemetryEngine(
                circuits=circuits,
                frequency_hz=frequency_hz,
                active_circuits=active_circuits,
                load_scale=load_scale,
                rated_kw=_kwmap.get(ip),
            )
            if ip in saved_energy:
                _eng.set_energy_state(saved_energy[ip])
            self._telemetry[instance] = _eng

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

        return True

    def _load_energy(self) -> Dict[str, dict]:
        """Read persisted per-EV2 kWh registers ({ip: {panel_kwh, circuit_kwh}})."""
        try:
            import json
            if self._energy_path.exists():
                with open(self._energy_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as exc:
            self._log(f"[BACnet] Could not load kWh state ({exc}); starting fresh.",
                      "warning")
        return {}

    def _save_energy(self) -> None:
        """Flush every EV2 engine's kWh registers to disk atomically. Plant engines
        are skipped (they have no lifetime kWh register)."""
        out: Dict[str, dict] = {}
        with self._dev_lock:
            _by_ip = list(self._devices_by_ip.items())
        for ip, dev in _by_ip:
            eng = self._telemetry.get(getattr(dev, "device_instance", None))
            if isinstance(eng, EV2TelemetryEngine):
                out[ip] = eng.get_energy_state()
        if not out:
            return
        try:
            import json, os
            tmp = str(self._energy_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f)
            os.replace(tmp, str(self._energy_path))
        except Exception as exc:
            self._log(f"[BACnet] Could not save kWh state: {exc}", "warning")

    def stop(self) -> None:
        """Graceful shutdown: stop recv thread, close all sockets."""
        if not self._running:
            return

        # Final flush so the lifetime kWh registers survive this shutdown.
        self._save_energy()

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
    #  Hot add / remove (fleet lifecycle — commission new plant gear)
    # ─────────────────────────────────────────────────────────────

    def add_plant_device(self, ip: str, device_type: str,
                         name: Optional[str] = None,
                         rated_kw: float = 0.0) -> bool:
        """Hot-add a chiller-plant BACnet device (chiller/pump/cooling_tower/valve/
        crah/cdu) into a RUNNING controller — used when Fleet Lifecycle commissions
        new cooling gear. Mirrors the plant block in start(): builds the type's
        object tree + a live PlantTelemetryEngine and registers it, so it shows in
        the panel's Active Devices and answers BACnet reads. The IP must already be
        host-bound (fleet _commission binds it). Returns False if not running, the
        IP is unknown/duplicate, or the type is unrecognised."""
        if not self._running or not ip:
            return False
        with self._dev_lock:
            if ip in self._devices_by_ip:
                return False
            inst = (max(self._devices) + 1) if self._devices else self._base_instance
            name = name or f"{device_type}_{inst}"
            try:
                tree, n2k = build_plant_object_tree(device_type, float(rated_kw or 0.0))
            except KeyError:
                self._log(f"[BACnet] Unknown plant device_type '{device_type}' — skipped.",
                          "warning")
                return False
            try:
                dev = EV2BACnetDevice(
                    device_ip=ip, device_instance=inst, device_name=name,
                    log_cb=self._log_cb, port=self._port,
                    object_tree=tree, name_to_key=n2k, kind=f"plant:{device_type}")
            except Exception as exc:
                self._log(f"[BACnet] hot-add {device_type} @ {ip} failed: {exc}", "warning")
                return False
            self._devices[inst]      = dev
            self._devices_by_ip[ip]  = dev
            self._telemetry[inst]    = PlantTelemetryEngine(
                device_type, rated_kw=float(rated_kw or 0.0), seed=(hash(name) & 0xFFFFFFFF))
            self._sockets_dirty = True
        self._log(f"[BACnet] hot-added {device_type} {name} @ {ip} (instance {inst})",
                  "success")
        return True

    def add_ev2_device(self, ip: str, name: Optional[str] = None,
                       circuits: int = DEFAULT_CIRCUITS,
                       active_circuits: int = 0,
                       rated_kw: float = 0.0) -> bool:
        """Hot-add a Verdigris EV2 energy meter into a RUNNING controller — used when
        Fleet Lifecycle provisions a new RPP (+meter) as a panel fills or a hall
        opens. Mirrors the EV2 block in start(): builds the meter's object tree + a
        live EV2TelemetryEngine and registers it, so it shows in Active Devices and
        answers BACnet reads. active_circuits self-corrects each tick from the live
        branch map (see EV2TelemetryEngine.tick), so 0 at creation is fine — the
        panel starts empty and fills as rack PDUs wire onto it. The IP must already
        be host-bound (fleet _commission binds it). Returns False if not running, the
        IP is unknown/duplicate."""
        if not self._running or not ip:
            return False
        with self._dev_lock:
            if ip in self._devices_by_ip:
                return False
            inst = (max(self._devices) + 1) if self._devices else self._base_instance
            name = name or f"Verdigris_EV2_{inst}"
            circuits = clamp_circuit_count(min(circuits, MAX_CIRCUITS))
            active_circuits = min(active_circuits, circuits)
            try:
                dev = EV2BACnetDevice(
                    device_ip=ip, device_instance=inst, device_name=name,
                    circuits=circuits, log_cb=self._log_cb, port=self._port)
            except Exception as exc:
                self._log(f"[BACnet] hot-add EV2 @ {ip} failed: {exc}", "warning")
                return False
            self._devices[inst]      = dev
            self._devices_by_ip[ip]  = dev
            self._telemetry[inst]    = EV2TelemetryEngine(
                circuits=circuits, frequency_hz=self._frequency_hz,
                active_circuits=active_circuits,
                load_scale=circuits / float(DEFAULT_CIRCUITS),
                rated_kw=(rated_kw or None))
            self._sockets_dirty = True
        self._log(f"[BACnet] hot-added EV2 {name} @ {ip} "
                  f"(instance {inst}, {circuits}ch)", "success")
        return True

    def remove_plant_device(self, ip: str) -> None:
        """Tear down a hot-added plant device (fleet decommission)."""
        with self._dev_lock:
            dev = self._devices_by_ip.pop(ip, None)
            if dev is None:
                return
            inst = getattr(dev, "device_instance", None)
            if inst is not None:
                self._devices.pop(inst, None)
                self._telemetry.pop(inst, None)
            self._sockets_dirty = True
        try:
            dev.close()
        except Exception:
            pass

    # EV2 teardown is the same IP-keyed removal as a plant device.
    remove_ev2_device = remove_plant_device

    # ─────────────────────────────────────────────────────────────
    #  Telemetry tick (called by DeviceStateStore)
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    def _flag_key(kind: str, point: str) -> str:
        """Map a telemetry point to its Metrics-Tick flag key.

        Plant devices use per-point keys ("<type>:<PointName>"); EV2 panels
        group many points under a handful of flags.
        """
        if kind.startswith("plant:"):
            return f"{kind.split(':', 1)[1]}:{point}"
        if point.startswith("Alarm_"):
            return "ev2_alarms"
        if point.endswith("_kWh"):
            return "ev2_energy"
        if point.startswith("Ckt"):
            return "ev2_circuits"
        if "THD" in point or point.startswith("Harmonic_"):
            return "ev2_power_quality"
        if point in ("Line_Frequency", "Panel_PF"):
            return "ev2_freq_pf"
        return "ev2_power"

    @staticmethod
    def _limit_key(kind: str, point: str) -> str:
        """Per-point key for the Limits tab. Plant: "<type>:<Point>"; EV2: "ev2:<Point>"."""
        if kind.startswith("plant:"):
            return f"{kind.split(':', 1)[1]}:{point}"
        return f"ev2:{point}"

    @staticmethod
    def _apply_limits(kind: str, values: dict, limits: dict) -> dict:
        """Clamp numeric points / force binary points per enabled limits.

        Numeric limit = {min, max} (clamp). Binary force = {lock, options}
        where lock "on" → 1.0, anything else → 0.0.
        """
        out = {}
        for k, v in values.items():
            lim = limits.get(BACnetController._limit_key(kind, k))
            if lim and lim.get("enabled"):
                if lim.get("options"):                       # binary force
                    v = 1.0 if str(lim.get("lock")) == "on" else 0.0
                else:                                        # numeric clamp
                    lo, hi = lim.get("min"), lim.get("max")
                    if lo is not None and v < lo:
                        v = float(lo)
                    if hi is not None and v > hi:
                        v = float(hi)
            out[k] = v
        return out

    def tick(self, dt: float, metric_flags: dict | None = None,
             metric_limits: dict | None = None,
             plant_overrides: dict | None = None,
             live_kw_by_ip: dict | None = None,
             circuit_kw_by_ip: dict | None = None,
             plant_power_by_name: dict | None = None,
             plant_cop_by_name: dict | None = None,
             plant_loadfrac_by_name: dict | None = None,
             plant_heat_by_name: dict | None = None,
             plant_standby_names: set | None = None,
             plant_unpowered_names: set | None = None) -> None:
        """
        Advance all EV2 telemetry engines by *dt* seconds.

        Applies updated values to BACnet object trees and dispatches
        any pending COV notifications. When *metric_flags* is given, points
        whose flag is False are dropped from the update (frozen at last value).
        When *metric_limits* is given, enabled points are clamped (numeric) or
        forced (binary) before the update. When *plant_overrides* is given
        ({device_name: {point: value}}) those per-device binary forces are applied
        last — this is how a single plant unit is alarmed from its Metric Tick
        window, independent of the type-wide locks.
        """
        if not self._running:
            return
        with self._dev_lock:
            _tel_items = list(self._telemetry.items())
        for instance, engine in _tel_items:
            dev = self._devices.get(instance)
            if dev is None:
                continue
            try:
                kind = getattr(dev, "kind", "ev2")
                # Keyed by device IP — stable across renames (the BACnet device's
                # name is fixed at creation, so name-matching would break on edit).
                ovr = (plant_overrides or {}).get(getattr(dev, "device_ip", ""), {})
                # Manual alarm triggers: any plant binary the Limits tab locks
                # "on" (type-wide) OR this device's per-device override drives that
                # device's coupled fault physics (pressure/flow/temp shifts, and for
                # the CDU leak the downstream CPU heat) — not just the forced bit.
                if kind.startswith("plant:"):
                    forced = set()
                    if metric_limits:
                        dtype = kind.split(":")[-1]          # plant:cdu -> cdu
                        pref = dtype + ":"
                        for _k, _lim in metric_limits.items():
                            if (_k.startswith(pref) and _lim.get("enabled")
                                    and str(_lim.get("lock")) == "on"):
                                forced.add(_k[len(pref):])
                    forced |= {p for p, v in ovr.items()
                               if p.startswith("Alarm_") and float(v) >= 0.5}
                    _nm = getattr(dev, "device_name", "")
                    _pw = (plant_power_by_name or {}).get(_nm)
                    _cop = (plant_cop_by_name or {}).get(_nm)
                    _lf = (plant_loadfrac_by_name or {}).get(_nm)
                    _hl = (plant_heat_by_name or {}).get(_nm)
                    values = engine.tick(dt, forced=forced, live_power=_pw,
                                         live_cop=_cop, plant_load_frac=_lf,
                                         live_heat=_hl)
                    # Staged-OFF (standby) chiller / pump / tower cell: sequenced down
                    # by the BMS (lead/lag), not faulted. Report the unit STOPPED and
                    # its dynamic signals at rest — so a staged-off unit reads "off",
                    # not "running at 75 % speed" — without tripping the cooling-loss
                    # penalty (the store excludes standby names from that calc).
                    # An UNPOWERED unit (its MCC is dead — utility lost and the ATS
                    # has not yet re-energized that mechanical load block) looks
                    # identical on the wire: stopped, no draw, no flow. The
                    # difference is upstream: the store excludes standby names from
                    # the cooling-loss penalty but NOT unpowered ones, because a
                    # unit that isn't turning genuinely isn't rejecting heat.
                    _off = ((plant_standby_names and _nm in plant_standby_names)
                            or (plant_unpowered_names and _nm in plant_unpowered_names))
                    if _off:
                        for _pt in list(values.keys()):
                            if _pt in ("Chiller_Running", "Run_Status",
                                       "Fan_Status", "Unit_Running"):
                                values[_pt] = 0.0            # stopped
                            elif any(_k in _pt for _k in ("Power", "Speed", "Flow",
                                                          "Frequency", "Load", "Capacity",
                                                          "Position", "Valve", "Vibration")):
                                values[_pt] = 0.0            # no draw/flow/rotation/valve
                        # A stopped unit does no thermodynamic work: efficiency is
                        # undefined (COP→0), the temperature ΔT collapses (no flow →
                        # no heat exchange, so supply≈return), and the refrigerant
                        # pressures equalize. Sensors still read — just with no ΔT —
                        # so the row shows "idle", not fake operating values. Setpoints
                        # and run-hour counters persist (config / cumulative).
                        if "COP" in values:
                            values["COP"] = 0.0
                        for _a, _b in (("CHW_Supply_Temp", "CHW_Return_Temp"),
                                       ("Cond_Supply_Temp", "Cond_Return_Temp"),
                                       ("Supply_Air_Temp", "Return_Air_Temp"),
                                       ("TCS_Supply_Temp", "TCS_Return_Temp"),
                                       ("Cond_Water_In", "Cond_Water_Out"),
                                       ("Evap_Pressure", "Cond_Pressure")):
                            if _a in values and _b in values:
                                _m = round((values[_a] + values[_b]) / 2.0, 2)
                                values[_a] = values[_b] = _m
                        # A stopped pump develops no head: differential → 0 and the
                        # discharge falls back to the suction (standby header) pressure.
                        if "Diff_Pressure" in values:
                            values["Diff_Pressure"] = 0.0
                        if "Discharge_Pressure" in values and "Suction_Pressure" in values:
                            values["Discharge_Pressure"] = values["Suction_Pressure"]
                        # A stopped motor cools toward mechanical-room ambient.
                        if "Motor_Temp" in values:
                            values["Motor_Temp"] = 25.0
                else:
                    # EV2 energy meter: drive the panel from the live downstream
                    # load (server→PDU→panel) when available, else its own curve.
                    # Per-circuit loads (one per clamped branch PDU) make each
                    # circuit meter its real branch draw instead of a random walk.
                    _ip = getattr(dev, "device_ip", "")
                    _lkw = (live_kw_by_ip or {}).get(_ip)
                    _ckw = (circuit_kw_by_ip or {}).get(_ip)
                    values = engine.tick(dt, live_kw=_lkw, circuit_kw=_ckw)
                if metric_flags:
                    values = {
                        k: v for k, v in values.items()
                        if metric_flags.get(self._flag_key(kind, k), True)
                    }
                if metric_limits:
                    values = self._apply_limits(kind, values, metric_limits)
                # Per-device binary overrides win last (alarm on = 1.0, unit off = 0.0).
                for _p, _v in ovr.items():
                    if _p in values:
                        values[_p] = float(_v)
                dev.update_present_values(values)
                dev.dispatch_cov_notifications(
                    send_sock_fallback=self._recv_sock
                )
            except Exception:
                log.exception("[BACnet] tick error for instance %d", instance)

        # Periodic kWh flush so the lifetime registers survive an unclean exit.
        self._tick_count += 1
        if self._energy_save_every and self._tick_count % self._energy_save_every == 0:
            self._save_energy()

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

        # Build socket → device map for per-device sockets. Rebuilt whenever a
        # fleet hot add/remove flips _sockets_dirty, so a newly commissioned
        # device's socket joins the select set (and a removed one drops out).
        def _build_sockmap():
            with self._dev_lock:
                self._sockets_dirty = False
                s2d = {dev._send_sock: dev
                       for dev in self._devices.values()
                       if dev._send_sock is not None}
            return s2d, [wildcard] + list(s2d.keys())

        # Use a selectors poller (epoll/kqueue on POSIX) rather than select():
        # a large fleet opens per-device sockets whose fd numbers exceed
        # FD_SETSIZE (1024), which select() cannot represent — it raises
        # "filedescriptor out of range in select()". epoll has no such ceiling.
        sel = selectors.DefaultSelector()

        def _reregister(socks):
            for key in list(sel.get_map().values()):
                try: sel.unregister(key.fileobj)
                except (KeyError, ValueError): pass
            for s in socks:
                try: sel.register(s, selectors.EVENT_READ)
                except (KeyError, ValueError, OSError): pass

        sock_to_dev, all_socks = _build_sockmap()
        _reregister(all_socks)

        try:
            while not self._stop_ev.is_set():
                if self._sockets_dirty:
                    sock_to_dev, all_socks = _build_sockmap()
                    _reregister(all_socks)
                try:
                    events = sel.select(timeout=1.0)
                except OSError:
                    break
                for key, _mask in events:
                    sock = key.fileobj
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
        finally:
            sel.close()

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
                    with self._dev_lock:
                        _devs = list(self._devices.values())
                    for dev in _devs:
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
        with self._dev_lock:
            _devs = list(self._devices.values())
        for dev in _devs:
            result.extend(dev.get_all_subscribers())
        return result

    def get_cov_events(self) -> List[dict]:
        result = []
        with self._dev_lock:
            _devs = list(self._devices.values())
        for dev in _devs:
            result.extend(dev.get_cov_events())
        return result[-100:]

    def get_device_summary(self) -> List[dict]:
        """Return list of {ip, instance, name, circuits} for the UI table."""
        result = []
        with self._dev_lock:
            _devs = list(self._devices.values())
        for dev in _devs:
            result.append({
                "ip":       dev.device_ip,
                "instance": dev.device_instance,
                "name":     dev.device_name,
                "circuits": dev.circuits,
                "kind":     getattr(dev, "kind", "ev2"),
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
        with self._dev_lock:
            _dev_items = list(self._devices.items())
        for instance, dev in _dev_items:
            result.append({
                "ip":       dev.device_ip,
                "instance": instance,
                "name":     dev.device_name,
                "circuits": dev.circuits,
                "kind":     getattr(dev, "kind", "ev2"),
                "values":   dev.get_snapshot(),
                # Static nameplate — the panel's rated per-phase current + rated kW,
                # so the UI can colour phase currents and Total kW relative to panel
                # size (not a flat limit). None for plant devices (no EV2 engine).
                "rated_current": getattr(self._telemetry.get(instance), "_rated_phase_current", None),
                "rated_kw":      getattr(self._telemetry.get(instance), "_rated_capacity_kw", None),
            })
        return result