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
  cpu_usage     ±4 pp normal; 1% spike to >90%; gradual 3-8pp/tick recovery
  memory_used   ±swing normal; 0.5% spike to >85%; gradual 3-6%/tick recovery
  sys_uptime    += tick_interval × 100  (centiseconds)
  iface counters: random increments; rare error increments
"""
from __future__ import annotations

import logging
import random
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, TYPE_CHECKING

from core.device_manager import DeviceType

if TYPE_CHECKING:
    from core.device_manager import Device, DeviceManager
    from core.topology_engine import TopologyEngine
    from simulator.snmpsim_controller import SNMPSimController
    from simulator.bacnet_controller import BACnetController

log = logging.getLogger(__name__)

# Cold-aisle CRAH supply-air setpoint (°C). IT inlet/intake temperatures are
# modelled around this value, not around device CPU load — it is the air the
# equipment pulls in. ~22 °C sits in the middle of the ASHRAE TC9.9 recommended
# envelope (18–27 °C). Override-friendly single source of truth.
_SUPPLY_SETPOINT_C = 22.0

# Fraction of a direct-to-chip (CDU cold-plate) server's heat that still leaves
# via AIR. Cold plates capture ~70 % of the load (CPU/GPU) into the liquid loop;
# the residual (VRMs, DIMMs, drives, PSUs) is air-cooled, so the air-side exhaust
# ΔT — and thus outlet_temp — is much lower than an all-air server's.
_DTC_AIR_FRACTION = 0.30

# Plant running-status points: a value of 0 means the unit is stopped, which for
# cooling gear is as much a loss of cooling as an alarm — see _is_faulted().
_RUNNING_POINTS = frozenset({"Chiller_Running", "Run_Status", "Fan_Status", "Unit_Running"})

# UPS status progression
_UPS_STATES = ("normal", "on_battery", "low_battery")
# BGP session states
_BGP_STATES = ("established", "idle", "active", "connect")

# Module-level cache for snmprec_generator to read UPS/PDU states without a reference to DeviceStateStore.
_ext_state_cache: Dict[str, dict] = {}

# Module-level cache of live chiller-plant BACnet telemetry (device name → {point: value}),
# published each tick from the BACnet controller so snmprec_generator can patch the plant
# SNMP OIDs with the SAME live values the BACnet plane serves.
_plant_state_cache: Dict[str, dict] = {}


def _get_ext_state(device_name: str) -> dict:
    return _ext_state_cache.get(device_name, {})


def _get_plant_state(device_name: str) -> dict:
    return _plant_state_cache.get(device_name, {})


class DeviceStateStore:
    """
    Single in-memory source of truth for all live device metrics.

    Usage::

        store = DeviceStateStore(device_manager, topology, "datasets", tick_interval=1)
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
        tick_interval: float = 1.0,
        snmp_sync_every: int = 5,   # rewrite SNMP .snmprec/.dbm every 5 ticks (decoupled from the 1s metric tick)
    ):
        self._dm              = device_manager
        self._topology        = topology
        self._datasets_dir    = str(Path(datasets_dir).resolve())
        self._tick_interval   = tick_interval
        self._snmp_sync_every = snmp_sync_every

        # Direct-to-chip leak → CPU-temp coupling. A CDU leak starves the cold
        # plates on the servers it cools, so their CPU temp climbs (and the
        # HighTemperature rule fires a corroborating SNMP trap). Map is built
        # lazily from the TCS cooling edges; intensity comes from the leak's
        # loop-pressure drop, refreshed each tick from the plant-state cache.
        self._cdu_loop_servers_cache: Optional[Dict[str, set]] = None
        self._liquid_servers_cache: Optional[set] = None   # servers on a CDU cold-plate loop
        self._leak_heat: Dict[str, float] = {}   # server name → 0..1 intensity

        # Plant-wide cascade: upstream cooling faults (chiller/CHW-pump/tower)
        # warm the chilled water serving a whole datacenter, which then warms the
        # CRAH supply air (→ server inlet) and the CDU coolant (→ liquid CPU).
        # _cool_ctx caches the cooling-topology maps; _chw_pen is the per-DC CHW
        # temperature penalty (°C), ramped each tick toward the fault target.
        self._cool_ctx: Optional[dict] = None
        self._chw_pen: Dict[str, float] = {}

        # Per-rack cold-aisle supply temperature: {rack_key: [temp, last_tick]}.
        # All devices in a rack share one baseline (same cold aisle); it advances
        # by a slow random walk once per tick. Inlet = this baseline + a height
        # term (top-of-rack runs warmer from recirculation).
        self._rack_supply: Dict[tuple, list] = {}

        # Stable boot-time cache: {ip: nanoseconds}.
        # Computed once the first time get_metrics() is called for a device
        # so that boot-time never drifts between gNMI responses.
        self._boot_times: dict = {}

        # SNMP sync
        self._snmp_ctrl: Optional["SNMPSimController"] = None
        self._snmp_enabled: bool = False

        # BACnet controller (optional — only active when BACnet sim is running)
        self._bacnet_ctrl: Optional["BACnetController"] = None

        # Background ticker
        self._thread: Optional[threading.Thread] = None
        self._recovery_thread: Optional[threading.Thread] = None
        self._stop_ev  = threading.Event()
        self._pause_ev = threading.Event()   # set → paused
        self._tick_count: int = 0

        # Per-metric enable flags — toggled by TickPanel
        self.metric_flags: Dict[str, bool] = {
            # All devices
            "cpu_usage":            True,
            "memory_used":          True,
            "disk_used":            True,
            "sys_uptime":           True,
            "cpu_temp":             True,
            "inlet_temp":           True,
            "fan_rpm":              True,
            "iface_octets":         True,
            "iface_errors":         True,
            "iface_discards":       True,
            "interface_flap":       True,
            # Sensor devices
            "sensor_ambient_temp":  True,
            "humidity":             True,
            "dewpoint":             True,
            "airflow":              True,
            "mid_temp":             True,
            "outlet_temp":          True,
            "water_detection":      True,
            # UPS devices
            "ups_status":           True,
            "ups_output_load":      True,
            "ups_battery_status":   True,
            "ups_input_voltage":    True,
            "ups_input_frequency":  True,
            "ups_fan_status":       True,
            "ups_charger_status":   True,
            "ups_rectifier_status": True,
            "ups_phase_status":     True,
            "ups_bypass_status":    True,
            "ups_battery_health":   True,
            "ups_energy_kwh":       True,
            # PDU / Floor PDU devices
            "pdu_load":             True,
            "pdu_voltage":          True,
            "pdu_power_factor":     True,
            "pdu_phase_imbalance":  True,
            "pdu_outlet_status":    True,
            "pdu_breaker_status":   True,
            "pdu_outlet_failure":   True,
            "pdu_smoke":            True,
            "pdu_outlet_current":   True,
            "pdu_ground_fault":     True,
            "pdu_frequency":        True,
            "pdu_temperature":      True,
            "pdu_humidity":         True,
            "pdu_energy_kwh":       True,
            # Router / Firewall
            "bgp_sessions":         True,
        }

        # Verdigris EV2 + chiller-plant BACnet metric flags (Metrics Tick panel).
        # Plant keys are "<device_type>:<PointName>" (mirrors PLANT_SPEC); EV2
        # metrics are grouped. bacnet_controller.tick() filters values by these.
        from core.bacnet_plant_generator import PLANT_SPEC as _PLANT_SPEC
        for _dt, _spec in _PLANT_SPEC.items():
            for _nm, *_rest in _spec["ai"]:
                self.metric_flags[f"{_dt}:{_nm}"] = True
            for _nm in _spec["bi"]:
                self.metric_flags[f"{_dt}:{_nm}"] = True
        for _g in ("ev2_power", "ev2_energy", "ev2_power_quality",
                   "ev2_freq_pf", "ev2_alarms", "ev2_circuits"):
            self.metric_flags[_g] = True

        # Per-DEVICE plant binary overrides, set from a node's Metric Tick window
        # (right-click a plant device). Keyed by device IP (stable across renames)
        # → {point: forced_value} — e.g. {"192.168.4.247": {"Alarm_FlowLoss": 1.0}}
        # trips just that one unit, independent of the type-wide Limits-tab lock.
        # Applied by the BACnet controller (matched on device_ip) atop the locks.
        self.plant_alarm_overrides: Dict[str, Dict[str, float]] = {}

        # Per-DEVICE live-metric overrides for servers / network gear, set from a
        # node's Metric Tick window. {device_id: {metric: value}} — forces a metric
        # to a held value each tick (cpu_usage/memory_pct/disk_pct/cpu_temp/
        # inlet_temp), e.g. pin a server to 99% CPU to drive a hotspot + traps.
        self.device_overrides: Dict[str, Dict[str, float]] = {}

        # Inject Fault ramps (right-click → Inject Fault). Unlike device_overrides
        # (instant pin), these EASE a metric toward a target over several ticks so
        # it crosses the SNMP threshold organically — the rule engine then fires
        # the trap, and an SNMP poll of the same OID reads the same value. On clear
        # the ramp reverses toward the captured baseline, crossing the recovery
        # threshold so a recovery trap fires. {device_id: {metric: ramp-record}}.
        self._fault_ramps: Dict[str, Dict[str, dict]] = {}

        # Per-metric limits — toggled and configured by TickPanel (Limits tab)
        # Numeric: {"enabled": bool, "min": float, "max": float}
        # State lock: {"enabled": bool, "lock": str, "options": list[str]}
        self.metric_limits: Dict[str, dict] = {
            # Numeric limits
            "cpu_usage":           {"enabled": False, "min": 0.0,   "max": 100.0},
            "memory_pct":          {"enabled": False, "min": 0.0,   "max": 100.0},
            "disk_pct":            {"enabled": False, "min": 0.0,   "max": 100.0},
            "cpu_temp":            {"enabled": False, "min": 20.0,  "max": 95.0},
            "inlet_temp":          {"enabled": False, "min": 15.0,  "max": 55.0},
            "fan_rpm":             {"enabled": False, "min": 0.0,   "max": 20000.0},
            "sensor_ambient_temp":  {"enabled": False, "min": 15.0,  "max": 35.0},
            "humidity":            {"enabled": False, "min": 10.0,  "max": 90.0},
            "airflow":             {"enabled": False, "min": 0.2,   "max": 4.0},
            "mid_temp":            {"enabled": False, "min": 15.0,  "max": 55.0},
            "outlet_temp":         {"enabled": False, "min": 15.0,  "max": 65.0},
            "ups_output_load":     {"enabled": False, "min": 0.0,   "max": 100.0},
            "ups_input_voltage":   {"enabled": False, "min": 200.0, "max": 240.0},
            "ups_input_frequency": {"enabled": False, "min": 49.5,  "max": 50.5},
            "ups_battery_health":  {"enabled": False, "min": 0.0,   "max": 100.0},
            "pdu_load":            {"enabled": False, "min": 0.0,   "max": 100.0},
            "pdu_voltage":         {"enabled": False, "min": 205.0, "max": 235.0},
            "pdu_outlet_current":  {"enabled": False, "min": 0.0,   "max": 20.0},
            "pdu_frequency":       {"enabled": False, "min": 49.5,  "max": 50.5},
            "pdu_temperature":     {"enabled": False, "min": 15.0,  "max": 45.0},
            "pdu_humidity":        {"enabled": False, "min": 10.0,  "max": 90.0},
            # State locks
            "ups_status":           {"enabled": False, "lock": "normal",      "options": ["normal", "on_battery", "low_battery"]},
            "ups_battery_status":   {"enabled": False, "lock": "normal",      "options": ["normal", "failure", "disconnected"]},
            "ups_fan_status":       {"enabled": False, "lock": "ok",          "options": ["ok", "failure"]},
            "ups_charger_status":   {"enabled": False, "lock": "ok",          "options": ["ok", "failure"]},
            "ups_rectifier_status": {"enabled": False, "lock": "ok",          "options": ["ok", "failure"]},
            "ups_phase_status":     {"enabled": False, "lock": "ok",          "options": ["ok", "failure"]},
            "ups_bypass_status":    {"enabled": False, "lock": "off",         "options": ["off", "on"]},
            "pdu_outlet_status":    {"enabled": False, "lock": "on",          "options": ["on", "off"]},
            "pdu_breaker_status":   {"enabled": False, "lock": "ok",          "options": ["ok", "tripped"]},
            "pdu_outlet_failure":   {"enabled": False, "lock": "ok",          "options": ["ok", "failed"]},
            "pdu_smoke":            {"enabled": False, "lock": "no",          "options": ["no", "yes"]},
            "pdu_ground_fault":     {"enabled": False, "lock": "no",          "options": ["no", "yes"]},
            "water_detection":      {"enabled": False, "lock": "dry",         "options": ["dry", "wet"]},
            "bgp_sessions":         {"enabled": False, "lock": "established", "options": ["established", "idle"]},
        }

        # Verdigris EV2 + chiller-plant BACnet limits (Metrics Tick — Limits tab).
        # Plant AI points = numeric clamp; BI points = off/on force (force alarms).
        # Numeric bounds default None → frontend supplies them on apply.
        for _dt, _spec in _PLANT_SPEC.items():
            for _nm, *_rest in _spec["ai"]:
                self.metric_limits[f"{_dt}:{_nm}"] = {"enabled": False, "min": None, "max": None}
            for _nm in _spec["bi"]:
                self.metric_limits[f"{_dt}:{_nm}"] = {"enabled": False, "lock": "off", "options": ["off", "on"]}
        for _nm in ("Panel_Total_kW", "Voltage_PhA", "Voltage_PhB", "Voltage_PhC",
                    "Current_PhA", "Current_PhB", "Current_PhC", "Line_Frequency",
                    "Panel_PF", "Voltage_THD", "Current_THD"):
            self.metric_limits[f"ev2:{_nm}"] = {"enabled": False, "min": None, "max": None}
        for _nm in ("Alarm_Overcurrent", "Alarm_VoltageImbalance", "Alarm_HighTHD",
                    "Alarm_PhaseLoss", "Alarm_SensorFault"):
            self.metric_limits[f"ev2:{_nm}"] = {"enabled": False, "lock": "off", "options": ["off", "on"]}

        # Wall-clock link recovery: device_name → {iface_index: scheduled_time}
        self._pending_recovery: Dict[str, Dict[int, float]] = {}
        self._recovery_lock = threading.Lock()

        self._log_cb: Optional[Callable[[str, str], None]] = None
        self._tick_cb: Optional[Callable[[], None]] = None
        self._link_cb: Optional[Callable[[str, str, bool], None]] = None

        # Rule engine integration
        self._rule_engine_cb: Optional[Callable] = None

        # Simulated extended states per device (not stored on Device object)
        # device.name → {ups_status, bgp_sessions: [{peer, state}]}
        self._ext_states: Dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    #  Configuration                                                       #
    # ------------------------------------------------------------------ #

    def set_log_callback(self, cb: Callable[[str, str], None]):
        """cb(message, level) — level ∈ {"info", "success", "warning", "error"}"""
        self._log_cb = cb

    def set_tick_callback(self, cb: Callable[[], None]):
        """cb() is called after every successful tick — use to push SSE/UI sync."""
        self._tick_cb = cb

    def set_link_callback(self, cb: Callable[[str, str, bool], None]):
        """cb(src_id, dst_id, broken) — fired when the ticker breaks/restores
        a link (e.g. server powered off via Redfish takes its uplinks down)."""
        self._link_cb = cb

    def set_rule_engine_callback(self, cb: Callable):
        """
        cb(fact: DeviceFact, device: Device) is called once per device per tick.
        The rule engine evaluates the fact and fires appropriate traps.
        """
        self._rule_engine_cb = cb

    # ── Tick runtime controls ──────────────────────────────────────────────

    def set_tick_interval(self, interval: float):
        """Change interval live; takes effect on the next wake-up."""
        self._tick_interval = max(1.0, float(interval))

    def set_paused(self, paused: bool):
        if paused:
            self._pause_ev.set()
        else:
            self._pause_ev.clear()

    def is_paused(self) -> bool:
        return self._pause_ev.is_set()

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

    def enable_bacnet(self, ctrl: "BACnetController"):
        """Register BACnet controller — its tick() is called every tick cycle."""
        self._bacnet_ctrl = ctrl
        self._log("[StateStore] BACnet telemetry sync enabled.", "info")

    def disable_bacnet(self):
        """Deregister BACnet controller."""
        self._bacnet_ctrl = None
        _plant_state_cache.clear()
        self._log("[StateStore] BACnet telemetry sync disabled.", "info")

    def _publish_plant_state(self):
        """Publish live chiller-plant BACnet present-values to the module cache
        so snmprec_generator patches the plant SNMP OIDs with the same values —
        keeping the SNMP and BACnet planes in lock-step."""
        ctrl = self._bacnet_ctrl
        if ctrl is None:
            return
        for snap in ctrl.get_telemetry_snapshot():
            if str(snap.get("kind", "")).startswith("plant:"):
                _plant_state_cache[snap["name"]] = snap["values"]

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
        self._recovery_thread = threading.Thread(
            target=self._recovery_loop,
            daemon=True,
            name="DeviceStateStore-recovery",
        )
        self._thread.start()
        self._recovery_thread.start()
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
        if self._recovery_thread:
            self._recovery_thread.join(timeout=3)
            self._recovery_thread = None
        with self._recovery_lock:
            self._pending_recovery.clear()
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
            "outlet_temp":  device.outlet_temp,
            "fan_rpm":      device.fan_rpm,
            "humidity":     device.humidity,
            "dewpoint":     device.dewpoint,
            "airflow":      device.airflow,
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
            if self._pause_ev.is_set():
                continue
            try:
                self._tick()
            except Exception:
                log.exception("[StateStore] Tick error")

    def _recovery_loop(self):
        """Checks pending link recoveries every second and fires LinkUp immediately."""
        while not self._stop_ev.wait(1.0):
            now = time.time()
            recovered: Dict[str, List[int]] = {}
            with self._recovery_lock:
                for device_name, schedules in list(self._pending_recovery.items()):
                    ready = [idx for idx, t in schedules.items() if now >= t]
                    if ready:
                        recovered[device_name] = ready
                        for idx in ready:
                            del schedules[idx]
                    if not schedules:
                        del self._pending_recovery[device_name]

            if not recovered:
                continue

            recovered_devices = []
            for device_name, iface_indices in recovered.items():
                device = next(
                    (d for d in self._dm.get_all_devices() if d.name == device_name),
                    None,
                )
                if device is None:
                    continue
                for iface in device.interfaces:
                    if iface.index in iface_indices:
                        iface.oper_status = 1
                recovered_devices.append(device)

            if recovered_devices and self._rule_engine_cb:
                try:
                    self._publish_facts(recovered_devices)
                except Exception:
                    log.exception("[StateStore] Recovery publish error")

    def _cdu_loop_servers(self) -> Dict[str, set]:
        """Map each CDU name → set of server names on its TCS cold-plate loop,
        built once from the cooling-layer edges. Cached for the run."""
        if self._cdu_loop_servers_cache is not None:
            return self._cdu_loop_servers_cache
        from core.device_manager import DeviceType
        out: Dict[str, set] = {}
        try:
            for u, v, _d in self._topology.get_edges_by_layer("cooling"):
                du, dv = self._dm.get_device(u), self._dm.get_device(v)
                if du is None or dv is None:
                    continue
                cdu, srv = None, None
                if du.device_type == DeviceType.CDU and dv.device_type == DeviceType.SERVER:
                    cdu, srv = du, dv
                elif dv.device_type == DeviceType.CDU and du.device_type == DeviceType.SERVER:
                    cdu, srv = dv, du
                if cdu is not None:
                    out.setdefault(cdu.name, set()).add(srv.name)
        except Exception:
            log.exception("[StateStore] CDU loop map build error")
        self._cdu_loop_servers_cache = out
        return out

    def _liquid_cooled_servers(self) -> set:
        """Set of all server names sitting on a CDU cold-plate loop (direct-to-
        chip liquid cooling). Cached via the underlying CDU-loop map."""
        if self._liquid_servers_cache is None:
            self._liquid_servers_cache = set().union(*self._cdu_loop_servers().values()) \
                if self._cdu_loop_servers() else set()
        return self._liquid_servers_cache

    # ── Plant-wide cooling cascade ──────────────────────────────────────────
    def _cooling_context(self) -> dict:
        """Cached maps tying servers/rooms to the cooling plant that feeds them:
          crah_by_room  {(dc,room): [crah names]}     — CRAHs cooling each room
          cdu_by_server {server name: cdu name}       — which CDU cools a server
          plant_by_dc   {dc: {chiller|pump|cooling_tower: [names]}}
        Built once from the device inventory; used to propagate upstream faults.
        """
        if self._cool_ctx is not None:
            return self._cool_ctx
        crah_by_room: Dict[tuple, list] = {}
        plant_by_dc: Dict[str, Dict[str, list]] = {}
        try:
            for d in self._dm.get_all_devices():
                dt = d.device_type
                if dt == DeviceType.CRAH:
                    crah_by_room.setdefault((d.datacenter, d.room), []).append(d.name)
                elif dt in (DeviceType.CHILLER, DeviceType.PUMP,
                            DeviceType.COOLING_TOWER, DeviceType.VALVE):
                    plant_by_dc.setdefault(d.datacenter, {}).setdefault(dt.value, []).append(d.name)
        except Exception:
            log.exception("[StateStore] cooling context build error")
        cdu_by_server: Dict[str, str] = {}
        for cdu_name, servers in self._cdu_loop_servers().items():
            for s in servers:
                cdu_by_server[s] = cdu_name
        self._cool_ctx = {"crah_by_room": crah_by_room,
                          "cdu_by_server": cdu_by_server,
                          "plant_by_dc": plant_by_dc}
        return self._cool_ctx

    @staticmethod
    def _is_faulted(name: str) -> bool:
        """True if this plant device is in a cooling-loss state — either any
        Alarm_* point is set, or its running-status point reads 0 (unit stopped).
        A stopped unit is as much a loss of cooling as an alarm."""
        pv = _plant_state_cache.get(name)
        if not pv:
            return False
        for k, v in pv.items():
            if k.startswith("Alarm_") and float(v) >= 0.5:
                return True
            if k in _RUNNING_POINTS and float(v) < 0.5:
                return True
        return False

    # Cooling penalty model constants.
    _COOL_TOL = 0.34     # cooling-loss fraction the plant rides out (N+1 + thermal mass)
    _COOL_RUN = 0.20     # runaway integration gain (°C/tick per unit deficit)
    _COOL_MAX = 28.0     # ceiling — equipment thermal-limit territory (inlet → ~50 °C)

    def _compute_chw_penalty(self) -> None:
        """Per-tick: update the per-DC chilled-water temperature penalty.

        The cooling plant is a SERIES chain — chillers make CHW, pumps move it,
        towers reject condenser heat — so the delivered cooling is the product of
        each stage's surviving fraction. From that, a cooling-loss fraction L:

          * Below the redundancy tolerance (N+1 + thermal mass) the loss is ridden
            out and the penalty settles at a small bounded value (EMA).
          * Above it the IT heat load exceeds the remaining cooling, so heat
            ACCUMULATES — the penalty integrates upward every tick (thermal
            runaway) toward an equipment-limit ceiling instead of plateauing.

        So losing 1 of 3 chillers is a mild, steady offset, but losing the whole
        chiller (or pump) stage runs the room away toward shutdown temperatures.
        Clearing the fault drops L below tolerance and the penalty decays back."""
        ctx = self._cooling_context()
        for dc, kinds in ctx["plant_by_dc"].items():
            def frac(kind: str) -> float:
                names = kinds.get(kind) or []
                return (sum(1 for n in names if self._is_faulted(n)) / len(names)) if names else 0.0
            # surviving cooling capacity (series chain); towers/valves throttle partially
            avail = ((1.0 - frac("chiller")) * (1.0 - frac("pump"))
                     * (1.0 - 0.6 * frac("cooling_tower")) * (1.0 - 0.5 * frac("valve")))
            loss = max(0.0, 1.0 - avail)                  # 0 = full cooling, 1 = none
            cur = self._chw_pen.get(dc, 0.0)
            deficit = loss - self._COOL_TOL
            if deficit <= 0.0:
                target = loss * 18.0                       # bounded: redundancy absorbs it (≤ ~6 °C)
                new = cur + (target - cur) * 0.06          # EMA ease in/out
            else:
                new = cur + self._COOL_RUN * deficit       # runaway: integrate heat upward
            self._chw_pen[dc] = round(min(new, self._COOL_MAX), 3)

    def _room_supply_temp(self, device: "Device") -> float:
        """Cold-aisle supply temperature for a device's room.

        Healthy CRAHs set the room supply via their live Supply_Air_Temp (so a
        HighTemp fault, which warms that air, propagates to inlets). A CRAH that
        is OFF (Unit_Running=0) or has lost airflow delivers no cold air, so it is
        dropped from the average AND counts as lost cooling capacity — the room
        warms in proportion to the fraction of CRAHs down. The datacenter CHW
        penalty (upstream chiller/pump/tower/valve faults) is added on top."""
        ctx = self._cooling_context()
        crahs = ctx["crah_by_room"].get((device.datacenter, device.room))
        if not crahs:
            return self._rack_supply_temp(device) + self._chw_pen.get(device.datacenter, 0.0)
        supplies, ndown = [], 0
        for n in crahs:
            pv = _plant_state_cache.get(n) or {}
            off = float(pv.get("Unit_Running", 1.0)) < 0.5
            noair = float(pv.get("Alarm_AirflowLoss", 0.0)) >= 0.5
            if off or noair:               # not delivering cold air
                ndown += 1
                continue
            sa = pv.get("Supply_Air_Temp")
            if sa is not None:
                supplies.append(float(sa))
        base = sum(supplies) / len(supplies) if supplies else self._rack_supply_temp(device)
        base += (ndown / len(crahs)) * 12.0      # lost capacity → room heats (all down → +12)
        return base + self._chw_pen.get(device.datacenter, 0.0)

    def _compute_leak_heat(self) -> None:
        """Refresh server→intensity heat map from leaking CDUs. Intensity scales
        with the loop-pressure drop; a forced leak (alarm on, pressure normal)
        still applies a moderate floor."""
        heat: Dict[str, float] = {}
        loop = self._cdu_loop_servers()
        if loop:
            for cdu_name, servers in loop.items():
                pv = _plant_state_cache.get(cdu_name)
                if not pv or pv.get("Alarm_Leak", 0.0) < 0.5:
                    continue
                p = pv.get("TCS_Loop_Pressure", 250.0)
                inten = max(0.5, min(1.0, (250.0 - p) / 110.0))
                for s in servers:
                    heat[s] = max(heat.get(s, 0.0), inten)
        self._leak_heat = heat

    def _tick(self):
        devices = self._dm.get_all_devices()
        self._compute_leak_heat()
        self._compute_chw_penalty()       # roll per-DC CHW penalty from upstream faults
        for device in devices:
            self._step_device(device)
            self._step_ext_state(device)
            self._apply_ext_overrides(device)   # pin UPS/PDU overrides last
            self._apply_fault_ramps(device)     # ease injected-fault metrics last

        self._tick_count += 1

        if self._snmp_enabled and (self._tick_count % self._snmp_sync_every == 0):
            self._sync_snmp(devices)

        if self._rule_engine_cb:
            self._publish_facts(devices)

        # BACnet telemetry tick — advances EV2 + plant engines and dispatches COV
        if self._bacnet_ctrl:
            try:
                self._bacnet_ctrl.tick(self._tick_interval, self.metric_flags, self.metric_limits,
                                       self.plant_alarm_overrides)
                self._publish_plant_state()
            except Exception:
                log.exception("[StateStore] BACnet tick error")

        if self._tick_cb:
            try:
                self._tick_cb()
            except Exception:
                pass

    def _num_limit(self, key: str, value: float) -> float:
        lim = self.metric_limits.get(key)
        if lim and lim["enabled"]:
            return max(lim["min"], min(lim["max"], value))
        return value

    def _state_lock(self, key: str, value: str) -> str:
        lim = self.metric_limits.get(key)
        if lim and lim["enabled"]:
            return lim["lock"]
        return value

    def _rack_supply_temp(self, device: "Device") -> float:
        """Cold-aisle supply temperature for the rack this device sits in.

        Shared by every device in the same rack (they breathe the same cold
        aisle). It mean-reverts to the CRAC supply setpoint each tick (a random
        walk alone drifts to the rails over a long run), so inlets stay correlated
        within a rack and centred on ~22 °C — ASHRAE TC9.9 recommended. Callers
        add a height term on top.
        """
        key = (device.datacenter, device.room, device.floor,
               device.rack_row, device.rack_num)
        entry = self._rack_supply.get(key)
        if entry is None:
            base = _SUPPLY_SETPOINT_C + random.uniform(-1.0, 1.0)
            self._rack_supply[key] = [base, self._tick_count]
            return base
        if entry[1] != self._tick_count:                 # advance once per tick
            entry[0] = max(20.0, min(25.0, entry[0]
                + (_SUPPLY_SETPOINT_C - entry[0]) * 0.06 + random.uniform(-0.3, 0.3)))
            entry[1] = self._tick_count
        return entry[0]

    def _break_server_links(self, device: "Device") -> list:
        """Break production links from a powered-off server to its peers.

        Returns the peer ids of links actually broken here (links already
        broken — e.g. by the user — are skipped so restore won't touch them).
        """
        broke = []
        topo = self._topology
        if topo is None or not topo.graph.has_node(device.id):
            return broke
        for peer_id, edges in list(topo.graph.adj[device.id].items()):
            prod = [e for e in edges.values()
                    if e.get("layer", "production") == "production"]
            if prod and not any(e.get("broken") for e in prod):
                topo.break_link(device.id, peer_id, "production")
                broke.append(peer_id)
                if self._link_cb:
                    try:
                        self._link_cb(device.id, peer_id, True)
                    except Exception:
                        pass
        return broke

    def _step_device(self, device: "Device"):
        """Apply one random-walk step to a single device's metrics.

        CPU spikes are classified at onset as brief (6 in 7) or sustained (1 in 7).

          • Brief spike  : CPU drops back to normal zone (35–60%) in ONE tick
                           (~30 s), so CPUNormal fires within 5–30 s of HighCPU.
          • Sustained spike: gradual 3–8 pp/tick recovery — stays in alert
                           for several ticks; may trigger HighCPUSustained.
          • Hysteresis zone (70–90%): 3–8 pp/tick drop, clears in 3–7 ticks.
          • Normal zone (<70%): ±4 walk capped 65%; 1 % spike chance.
        """
        # Access per-device extended state (initialised here on first call)
        ext = self._ext_states.setdefault(device.name, {
            "ups_status": "normal",
            "ups_output_load": random.uniform(20.0, 60.0),
            "ups_battery_status": "normal",
            "ups_input_voltage": random.uniform(216.0, 224.0),
            "ups_input_frequency": random.uniform(49.8, 50.2),
            "ups_fan_status": "ok",
            "ups_charger_status": "ok",
            "ups_rectifier_status": "ok",
            "ups_phase_status": "ok",
            "ups_bypass_status": "off",
            "ups_battery_health": random.uniform(92.0, 100.0),
            "ups_energy_kwh": 0.0,
            "pdu_load": random.uniform(30.0, 60.0),
            "pdu_voltage": random.uniform(216.0, 224.0),
            "pdu_power_factor": random.uniform(0.92, 0.98),
            "pdu_phase_imbalance": random.uniform(0.0, 5.0),
            "pdu_outlet_status": "on",
            "pdu_breaker_status": "ok",
            "pdu_outlet_failure": "ok",
            "pdu_smoke": "no",
            "pdu_outlet_current": random.uniform(5.0, 15.0),
            "pdu_ground_fault": "no",
            "pdu_frequency": random.uniform(49.8, 50.2),
            "pdu_temperature": random.uniform(18.0, 28.0),
            "pdu_humidity": random.uniform(30.0, 60.0),
            "pdu_energy_kwh": 0.0,
            "bgp_sessions": [],
            "water_detection": "dry",
            "cpu_sustained": False,
            "mem_sustained": False,
        })

        mf = self.metric_flags

        # Powered-off server (Redfish chassis state): no OS, no load, no
        # traffic — metrics go dark instead of random-walking.
        if (device.device_type == DeviceType.SERVER
                and getattr(device, "power_state", "On") == "Off"):
            if not ext.get("srv_was_off"):
                ext["srv_was_off"] = True
                # Link loss is bidirectional: take down the production links
                # to this server's peers (switch-side port + canvas edge).
                # Record only links WE broke so restore skips user-broken ones.
                ext["srv_off_links"] = self._break_server_links(device)
            device.cpu_usage = 0
            device.memory_used = 0
            device.sys_uptime = 0
            # Powered off: no self-airflow, but the intake sensor still reads the
            # surrounding cold-aisle air — same rack supply baseline + height
            # recirculation as a live device.
            if mf["inlet_temp"]:
                base = self._rack_supply_temp(device)
                grad = min(max(device.rack_unit, 0), 42) / 42.0 * 3.0
                device.inlet_temp = round(max(15.0, min(32.0,
                    base + grad + random.uniform(-0.2, 0.2))), 1)
            # CPU temp decays gradually toward 0 — chip stops dissipating once
            # powered off (sensor reads no heat).
            if mf["cpu_temp"]:
                device.cpu_temp = round(
                    max(0.0, device.cpu_temp - random.uniform(1.5, 3.5)), 1)
            for iface in device.interfaces:
                iface.oper_status = 2
            return
        if ext.pop("srv_was_off", False):
            # Power restored — uplinks and NICs come back up; uptime restarts
            # from zero via the normal walk below.
            for peer_id in ext.pop("srv_off_links", []):
                self._topology.restore_link(device.id, peer_id, "production")
                if self._link_cb:
                    try:
                        self._link_cb(device.id, peer_id, False)
                    except Exception:
                        pass
            for iface in device.interfaces:
                iface.oper_status = 1

        # CPU — brief vs sustained spike recovery
        if mf["cpu_usage"]:
            if device.cpu_usage > 90:
                if ext.get("cpu_sustained", False):
                    device.cpu_usage = max(1, device.cpu_usage + random.randint(-8, -3))
                else:
                    device.cpu_usage = random.randint(35, 60)
            elif device.cpu_usage >= 70:
                device.cpu_usage = max(1, device.cpu_usage + random.randint(-8, -3))
            else:
                device.cpu_usage = max(1, min(65, device.cpu_usage + random.randint(-4, 4)))
                if random.random() < 0.01:
                    device.cpu_usage = random.randint(91, 99)
                    ext["cpu_sustained"] = random.random() < (1.0 / 7.0)
        if mf["cpu_usage"]:
            device.cpu_usage = int(self._num_limit("cpu_usage", device.cpu_usage))

        # Memory — brief (9 in 10) vs sustained (1 in 10) spike recovery
        if mf["memory_used"]:
            lo        = device.memory_total // 10
            alert_hi  = int(device.memory_total * 0.85)
            recov_thr = int(device.memory_total * 0.70)
            swing     = max(1, device.memory_total // 50)
            if device.memory_used > alert_hi:
                if ext.get("mem_sustained", False):
                    drop = random.randint(int(device.memory_total * 0.03),
                                          int(device.memory_total * 0.06))
                    device.memory_used = max(lo, device.memory_used - drop)
                else:
                    device.memory_used = random.randint(
                        int(device.memory_total * 0.35),
                        int(device.memory_total * 0.60),
                    )
            elif device.memory_used >= recov_thr:
                drop = random.randint(int(device.memory_total * 0.03),
                                      int(device.memory_total * 0.06))
                device.memory_used = max(lo, device.memory_used - drop)
            else:
                cap = int(device.memory_total * 0.65)
                device.memory_used = max(lo, min(cap,
                    device.memory_used + random.randint(-swing, swing)))
                if random.random() < 0.005:
                    device.memory_used = random.randint(int(device.memory_total * 0.86),
                                                        int(device.memory_total * 0.92))
                    ext["mem_sustained"] = random.random() < (1.0 / 10.0)
        if mf["memory_used"]:
            _lim_mem = self.metric_limits.get("memory_pct")
            if _lim_mem and _lim_mem["enabled"]:
                _lo_mem = int(device.memory_total * _lim_mem["min"] / 100)
                _hi_mem = int(device.memory_total * _lim_mem["max"] / 100)
                device.memory_used = max(_lo_mem, min(_hi_mem, device.memory_used))

        # Disk: slow random walk — mostly grows (log/tmp accumulation), occasional drop
        if mf["disk_used"]:
            _disk_swing = max(1, device.disk_total // 200)
            _disk_delta = random.randint(-_disk_swing // 4, _disk_swing)
            device.disk_used = max(
                int(device.disk_total * 0.05),
                min(int(device.disk_total * 0.90), device.disk_used + _disk_delta),
            )
        if mf["disk_used"]:
            _lim_disk = self.metric_limits.get("disk_pct")
            if _lim_disk and _lim_disk["enabled"]:
                _lo_disk = int(device.disk_total * _lim_disk["min"] / 100)
                _hi_disk = int(device.disk_total * _lim_disk["max"] / 100)
                device.disk_used = max(_lo_disk, min(_hi_disk, device.disk_used))

        # Uptime
        if mf["sys_uptime"]:
            device.sys_uptime += int(self._tick_interval * 100)

        # CPU/ASIC temperature
        if mf["cpu_temp"]:
            # Direct-to-chip liquid cooling holds the die far cooler than air: a
            # cold plate fed with ~25 °C water keeps a loaded CPU ~45 °C, vs ~62 °C
            # air-cooled. Model it as a lower baseline + flatter load slope for
            # servers on a CDU loop; everything else uses the air-cooled curve.
            _liquid = (device.device_type == DeviceType.SERVER
                       and device.name in self._liquid_cooled_servers())
            if _liquid:
                _cpu_t = 18.0 + device.cpu_usage * 0.27 + random.uniform(-1.0, 1.0)
                # Warmer coolant raises the die: a CDU fault (HighSupplyTemp/
                # PumpFault/LowFlow) lifts its TCS_Supply_Temp above the ~32 °C
                # nominal, and an upstream chiller/CHW fault adds the DC penalty.
                _cdu = self._cooling_context()["cdu_by_server"].get(device.name)
                _pv = _plant_state_cache.get(_cdu) if _cdu else None
                if _pv:
                    if float(_pv.get("Unit_Running", 1.0)) < 0.5:
                        _cpu_t += 34.0          # CDU stopped → coolant flow lost → die climbs
                    elif _pv.get("TCS_Supply_Temp") is not None:
                        _cpu_t += max(0.0, float(_pv["TCS_Supply_Temp"]) - 32.0) * 1.2
                _cpu_t += self._chw_pen.get(device.datacenter, 0.0) * 0.8
            else:
                _cpu_t = 20.0 + device.cpu_usage * 0.42 + random.uniform(-1.0, 1.0)
            # Warmer INTAKE AIR raises the die/ASIC: air-cooled gear tracks its
            # inlet nearly 1:1, so a cooling failure that lifts the cold-aisle temp
            # also drives CPU temp up; direct-to-chip CPUs are largely decoupled
            # (the cold plate, not room air, removes the heat) so only weakly.
            # Uses last tick's inlet (the inlet block runs just below) — 1 s lag.
            _intake = max(0.0, (getattr(device, "inlet_temp", 22.0) or 22.0) - 22.0)
            _cpu_t += _intake * (0.3 if _liquid else 0.9)
            # Direct-to-chip leak: cold plate starves → chip runs hot (up to
            # +38 °C at full severity), pushing past the HighTemperature trap.
            # This cancels the liquid advantage above, as a real leak would.
            if device.device_type == DeviceType.SERVER and self._leak_heat:
                _cpu_t += self._leak_heat.get(device.name, 0.0) * 38.0
            device.cpu_temp = round(max(20.0, min(95.0, _cpu_t)), 1)
            device.cpu_temp = self._num_limit("cpu_temp", device.cpu_temp)

        # Chassis fan speed — servers only; rises with CPU temperature so it
        # tracks load. Single source of truth: Redfish _fans() reads this.
        if mf["fan_rpm"] and device.device_type == DeviceType.SERVER:
            _fan = 3000.0 + max(0.0, device.cpu_temp - 40.0) * 95.0 + random.uniform(-60, 60)
            device.fan_rpm = int(max(0.0, self._num_limit("fan_rpm", _fan)))

        # Chassis inlet temperature — servers/network gear only. COLD-AISLE
        # INTAKE air, set by the CRAH supply setpoint, NOT this device's own load
        # (load heat shows up in cpu_temp / outlet_temp, not the inlet). Every
        # device in a rack shares one supply baseline (same cold aisle); inlet
        # then rises with HEIGHT because hot air recirculates over the top of the
        # rack — top-of-rack reads ~+3 °C warmer than the floor. Kept inside the
        # ASHRAE TC9.9 recommended envelope (18–27 °C).
        if mf["inlet_temp"] and device.device_type not in (DeviceType.SENSOR, DeviceType.RPP):
            base = self._room_supply_temp(device)   # CRAH supply air + DC CHW penalty (cascade)
            grad = min(max(device.rack_unit, 0), 42) / 42.0 * 3.0   # 0 at floor .. +3 °C at top
            t = base + grad + random.uniform(-0.2, 0.2)             # small per-sensor noise
            # ceiling well above the ASHRAE envelope so a real cooling failure can
            # push inlets into the alarm range instead of pinning at 32 °C.
            device.inlet_temp = round(max(15.0, min(45.0, t)), 1)
            device.inlet_temp = self._num_limit("inlet_temp", device.inlet_temp)

        # Server chassis airflow + exhaust temp. The BMC reports exhaust as
        # inlet + ΔT, where ΔT follows from the heat dumped (power_draw_w) and
        # the volumetric cooling flow the fans push. Fans track load, so exhaust
        # velocity (airflow, m/s) rises with CPU load and inlet temp; the two
        # are coupled — more flow ⇒ smaller rise. ΔT(°C) ≈ 1.76·P / CFM; with
        # CFM ∝ velocity this reduces to k·P/v. DCIM reads outlet_temp (Redfish
        # Thermal "Exhaust") to build hot-aisle heatmaps.
        if device.device_type == DeviceType.SERVER:
            load = device.cpu_usage / 100.0
            _pw = float(device.power_draw_w or 0.0)
            # fan controllers track the HEAT dumped, i.e. power, so flow scales
            # with power_draw_w (cpu_usage drives cpu_temp/fan_rpm, but server
            # power here is not cpu-correlated). pf normalises a typical
            # ~450–760 W server band to 0..1.
            pf = max(0.0, min(1.0, (_pw - 450.0) / 310.0))
            if mf["airflow"]:
                # exhaust air velocity (m/s): scales with power so ΔT stays in
                # the realistic band; inlet temp + load add a small ramp.
                _v = (2.2 + 1.3 * pf + max(0.0, device.inlet_temp - 24.0) * 0.03
                      + 0.2 * load + random.uniform(-0.1, 0.1))
                device.airflow = round(max(0.2, min(4.0, _v)), 2)
                device.airflow = self._num_limit("airflow", device.airflow)
            if mf["outlet_temp"]:
                _v = device.airflow if device.airflow > 0.2 else 2.5
                # ΔT(°C) ≈ 1.76·P / CFM, with CFM ∝ velocity ⇒ k·P/v (k=0.056).
                # Direct-to-chip liquid-cooled servers dump only ~30 % of their
                # heat to air (cold plates take the rest), so the air-side ΔT —
                # and the rear exhaust — is much smaller than an all-air server's.
                _liquid = device.name in self._liquid_cooled_servers()
                _air_pw = _pw * (_DTC_AIR_FRACTION if _liquid else 1.0)
                _floor = 2.0 if _liquid else 3.0
                _dt = max(_floor, min(18.0, 0.056 * _air_pw / _v))
                device.outlet_temp = round(min(65.0, device.inlet_temp + _dt), 1)
                device.outlet_temp = self._num_limit("outlet_temp", device.outlet_temp)

        # Environmental readings — sensor devices only
        if device.device_type == DeviceType.SENSOR:
            # Ambient temperature: a rack environmental probe reads the cold-aisle
            # air, so it mean-reverts to the CRAC supply setpoint (~22 °C) instead
            # of drifting freely — otherwise the random walk wanders up to 35 °C
            # and shows up as a phantom hot rack via the inlet max().
            if mf["sensor_ambient_temp"]:
                device.inlet_temp = round(max(15.0, min(30.0,
                    device.inlet_temp + (_SUPPLY_SETPOINT_C - device.inlet_temp) * 0.06
                    + random.uniform(-0.3, 0.3))), 1)
                device.inlet_temp = self._num_limit("sensor_ambient_temp", device.inlet_temp)

            # Relative humidity is actively controlled by the CRAC humidifier/
            # dehumidifier, so it mean-reverts toward a ~50% setpoint inside the
            # ASHRAE TC9.9 recommended band rather than drifting freely.
            if mf["humidity"]:
                device.humidity = round(max(35.0, min(65.0,
                    device.humidity + (50.0 - device.humidity) * 0.05
                    + random.uniform(-0.8, 0.8))), 1)
                device.humidity = self._num_limit("humidity", device.humidity)
            if mf["dewpoint"]:
                device.dewpoint = round(
                    device.inlet_temp - ((100.0 - device.humidity) / 5.0), 1)
            if mf["airflow"] and "NetBotz" in device.model_name:
                device.airflow = round(max(0.2, min(4.0,
                    device.airflow + random.uniform(-0.15, 0.15))), 2)
                device.airflow = self._num_limit("airflow", device.airflow)

            # Mid-rack + exhaust temps — Raritan DPX2-T3H1 only
            if device.model_name == "Raritan DPX2-T3H1":
                if mf["mid_temp"]:
                    target_mid = device.inlet_temp + random.uniform(3.0, 7.0)
                    device.mid_temp = round(max(device.inlet_temp,
                        min(55.0, device.mid_temp * 0.85 + target_mid * 0.15)), 1)
                    device.mid_temp = self._num_limit("mid_temp", device.mid_temp)
                if mf["outlet_temp"]:
                    target_out = device.inlet_temp + random.uniform(8.0, 14.0)
                    device.outlet_temp = round(max(device.mid_temp,
                        min(65.0, device.outlet_temp * 0.85 + target_out * 0.15)), 1)
                    device.outlet_temp = self._num_limit("outlet_temp", device.outlet_temp)

        # Interface counters — only UP interfaces
        _do_oct  = mf["iface_octets"]
        _do_err  = mf["iface_errors"]
        _do_disc = mf["iface_discards"]
        if _do_oct or _do_err or _do_disc:
            congested = device.cpu_usage > 70
            moderate  = device.cpu_usage > 50
            for iface in device.interfaces:
                # Skip down ports and unconnected ports — neither carries traffic.
                if iface.oper_status != 1 or iface.connected_to_device is None:
                    continue
                if _do_oct:
                    iface.in_octets  += random.randint(5_000, 150_000)
                    iface.out_octets += random.randint(5_000, 150_000)
                if _do_err:
                    if random.random() < 0.10:
                        iface.in_errors  += 1
                    if random.random() < 0.05:
                        iface.out_errors += 1
                if _do_disc:
                    if congested:
                        iface.in_discards  += random.randint(0, 5)
                        iface.out_discards += random.randint(0, 10)
                    elif moderate and random.random() < 0.30:
                        iface.in_discards  += random.randint(0, 2)
                        iface.out_discards += random.randint(0, 3)

        # Interface flapping — connected interfaces, 0.2% chance, recovers after 5 s
        if mf["interface_flap"]:
            for iface in device.interfaces:
                if not iface.connected_to_device:
                    continue
                if iface.oper_status == 1 and random.random() < 0.002:
                    iface.oper_status = 2
                    with self._recovery_lock:
                        self._pending_recovery.setdefault(device.name, {})[iface.index] = (
                            time.time() + 5.0
                        )

        # Per-device metric overrides win last — a Metric Tick window pins these
        # to a held value regardless of the random walk above.
        self._apply_device_overrides(device)

    def _apply_device_overrides(self, device: "Device") -> None:
        """Force this device's live metrics to operator-set values (Metric Tick
        window). Percentages are translated to the underlying used/total fields."""
        ov = self.device_overrides.get(device.id)
        if not ov:
            return
        for metric, v in ov.items():
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if metric == "cpu_usage":
                device.cpu_usage = int(max(0.0, min(100.0, v)))
            elif metric == "memory_pct" and getattr(device, "memory_total", 0):
                device.memory_used = int(device.memory_total * max(0.0, min(100.0, v)) / 100.0)
            elif metric == "disk_pct" and getattr(device, "disk_total", 0):
                device.disk_used = int(device.disk_total * max(0.0, min(100.0, v)) / 100.0)
            elif metric == "cpu_temp":
                device.cpu_temp = round(max(0.0, min(120.0, v)), 1)
            elif metric == "inlet_temp":
                device.inlet_temp = round(max(0.0, min(60.0, v)), 1)
            elif metric == "humidity":
                device.humidity = round(max(0.0, min(100.0, v)), 1)
            elif metric == "dewpoint":
                device.dewpoint = round(max(-10.0, min(40.0, v)), 1)
            elif metric == "airflow":
                device.airflow = round(max(0.0, min(5.0, v)), 2)
            elif metric == "mid_temp":
                device.mid_temp = round(max(0.0, min(60.0, v)), 1)
            elif metric == "outlet_temp":
                device.outlet_temp = round(max(0.0, min(70.0, v)), 1)

    def _apply_ext_overrides(self, device: "Device") -> None:
        """Pin UPS/PDU extended-state metrics (ups_*/pdu_* — numeric or string
        states) to operator-set values from the Metric Tick window, after the
        random walk has run, and republish the module cache the API/SNMP read."""
        ov = self.device_overrides.get(device.id)
        if not ov:
            return
        ext = {k: val for k, val in ov.items()
               if k.startswith("ups_") or k.startswith("pdu_")}
        if not ext:
            return
        st = self._ext_states.get(device.name)
        if st is None:
            return
        st.update(ext)
        _ext_state_cache[device.name] = dict(st)

    # ------------------------------------------------------------------ #
    #  Inject Fault — gradual metric ramps that cross SNMP thresholds     #
    # ------------------------------------------------------------------ #

    # metric → (kind, field, clamp_lo, clamp_hi)
    #   dev : plain device attribute (int/float)
    #   pct : percentage backed by <field>_used / <field>_total
    #   ext : UPS/PDU extended-state dict entry
    _RAMP_METRICS = {
        "cpu_usage":           ("dev", "cpu_usage",           0.0, 100.0),
        "cpu_temp":            ("dev", "cpu_temp",            0.0, 120.0),
        "inlet_temp":          ("dev", "inlet_temp",          0.0,  60.0),
        "sensor_ambient_temp": ("dev", "inlet_temp",          0.0,  60.0),
        "humidity":            ("dev", "humidity",            0.0, 100.0),
        "outlet_temp":         ("dev", "outlet_temp",         0.0,  70.0),
        "mid_temp":            ("dev", "mid_temp",            0.0,  60.0),
        "memory_pct":          ("pct", "memory",              0.0, 100.0),
        "disk_pct":            ("pct", "disk",                0.0, 100.0),
        "ups_output_load":     ("ext", "ups_output_load",     0.0, 150.0),
        "ups_input_voltage":   ("ext", "ups_input_voltage",   0.0, 300.0),
        "pdu_load":            ("ext", "pdu_load",            0.0, 150.0),
        "pdu_voltage":         ("ext", "pdu_voltage",         0.0, 300.0),
        "pdu_outlet_current":  ("ext", "pdu_outlet_current",  0.0,  50.0),
    }

    def set_fault(self, device_id: str, metric: str, target: float,
                  rate: float) -> bool:
        """Start an Inject Fault ramp: ease *metric* toward *target* at *rate*
        per tick. Baseline (for the later ramp-down) is captured on the first
        tick. Returns False for an unknown metric."""
        if metric not in self._RAMP_METRICS:
            return False
        self._fault_ramps.setdefault(device_id, {})[metric] = {
            "target": float(target), "rate": abs(float(rate)),
            "baseline": None, "clearing": False,
        }
        return True

    def clear_fault(self, device_id: str, metric: str) -> bool:
        """Reverse a ramp toward its captured baseline. The record is removed
        once the baseline is reached (after the recovery threshold is crossed)."""
        r = self._fault_ramps.get(device_id, {}).get(metric)
        if not r:
            return False
        if r["baseline"] is not None:
            r["target"] = r["baseline"]
        r["clearing"] = True
        return True

    def get_faults(self, device_id: str) -> dict:
        """Active ramps for a device, keyed by metric (for the UI ACTIVE state)."""
        return {m: {"target": r["target"], "clearing": r["clearing"]}
                for m, r in self._fault_ramps.get(device_id, {}).items()}

    def _ramp_read(self, device: "Device", kind: str, field: str) -> float:
        if kind == "dev":
            return float(getattr(device, field, 0.0))
        if kind == "pct":
            total = max(1, getattr(device, f"{field}_total", 1))
            return getattr(device, f"{field}_used", 0) / total * 100.0
        if kind == "ext":
            return float(self._ext_states.get(device.name, {}).get(field, 0.0))
        return 0.0

    def _ramp_write(self, device: "Device", kind: str, field: str, value: float) -> None:
        if kind == "dev":
            cur = getattr(device, field, 0.0)
            setattr(device, field,
                    int(round(value)) if isinstance(cur, int) else round(value, 1))
        elif kind == "pct":
            total = getattr(device, f"{field}_total", 0)
            if total:
                setattr(device, f"{field}_used", int(total * value / 100.0))
        elif kind == "ext":
            st = self._ext_states.get(device.name)
            if st is not None:
                st[field] = round(value, 1)
                _ext_state_cache[device.name] = dict(st)

    def _apply_fault_ramps(self, device: "Device") -> None:
        """Advance each active fault ramp one tick. Runs after the walk and the
        static overrides so the injected value wins, and independent of the
        per-metric enable flag so an injection always progresses while the ticker
        runs."""
        ramps = self._fault_ramps.get(device.id)
        if not ramps:
            return
        done = []
        for metric, r in ramps.items():
            acc = self._RAMP_METRICS.get(metric)
            if acc is None:
                done.append(metric)
                continue
            kind, field, lo, hi = acc
            cur = self._ramp_read(device, kind, field)
            if r["baseline"] is None:                 # capture on first tick
                r["baseline"] = cur
            target, rate = r["target"], r["rate"]
            if cur < target:
                cur = min(target, cur + rate)
            elif cur > target:
                cur = max(target, cur - rate)
            cur = max(lo, min(hi, cur))
            self._ramp_write(device, kind, field, cur)
            # Clearing ramp that has reached baseline → fault fully resolved.
            if r["clearing"] and abs(cur - target) < max(0.5, rate):
                done.append(metric)
        for m in done:
            ramps.pop(m, None)
        if not ramps:
            self._fault_ramps.pop(device.id, None)

    # ------------------------------------------------------------------ #
    #  Extended state simulation (UPS, BGP)                              #
    # ------------------------------------------------------------------ #

    def _step_ext_state(self, device: "Device"):
        """Random-walk UPS and routing protocol states for a device."""
        name = device.name
        # _step_device already initialises the entry via setdefault; this guard
        # only fires if _step_ext_state is somehow called first.
        st = self._ext_states.setdefault(name, {
            "ups_status": "normal",
            "ups_output_load": random.uniform(20.0, 60.0),
            "ups_battery_status": "normal",
            "ups_input_voltage": random.uniform(216.0, 224.0),
            "ups_input_frequency": random.uniform(49.8, 50.2),
            "ups_fan_status": "ok",
            "ups_charger_status": "ok",
            "ups_rectifier_status": "ok",
            "ups_phase_status": "ok",
            "ups_bypass_status": "off",
            "ups_battery_health": random.uniform(92.0, 100.0),
            "ups_energy_kwh": 0.0,
            "pdu_load": random.uniform(30.0, 60.0),
            "pdu_voltage": random.uniform(216.0, 224.0),
            "pdu_power_factor": random.uniform(0.92, 0.98),
            "pdu_phase_imbalance": random.uniform(0.0, 5.0),
            "pdu_outlet_status": "on",
            "pdu_breaker_status": "ok",
            "pdu_outlet_failure": "ok",
            "pdu_smoke": "no",
            "pdu_outlet_current": random.uniform(5.0, 15.0),
            "pdu_ground_fault": "no",
            "pdu_frequency": random.uniform(49.8, 50.2),
            "pdu_temperature": random.uniform(18.0, 28.0),
            "pdu_humidity": random.uniform(30.0, 60.0),
            "pdu_energy_kwh": 0.0,
            "bgp_sessions": [],
            "water_detection": "dry",
            "cpu_sustained": False,
            "mem_sustained": False,
        })

        mf = self.metric_flags
        is_ups = device.device_type == DeviceType.UPS
        is_pdu = device.device_type in (DeviceType.PDU, DeviceType.FLOOR_PDU)

        # ── UPS ───────────────────────────────────────────────────────────
        if is_ups:
            if mf["ups_status"]:
                ups = st["ups_status"]
                if ups == "normal" and random.random() < 0.001:
                    st["ups_status"] = "on_battery"
                elif ups == "on_battery" and random.random() < 0.08:
                    st["ups_status"] = "low_battery"
                elif ups == "on_battery" and random.random() < 0.10:
                    st["ups_status"] = "normal"
                elif ups == "low_battery" and random.random() < 0.10:
                    st["ups_status"] = "normal"
            if mf["ups_status"]:
                st["ups_status"] = self._state_lock("ups_status", st["ups_status"])

            if mf["ups_output_load"]:
                load = st.get("ups_output_load", 40.0)
                if load > 90.0:
                    load = max(20.0, load + random.uniform(-8.0, -3.0))
                elif load >= 70.0:
                    load = max(20.0, load + random.uniform(-6.0, -2.0))
                else:
                    load = max(5.0, min(70.0, load + random.uniform(-3.0, 3.0)))
                    if random.random() < 0.005:
                        load = random.uniform(91.0, 99.0)
                st["ups_output_load"] = round(self._num_limit("ups_output_load", load), 1)

            if mf["ups_battery_status"]:
                bst = st.get("ups_battery_status", "normal")
                if bst == "normal":
                    r = random.random()
                    if r < 0.0005:
                        st["ups_battery_status"] = "failure"
                    elif r < 0.0008:
                        st["ups_battery_status"] = "disconnected"
                elif random.random() < 0.15:
                    st["ups_battery_status"] = "normal"
            if mf["ups_battery_status"]:
                st["ups_battery_status"] = self._state_lock("ups_battery_status", st["ups_battery_status"])

            if mf["ups_input_voltage"]:
                v = st.get("ups_input_voltage", 220.0)
                v = max(200.0, min(240.0, v + random.uniform(-2.0, 2.0)))
                if random.random() < 0.003:
                    v = random.choice([random.uniform(251.0, 260.0),
                                       random.uniform(180.0, 189.0)])
                st["ups_input_voltage"] = round(self._num_limit("ups_input_voltage", v), 1)

            if mf["ups_input_frequency"]:
                f = st.get("ups_input_frequency", 50.0)
                f = max(49.5, min(50.5, f + random.uniform(-0.05, 0.05)))
                if random.random() < 0.002:
                    f = random.choice([random.uniform(47.0, 48.9),
                                       random.uniform(51.1, 53.0)])
                st["ups_input_frequency"] = round(self._num_limit("ups_input_frequency", f), 2)

            for comp_key in ("ups_fan_status", "ups_charger_status",
                             "ups_rectifier_status", "ups_phase_status"):
                if not mf[comp_key]:
                    continue
                if st.get(comp_key, "ok") == "ok":
                    if random.random() < 0.001:
                        st[comp_key] = "failure"
                elif random.random() < 0.15:
                    st[comp_key] = "ok"
                st[comp_key] = self._state_lock(comp_key, st[comp_key])

            # Bypass: rare switch to bypass (e.g. maintenance / overload), self-clears
            if mf["ups_bypass_status"]:
                byp = st.get("ups_bypass_status", "off")
                if byp == "off" and random.random() < 0.0008:
                    st["ups_bypass_status"] = "on"
                elif byp == "on" and random.random() < 0.12:
                    st["ups_bypass_status"] = "off"
                st["ups_bypass_status"] = self._state_lock("ups_bypass_status", st["ups_bypass_status"])

            # Battery health (state-of-health %): slow monotonic decay, faster on fault
            if mf["ups_battery_health"]:
                hp = st.get("ups_battery_health", 100.0)
                decay = 0.05 if st.get("ups_battery_status", "normal") != "normal" else 0.002
                hp = max(0.0, hp - random.uniform(0.0, decay))
                st["ups_battery_health"] = round(self._num_limit("ups_battery_health", hp), 1)

            # Output energy accumulator (kWh): integrate ~3 kW frame at current % load,
            # assuming a ~1-minute tick interval. Flag gates accumulation (freeze counter).
            if mf["ups_energy_kwh"]:
                load_now = st.get("ups_output_load", 40.0)
                st["ups_energy_kwh"] = round(st.get("ups_energy_kwh", 0.0)
                                             + (load_now / 100.0) * 3.0 / 60.0, 3)

        # ── PDU / Floor PDU ───────────────────────────────────────────────
        if is_pdu:
            if mf["pdu_load"]:
                ld = st.get("pdu_load", 45.0)
                if ld > 90.0:
                    ld = max(30.0, ld + random.uniform(-8.0, -3.0))
                elif ld >= 80.0:
                    ld = max(30.0, ld + random.uniform(-5.0, -1.0))
                else:
                    ld = max(10.0, min(75.0, ld + random.uniform(-3.0, 3.0)))
                    if random.random() < 0.004:
                        ld = random.uniform(81.0, 98.0)
                st["pdu_load"] = round(self._num_limit("pdu_load", ld), 1)

            if mf["pdu_voltage"]:
                pv = st.get("pdu_voltage", 220.0)
                pv = max(205.0, min(235.0, pv + random.uniform(-2.0, 2.0)))
                if random.random() < 0.003:
                    pv = random.choice([random.uniform(241.0, 250.0),
                                        random.uniform(190.0, 199.0)])
                st["pdu_voltage"] = round(self._num_limit("pdu_voltage", pv), 1)

            if mf["pdu_power_factor"]:
                pf = st.get("pdu_power_factor", 0.95)
                pf = max(0.60, min(0.99, pf + random.uniform(-0.02, 0.02)))
                if random.random() < 0.003:
                    pf = random.uniform(0.50, 0.69)
                st["pdu_power_factor"] = round(pf, 3)

            if mf["pdu_phase_imbalance"]:
                pi = st.get("pdu_phase_imbalance", 2.0)
                pi = max(0.0, min(15.0, pi + random.uniform(-1.0, 1.0)))
                if random.random() < 0.003:
                    pi = random.uniform(21.0, 35.0)
                st["pdu_phase_imbalance"] = round(pi, 1)

            if mf["pdu_outlet_status"]:
                os_ = st.get("pdu_outlet_status", "on")
                if os_ == "on":
                    if random.random() < 0.001:
                        st["pdu_outlet_status"] = "off"
                elif random.random() < 0.30:
                    st["pdu_outlet_status"] = "on"
                st["pdu_outlet_status"] = self._state_lock("pdu_outlet_status", st["pdu_outlet_status"])

            if mf["pdu_breaker_status"]:
                if st.get("pdu_breaker_status", "ok") == "ok":
                    if random.random() < 0.001:
                        st["pdu_breaker_status"] = "tripped"
                elif random.random() < 0.25:
                    st["pdu_breaker_status"] = "ok"
                st["pdu_breaker_status"] = self._state_lock("pdu_breaker_status", st["pdu_breaker_status"])

            if mf["pdu_outlet_failure"]:
                if st.get("pdu_outlet_failure", "ok") == "ok":
                    if random.random() < 0.001:
                        st["pdu_outlet_failure"] = "failed"
                elif random.random() < 0.25:
                    st["pdu_outlet_failure"] = "ok"
                st["pdu_outlet_failure"] = self._state_lock("pdu_outlet_failure", st["pdu_outlet_failure"])

            if mf["pdu_smoke"]:
                if st.get("pdu_smoke", "no") == "no":
                    if random.random() < 0.0001:
                        st["pdu_smoke"] = "yes"
                elif random.random() < 0.05:
                    st["pdu_smoke"] = "no"
                st["pdu_smoke"] = self._state_lock("pdu_smoke", st["pdu_smoke"])

            if mf["pdu_outlet_current"]:
                oc = st.get("pdu_outlet_current", 10.0)
                oc = max(1.0, min(18.0, oc + random.uniform(-1.0, 1.0)))
                if random.random() < 0.003:
                    oc = random.uniform(21.0, 28.0)
                st["pdu_outlet_current"] = round(self._num_limit("pdu_outlet_current", oc), 1)

            if mf["pdu_ground_fault"]:
                if st.get("pdu_ground_fault", "no") == "no":
                    if random.random() < 0.0005:
                        st["pdu_ground_fault"] = "yes"
                elif random.random() < 0.20:
                    st["pdu_ground_fault"] = "no"
                st["pdu_ground_fault"] = self._state_lock("pdu_ground_fault", st["pdu_ground_fault"])

            if mf["pdu_frequency"]:
                f = st.get("pdu_frequency", 50.0)
                f = max(49.5, min(50.5, f + random.uniform(-0.05, 0.05)))
                if random.random() < 0.002:
                    f = random.choice([random.uniform(47.0, 48.9),
                                       random.uniform(51.1, 53.0)])
                st["pdu_frequency"] = round(self._num_limit("pdu_frequency", f), 2)

            # PDU intake probe sits in the cold aisle, so it mean-reverts toward
            # the ~23 °C supply rather than drifting up (a high drift would re-
            # appear as a false hot rack via the floor-plan inlet max()).
            if mf["pdu_temperature"]:
                t = st.get("pdu_temperature", 23.0)
                t = max(18.0, min(30.0, t + (23.0 - t) * 0.08 + random.uniform(-0.25, 0.25)))
                st["pdu_temperature"] = round(self._num_limit("pdu_temperature", t), 1)

            # RH mean-reverts to the controlled ~50% setpoint (see sensor humidity).
            if mf["pdu_humidity"]:
                h = st.get("pdu_humidity", 45.0)
                h = max(35.0, min(65.0, h + (50.0 - h) * 0.05 + random.uniform(-0.8, 0.8)))
                st["pdu_humidity"] = round(self._num_limit("pdu_humidity", h), 1)

            # Energy accumulator: integrate kW load × 1-min tick
            if mf["pdu_energy_kwh"]:
                load_now = st.get("pdu_load", 45.0)
                volt_now = st.get("pdu_voltage", 220.0)
                cur_now  = st.get("pdu_outlet_current", 10.0)
                pf_now   = st.get("pdu_power_factor", 0.95)
                real_kw  = (volt_now * cur_now * pf_now) / 1000.0
                st["pdu_energy_kwh"] = round(st.get("pdu_energy_kwh", 0.0)
                                             + real_kw / 60.0, 3)

        # Update module-level cache so snmprec_generator can read UPS/PDU states
        _ext_state_cache[name] = dict(st)

        # ── Sensor — water detection (Raritan DPX2-CC2 only) ─────────────
        if device.device_type == DeviceType.SENSOR and device.model_name == "Raritan DPX2-CC2":
            if mf["water_detection"]:
                wd = st.get("water_detection", "dry")
                if wd == "dry" and random.random() < 0.0005:
                    st["water_detection"] = "wet"
                elif wd == "wet" and random.random() < 0.20:
                    st["water_detection"] = "dry"
                st["water_detection"] = self._state_lock("water_detection", st.get("water_detection", "dry"))

        # BGP sessions: only for routers and firewalls
        if mf["bgp_sessions"] and device.device_type.value in ("router", "firewall"):
            sessions = st["bgp_sessions"]
            if not sessions:
                # Initialise 1-3 BGP peers on first tick
                count = random.randint(1, 3)
                sessions[:] = [
                    {"peer": f"10.{random.randint(1,254)}.{random.randint(1,254)}.1",
                     "state": "established"}
                    for _ in range(count)
                ]
            else:
                for sess in sessions:
                    if sess["state"] == "established" and random.random() < 0.005:
                        sess["state"] = "idle"
                    elif sess["state"] != "established" and random.random() < 0.15:
                        sess["state"] = "established"
            _lim_bgp = self.metric_limits.get("bgp_sessions")
            if _lim_bgp and _lim_bgp["enabled"]:
                for sess in st.get("bgp_sessions", []):
                    sess["state"] = _lim_bgp["lock"]


    # ------------------------------------------------------------------ #
    #  Rule engine fact publishing                                        #
    # ------------------------------------------------------------------ #

    def _publish_facts(self, devices: list):
        """Build a DeviceFact for each device and invoke the rule engine callback."""
        try:
            from core.fact_model import DeviceFact, InterfaceFact, BGPSessionFact
            now = time.time()
            for device in devices:
                # A powered-off server has no OS/NOS agent — publish nothing,
                # so rules can't fire phantom traps (recovery, linkDown) from
                # a dead host. The BMC power trap uses the direct send path.
                if (device.device_type == DeviceType.SERVER
                        and getattr(device, "power_state", "On") == "Off"):
                    continue
                ext = self._ext_states.get(device.name, {})
                mem_pct = (device.memory_used / max(1, device.memory_total)) * 100.0
                disk_pct = (device.disk_used / max(1, device.disk_total)) * 100.0

                rack_id = ""
                if device.datacenter and device.rack_row and device.rack_num:
                    rack_id = f"{device.datacenter}:R{device.rack_row}:RACK{device.rack_num}"

                fact = DeviceFact(
                    device_id=device.name,
                    device_type=device.device_type.value,
                    model_name=device.model_name,
                    ip_address=device.ip_address,
                    timestamp=now,
                    cpu_usage=float(device.cpu_usage),
                    memory_usage=round(mem_pct, 1),
                    disk_usage=round(disk_pct, 1),
                    interfaces=[
                        InterfaceFact(index=i.index, name=i.name,
                                      oper_status=i.oper_status)
                        for i in device.interfaces
                    ],
                    temperature=float(device.cpu_temp),
                    ambient_temp=float(device.inlet_temp),
                    humidity=float(device.humidity),
                    dewpoint=float(device.dewpoint),
                    airflow=float(device.airflow),
                    mid_temp=float(device.mid_temp),
                    outlet_temp=float(device.outlet_temp),
                    ups_status=ext.get("ups_status", "normal"),
                    ups_output_load=float(ext.get("ups_output_load", 0.0)),
                    ups_battery_status=ext.get("ups_battery_status", "normal"),
                    ups_input_voltage=float(ext.get("ups_input_voltage", 220.0)),
                    ups_input_frequency=float(ext.get("ups_input_frequency", 50.0)),
                    ups_fan_status=ext.get("ups_fan_status", "ok"),
                    ups_charger_status=ext.get("ups_charger_status", "ok"),
                    ups_rectifier_status=ext.get("ups_rectifier_status", "ok"),
                    ups_phase_status=ext.get("ups_phase_status", "ok"),
                    ups_operating_mode=(
                        "bypass" if ext.get("ups_bypass_status", "off") == "on"
                        else "battery" if ext.get("ups_status", "normal") in ("on_battery", "low_battery")
                        else "online"),
                    ups_bypass_status=ext.get("ups_bypass_status", "off"),
                    ups_battery_health=float(ext.get("ups_battery_health", 100.0)),
                    ups_output_apparent_power=float(ext.get("ups_output_load", 0.0)) * 30.0,
                    ups_energy_kwh=float(ext.get("ups_energy_kwh", 0.0)),
                    pdu_load=float(ext.get("pdu_load", 0.0)),
                    pdu_voltage=float(ext.get("pdu_voltage", 220.0)),
                    pdu_power_factor=float(ext.get("pdu_power_factor", 0.95)),
                    pdu_phase_imbalance=float(ext.get("pdu_phase_imbalance", 0.0)),
                    pdu_outlet_status=ext.get("pdu_outlet_status", "on"),
                    pdu_breaker_status=ext.get("pdu_breaker_status", "ok"),
                    pdu_outlet_failure=ext.get("pdu_outlet_failure", "ok"),
                    pdu_smoke=ext.get("pdu_smoke", "no"),
                    pdu_outlet_current=float(ext.get("pdu_outlet_current", 0.0)),
                    pdu_ground_fault=ext.get("pdu_ground_fault", "no"),
                    pdu_frequency=float(ext.get("pdu_frequency", 50.0)),
                    pdu_temperature=float(ext.get("pdu_temperature", 0.0)),
                    pdu_humidity=float(ext.get("pdu_humidity", 0.0)),
                    pdu_energy_kwh=float(ext.get("pdu_energy_kwh", 0.0)),
                    bgp_sessions=[
                        BGPSessionFact(peer_addr=s["peer"], state=s["state"])
                        for s in ext.get("bgp_sessions", [])
                    ],
                    rack_id=rack_id,
                    datacenter=device.datacenter or "",
                )
                try:
                    self._rule_engine_cb(fact, device)
                except Exception:
                    log.exception("[StateStore] Rule engine callback error for %s", device.name)
        except Exception:
            log.exception("[StateStore] _publish_facts error")

    # ------------------------------------------------------------------ #
    #  SNMP file sync                                                      #
    # ------------------------------------------------------------------ #

    def _sync_snmp(self, devices: list):
        """
        Patch dynamic metric OIDs in .snmprec files for a rotating shard of
        devices rather than all of them at once.

        Writing all N files in one burst causes an I/O storm that starves the
        Windows kernel scheduler and freezes the cursor on large topologies.
        Spreading writes across multiple ticks eliminates the burst: each tick
        processes at most BATCH_MAX devices, cycling through the full list over
        ceil(N / BATCH_MAX) ticks.  At the default 60-second tick interval this
        means every device is refreshed at most every few minutes — acceptable
        for an SNMP simulator whose consumers poll on their own schedule anyway.

        Worker threads run at below-normal OS priority so disk I/O from this
        background task cannot starve the Qt event loop or the system cursor.
        """
        total = len(devices)
        if total == 0:
            return

        # At most 100 files per sync cycle; for small topologies process all.
        BATCH_MAX = 100
        num_shards = max(1, (total + BATCH_MAX - 1) // BATCH_MAX)
        shard_idx  = (self._tick_count - 1) % num_shards
        batch      = devices[shard_idx * BATCH_MAX : (shard_idx + 1) * BATCH_MAX]
        if not batch:
            return

        try:
            import sys as _sys
            import ctypes as _ctypes
            from core.snmprec_generator import SNMPRecGenerator
            from concurrent.futures import ThreadPoolExecutor

            snmp_gen = SNMPRecGenerator(self._datasets_dir)

            def _patch_below_normal(device):
                # Depress this worker thread's priority so the UI and cursor
                # remain responsive even when the disk is under write pressure.
                if _sys.platform == "win32":
                    try:
                        _ctypes.windll.kernel32.SetThreadPriority(
                            _ctypes.windll.kernel32.GetCurrentThread(), -1
                        )
                    except Exception:
                        pass
                else:
                    try:
                        import os as _os
                        _os.nice(10)
                    except Exception:
                        pass
                snmp_gen.patch_metrics(device)
                # Server BMC SNMP agent (mgmt IP) — refresh even while the
                # chassis is Off; the BMC runs on standby power.
                if device.device_type == DeviceType.SERVER:
                    snmp_gen.patch_bmc_metrics(device)

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(_patch_below_normal, batch))

            log.debug(
                "[StateStore] SNMP sync — %d/%d file(s) patched (shard %d/%d).",
                len(batch), total, shard_idx + 1, num_shards,
            )
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