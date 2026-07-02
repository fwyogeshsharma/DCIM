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

        # Per-device disk baseline (fraction 0–1). Disk hovers around this with
        # small jitter instead of growth-walking to the 90 % cap, so the fleet
        # shows varied, realistic disk usage rather than every server pinned full.
        self._disk_anchor: Dict[str, float] = {}

        # Live power cascade. _power_ctx caches the power-graph structure +
        # per-PDU/UPS rated capacity (built once); _through_live[device_id] is the
        # live watts flowing through each node, recomputed each tick by summing
        # live server draw bottom-up. PDU load/current, UPS output load and the
        # EV2 panels read it so a server load change ripples up the hierarchy.
        self._power_ctx: "Optional[dict]" = None
        # Per-node breaker/nameplate rating (W), FROZEN at install. Unlike
        # _power_ctx this survives invalidation — ratings are fixed at build time,
        # so as the fleet adds load the node's load% climbs toward overload
        # instead of the rating re-sizing itself to the new load.
        self._rated_w_frozen: Dict[str, float] = {}
        self._through_live: Dict[str, float] = {}
        self._ev2_live_kw: Dict[str, float] = {}   # {ev2_ip: live downstream kW}
        self._ev2_circuit_kw: Dict[str, list] = {} # {ev2_ip: [per-circuit live kW]}
        self._plant_power_by_name: Dict[str, float] = {}  # {plant_name: live cooling kW}
        self._plant_cop_by_name: Dict[str, float] = {}    # {chiller_name: live COP}
        self._facility_w: float = 0.0   # whole-DC draw (IT + cooling) for PUE
        self._it_w: float = 0.0         # IT-only draw for PUE denominator

        # Autonomous fault generation. OFF by default: the random walk keeps every
        # device healthy (metrics jitter inside safe bands, no state flips), so NO
        # SNMP trap fires unless the user injects a fault (Simulate Fault / Send
        # Trap). Flip on for a realistic "live monitoring" demo where the sim
        # spontaneously breaches thresholds. The rule engine itself is always on.
        self.autonomous_faults: bool = False

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

    # Power-chain rank: source (0) → leaf load (4). A device's parents are its
    # lower-rank power neighbours (the feeds above it). Mirrors api/routers/bacnet.py.
    _POWER_RANK = {"generator": 0, "ups": 1, "rpp": 2, "floor_pdu": 2, "pdu": 3}
    # Leaf IT load types whose live wattage drives the cascade.
    _IT_LEAF_TYPES = {"server", "switch", "router", "firewall",
                      "load_balancer", "oob_switch"}
    # Cooling-plant types — electrical loads counted toward facility / PUE.
    _COOLING_TYPES = {"crah", "chiller", "pump", "cooling_tower", "cdu"}
    # Per-plant-type BACnet point that reports its live electrical draw (kW).
    _PLANT_POWER_POINTS = ("Active_Power", "Motor_Power", "Pump_Power", "Fan_Power")
    _UPS_DESIGN_MIN  = 8.0      # UPS autonomy (min) at full load, healthy battery
    _GEN_FULL_HOURS  = 24.0     # genset full-tank runtime (h) at full load

    def _power_context(self) -> dict:
        """Cached power-graph structure + per-node rated capacity, built once.
          children {id: [downstream ids]}     — loads fed by this node
          parents  {id: [upstream feed ids]}  — feeds above this node
          rank     {id: int}
          rated_w  {id: W}                     — breaker rating (design peak ÷ 0.8)
          peak_w   {id: W}                     — nameplate sum flowing through (full load)
        Redundant feeds split a load equally among parents, so summing both A/B
        meters recovers the full load instead of double-counting (matches bacnet.py).
        """
        # Self-healing cache: rebuild whenever the device set or the power-edge
        # count changes, so a topology load / device add / feed edit ripples into
        # the cascade even if the caller forgot to invalidate. Explicit
        # invalidate_power_context() still forces a rebuild (sets _power_ctx None).
        topo = self._topology
        try:
            _sig = (len(self._dm.get_all_devices()),
                    len(topo.get_edges_by_layer("power")) if topo else 0)
        except Exception:
            _sig = None
        if self._power_ctx is not None and getattr(self, "_power_ctx_sig", None) == _sig:
            return self._power_ctx
        children: Dict[str, list] = {}
        parents: Dict[str, list] = {}
        rank: Dict[str, int] = {}
        peak_w: Dict[str, float] = {}
        rated_w: Dict[str, float] = {}
        ev2_ip_panel: Dict[str, str] = {}
        ev2_meters: list = []
        ev2_circuit_pdus: Dict[str, list] = {}
        gen_ups: Dict[str, list] = {}
        try:
            _devs = self._dm.get_all_devices()
            id_dev = {d.id: d for d in _devs}
            id_type = {d.id: d.device_type.value for d in _devs}
            id_draw = {d.id: float(getattr(d, "power_draw_w", 0) or 0) for d in _devs}
            id_rated = {d.id: float(getattr(d, "rated_power_w", 0) or 0) for d in _devs}
            for i in id_type:
                rank[i] = self._POWER_RANK.get(id_type[i], 4)
            edges = topo.get_edges_by_layer("power") if topo else []

            def _parents(i):
                out = []
                for u, v, _w in edges:
                    if i in (u, v):
                        nb = v if u == i else u
                        if id_type.get(nb) == "energy_monitor":
                            continue                 # meters clamp on, don't feed
                        if rank.get(nb, 4) < rank.get(i, 4):
                            out.append(nb)
                return out

            for i in id_type:
                parents[i] = _parents(i)
            for u, v, _w in edges:
                pu, pv = rank.get(u, 4), rank.get(v, 4)
                if pu < pv:
                    children.setdefault(u, []).append(v)
                elif pv < pu:
                    children.setdefault(v, []).append(u)

            # Nameplate peak flowing through each node (leaf→root, redundancy split).
            incoming: Dict[str, float] = {}
            for nid in sorted(id_type, key=lambda x: rank.get(x, 4), reverse=True):
                thr = id_draw.get(nid, 0.0) + incoming.get(nid, 0.0)
                peak_w[nid] = thr
                ps = parents.get(nid, [])
                if ps and thr:
                    share = thr / len(ps)
                    for p in ps:
                        incoming[p] = incoming.get(p, 0.0) + share
            # Breaker/nameplate rating, FIXED at install so load% climbs as the
            # fleet grows (not re-sized to the new load). Precedence per node:
            #   1. explicit device.rated_power_w  — hand-set or inherited nameplate
            #   2. a value frozen on a previous build — the install baseline
            #   3. first-time derive: design peak ÷ 0.8 (so a fully-loaded chain
            #      reads ~80 %, idle ~44 %). Freeze + stamp it so it never re-sizes.
            # Only power-distribution/backup nodes are rated+frozen; IT leaves are
            # loads, not distribution, so their (unused) rated_w stays transient.
            for nid, pk in peak_w.items():
                explicit = id_rated.get(nid, 0.0)
                if explicit > 0:
                    rated_w[nid] = explicit
                    continue
                frozen = self._rated_w_frozen.get(nid, 0.0)
                if frozen > 0:
                    rated_w[nid] = frozen
                    continue
                r = (pk / 0.8) if pk > 0 else 0.0
                rated_w[nid] = r
                # Freeze only once a power node actually carries load, so a node
                # seen before its downstream is wired doesn't lock in a 0 rating.
                if r > 0 and id_type.get(nid) in self._POWER_RANK:
                    self._rated_w_frozen[nid] = r
                    dev = id_dev.get(nid)
                    if dev is not None:
                        dev.rated_power_w = int(r)   # stamp so clones inherit it

            # EV2 meter IP → the panel/PDU it clamps onto (its power neighbour), so
            # the live downstream load can be fed to each EV2 telemetry engine.
            for d in self._dm.get_all_devices():
                if d.device_type.value != "energy_monitor":
                    continue
                nb = next((v if u == d.id else u
                           for u, v, _w in edges if d.id in (u, v)), None)
                ip = d.ip_address or getattr(d, "mgmt_ip", None)
                if nb and ip:
                    ev2_ip_panel[ip] = nb

            # Ordered circuit → downstream-PDU map per EV2, matching the display
            # order used by the API (the clamp panel's downstream power neighbours,
            # excluding the meter and any upstream UPS/generator, sorted by name).
            # Circuit i meters ev2_circuit_pdus[ip][i-1], so each circuit reflects
            # the REAL branch load it clamps — an empty rack PDU reads 0, a full one
            # reads its live kW — instead of a panel-scaled random walk.
            _up_types = {"ups", "generator"}
            for d in self._dm.get_all_devices():
                if d.device_type.value != "energy_monitor":
                    continue
                ip = d.ip_address or getattr(d, "mgmt_ip", None)
                panel = ev2_ip_panel.get(ip)
                if not ip or panel is None:
                    continue
                nbr_ids = [(u if v == panel else v) for u, v, _w in edges
                           if panel in (u, v) and d.id not in (u, v)]
                brs = [id_dev.get(nid) for nid in nbr_ids]
                brs = [b for b in brs if b is not None
                       and id_type.get(b.id) not in _up_types]
                brs.sort(key=lambda b: b.name or "")
                ev2_circuit_pdus[ip] = [b.id for b in brs]

            # Classify each EV2 meter by what its panel's subtree contains:
            #   • "main" (building feed): sees BOTH IT load and central cooling
            #     plant — a whole-facility meter (e.g. on the generator/utility
            #     feed). Its reading = total facility power.
            #   • "it" sub-meter: IT load, no central plant.
            #   • "cool" sub-meter: central plant, no IT.
            # PUE = Σ main ÷ Σ IT when a whole-facility meter exists; otherwise
            # facility = Σ(IT)+Σ(cool) from the non-overlapping branch sub-meters
            # (see get_power_summary). Central plant is BULK mechanical only
            # (chiller/tower/pump/CRAH); a CDU is in-rack direct-to-chip cooling fed
            # from the IT PDU, so it must NOT flag an IT branch as a facility meter.
            _central_ids = {i for i, t in id_type.items()
                            if t in ("crah", "chiller", "pump", "cooling_tower")}
            _it_ids = {i for i, t in id_type.items() if t in self._IT_LEAF_TYPES}

            def _subtree(root):
                seen, stack = set(), [root]
                while stack:
                    n = stack.pop()
                    for c in children.get(n, []):
                        if c not in seen:
                            seen.add(c)
                            stack.append(c)
                return seen

            for ip, panel in ev2_ip_panel.items():
                sub = _subtree(panel)
                has_it    = bool(sub & _it_ids)
                has_plant = bool(sub & _central_ids)
                role = ("main" if (has_it and has_plant)
                        else "it" if has_it
                        else "cool" if has_plant else "other")
                ev2_meters.append({
                    "ip": ip, "panel": panel, "role": role,
                    "facility": role in ("main", "cool"),   # legacy key
                })

            # Generator → names of the UPS units downstream of it. A genset starts
            # when utility is lost, which the sim sees as a downstream UPS going
            # on-battery — so this maps the trigger.
            ups_ids = {i for i, t in id_type.items() if t == "ups"}
            for gid, t in id_type.items():
                if t == "generator":
                    subups = _subtree(gid) & ups_ids
                    gen_ups[gid] = [self._dm.get_device(i).name for i in subups
                                    if self._dm.get_device(i)]
        except Exception:
            log.exception("[StateStore] power context build error")

        self._power_ctx = {"children": children, "parents": parents,
                           "rank": rank, "peak_w": peak_w, "rated_w": rated_w,
                           "ev2_ip_panel": ev2_ip_panel, "ev2_meters": ev2_meters,
                           "ev2_circuit_pdus": ev2_circuit_pdus, "gen_ups": gen_ups}
        self._power_ctx_sig = _sig
        return self._power_ctx

    def invalidate_power_context(self) -> None:
        """Drop the cached power graph so it rebuilds on the next tick. Call this
        whenever the power topology changes — a device (server/PDU/RPP) is added
        or removed, or its power feeds change — otherwise the bottom-up load
        cascade keeps walking a stale graph and new IT load never reaches the
        PDU/UPS/RPP/EV2 meters that feed it.

        Node RATINGS (_rated_w_frozen) are deliberately NOT cleared here: a
        breaker's rating is fixed at install, so as the rebuilt graph carries more
        fleet load each node's load% climbs toward overload instead of re-sizing."""
        self._power_ctx = None

    def _server_live_watts(self, device: "Device") -> float:
        """Per-leaf live draw: nameplate scaled by CPU load (idle ~55 %, full
        100 %) — the same curve Redfish _live_watts reports. 0 if powered off."""
        if getattr(device, "power_state", "On") == "Off":
            return 0.0
        nominal = float(getattr(device, "power_draw_w", 0) or 0)
        if nominal <= 0:
            return 0.0
        load = max(0.0, min(1.0, getattr(device, "cpu_usage", 0) / 100.0))
        return nominal * (0.55 + 0.45 * load)

    def get_power_summary(self) -> dict:
        """Live facility power + PUE, derived from the EV2 METER readings the way a
        real DCIM does it: PUE = Σ facility-meter kW ÷ Σ IT-sub-meter kW. Each EV2
        reports the live load through the panel it clamps (self._through_live). If
        the topology has no proper meter hierarchy, fall back to the internal
        IT/facility power sums so the value is still populated."""
        ctx = self._power_context()
        through = self._through_live
        it_m = main_m = cool_m = 0.0
        for m in ctx.get("ev2_meters", []):
            kw = through.get(m["panel"], 0.0) / 1000.0
            role = m.get("role") or ("main" if m.get("facility") else "it")
            if role == "it":
                it_m += kw           # IT branch sub-meter
            elif role == "main":
                main_m += kw         # building-feed meter — whole facility (IT+cooling)
            elif role == "cool":
                cool_m += kw         # cooling-plant sub-meter

        # CDU reclassification. A CDU (in-rack coolant distribution) is fed from the
        # IT PDU, so its pump draw rides an IT-role sub-meter and would be counted as
        # IT load — understating PUE. Its work is mechanical cooling overhead (Green
        # Grid/ASHRAE put it in the numerator), and the computed path (_COOLING_TYPES)
        # already treats it as cooling. Shift live CDU watts IT→cool so the meter PUE
        # matches: facility is unchanged (sub-meter sum is conserved), only the IT
        # denominator drops. Bounded by the metered IT so we never go negative.
        cdu_kw = 0.0
        try:
            for d in self._dm.get_all_devices():
                if d.device_type.value == "cdu":
                    cdu_kw += self._plant_watts(d.name) / 1000.0
        except Exception:
            log.exception("[StateStore] CDU reclassification error")
        cdu_kw = max(0.0, min(cdu_kw, it_m))
        it_m   -= cdu_kw
        cool_m += cdu_kw

        # Facility power from meters. The branch sub-meters (IT + cooling) are
        # non-overlapping and together cover the whole load, so their sum is the
        # primary facility figure. A building-main meter is only a cross-check: on
        # a redundant A/B feed a single metered main reads just its side (which can
        # be LESS than the full sub-meter sum), so take the larger of the two
        # rather than trusting an under-metered main alone.
        sub_fac = it_m + cool_m
        fac_m = max(sub_fac, main_m)
        # A trustworthy facility reading must be ≥ IT (the facility carries IT +
        # cooling). If it isn't, metering is incomplete → fall back to computed.
        metered = it_m > 0 and fac_m >= it_m
        it_w  = it_m * 1000.0 if it_m > 0 else self._it_w
        fac_w = fac_m * 1000.0 if fac_m > 0 else self._facility_w
        pue = (fac_w / it_w) if it_w > 0 else 0.0
        return {
            "it_watts":       round(it_w, 1),
            "cooling_watts":  round(max(0.0, fac_w - it_w), 1),
            "facility_watts": round(fac_w, 1),
            "pue":            round(pue, 3),
            "source":         "meters" if metered else "computed",
        }

    def _step_generator(self, device: "Device", st: dict) -> None:
        """Live generator state. A genset is in STANDBY until utility is lost —
        which the sim sees as a UPS downstream of it going on-battery. While
        running it carries the live downstream load (server→…→generator through),
        burns fuel proportional to that load, and accrues run-hours; the fuel
        level sets the remaining runtime. Written to the ext-state cache so
        snmprec serves the live OIDs."""
        ctx = self._power_context()
        ups_names = ctx.get("gen_ups", {}).get(device.id, [])
        # Utility lost → any downstream UPS on battery → start.
        on_outage = any(
            (self._ext_states.get(n, {}) or _ext_state_cache.get(n, {})).get("ups_status")
            in ("on_battery", "low_battery")
            for n in ups_names
        )
        rated = self._power_context()["rated_w"].get(device.id, 0.0)
        thr   = self._through_live.get(device.id, 0.0)
        dt_h  = self._tick_interval / 3600.0

        if on_outage and st.get("gen_fuel_pct", 0.0) > 0.5:
            if not st.get("gen_was_running"):
                st["gen_start_attempts"] = int(st.get("gen_start_attempts", 0)) + 1
                st["gen_was_running"] = True
            st["gen_status"] = "running"
            load_pct = (thr / rated * 100.0) if rated > 0 else 0.0
            st["gen_load_pct"] = round(max(0.0, min(100.0, load_pct)), 1)
            st["gen_kw"]       = round(thr / 1000.0, 1)
            st["gen_run_hours"] = round(st.get("gen_run_hours", 0.0) + dt_h, 3)
            # Fuel burn ∝ load; full-tank lasts _GEN_FULL_HOURS at full load.
            burn = (max(0.0, st["gen_load_pct"]) / 100.0) * dt_h / self._GEN_FULL_HOURS * 100.0
            st["gen_fuel_pct"] = round(max(0.0, st.get("gen_fuel_pct", 0.0) - burn), 2)
            lf = max(0.05, st["gen_load_pct"] / 100.0)
            st["gen_runtime_min"] = round(st["gen_fuel_pct"] / 100.0
                                          * self._GEN_FULL_HOURS / lf * 60.0, 1)
        else:
            st["gen_was_running"] = False
            st["gen_status"] = "fault" if st.get("gen_fuel_pct", 100.0) <= 0.5 else "standby"
            st["gen_load_pct"] = 0.0
            st["gen_kw"] = 0.0
            st["gen_runtime_min"] = 0.0
        _ext_state_cache[device.name] = dict(st)

    def _plant_watts(self, name: str) -> float:
        """Live electrical draw (W) of a cooling-plant unit from its BACnet
        telemetry (kW point → W). 0 if not running / unknown."""
        pv = _plant_state_cache.get(name)
        if not pv:
            return 0.0
        for k in self._PLANT_POWER_POINTS:
            if k in pv:
                try:
                    return float(pv[k]) * 1000.0
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _compute_power_flow(self) -> None:
        """Per tick: sum live IT load bottom-up through the power graph so each
        PDU/UPS/EV2 node carries the real watts flowing through it right now.
        Also totals IT vs facility (IT + cooling plant) draw for live PUE."""
        ctx = self._power_context()
        rank, parents = ctx["rank"], ctx["parents"]
        through: Dict[str, float] = {}
        incoming: Dict[str, float] = {}
        it_w = 0.0
        cool_w = 0.0
        try:
            from collections import defaultdict as _dd
            devices = self._dm.get_all_devices()
            own: Dict[str, float] = {}
            # Per-datacenter tallies for the load-/weather-coupled cooling model.
            it_live_dc: Dict[str, float] = _dd(float)   # live IT heat per DC
            inlet_sum_dc: Dict[str, float] = _dd(float) # Σ server inlet temp per DC
            inlet_n_dc: Dict[str, int] = _dd(int)       # server count per DC (for avg)
            dc_city: Dict[str, str] = {}
            plant_dc: Dict[str, list] = _dd(list)       # DC → [(name, nameplate_w, type)]
            for d in devices:
                dtv = d.device_type.value
                _dc = getattr(d, "datacenter", None) or "?"
                if _dc not in dc_city:
                    dc_city[_dc] = getattr(d, "datacenter_city", None)
                if dtv in self._IT_LEAF_TYPES:
                    w = self._server_live_watts(d)
                    own[d.id] = w
                    it_w += w
                    it_live_dc[_dc] += w
                    _inl = getattr(d, "inlet_temp", None)
                    if _inl is not None:
                        inlet_sum_dc[_dc] += float(_inl)
                        inlet_n_dc[_dc] += 1
                elif dtv in self._COOLING_TYPES:
                    # Cooling plant is also an electrical load on the power graph,
                    # so a facility meter downstream reads IT + cooling → PUE > 1.
                    w = self._plant_watts(d.name)
                    if w > 0:
                        own[d.id] = w
                        cool_w += w
                    plant_dc[_dc].append(
                        (d.name, float(getattr(d, "power_draw_w", 0) or 0), dtv))
            for nid in sorted(rank, key=lambda x: rank.get(x, 4), reverse=True):
                thr = own.get(nid, 0.0) + incoming.get(nid, 0.0)
                through[nid] = thr
                ps = parents.get(nid, [])
                if ps and thr:
                    share = thr / len(ps)
                    for p in ps:
                        incoming[p] = incoming.get(p, 0.0) + share

            # ── Load-/weather-coupled cooling power ──────────────────────────
            # Size each DC's total cooling electrical from its live IT heat + site
            # ambient (cooling_model), then split across the DC's plant units by
            # nameplate share. Fed to the plant engines next tick, so cooling draw
            # tracks the real IT load and the location's weather instead of a fixed
            # nameplate × clock curve.
            from core.cooling_model import (
                cooling_electrical_w, crah_fan_speed_ratio, vfd_speed_frac,
                affinity_power_kw, chiller_electrical_w, chiller_cop,
                PUMP_MIN_SPEED, FAN_MIN_SPEED, OH_FLOOR, OH_VAR)
            _oh_design = (OH_FLOOR + OH_VAR) or 0.47
            _VFD_FAN  = ("crah", "cooling_tower")   # centrifugal fans
            _VFD_PUMP = ("pump", "cdu")             # centrifugal pumps
            plant_power: Dict[str, float] = {}
            plant_cop: Dict[str, float] = {}
            for _dc, units in plant_dc.items():
                itl = it_live_dc.get(_dc, 0.0)
                np_sum = sum(w for _n, w, _t in units) or 1.0
                # Design IT capacity is set by the INSTALLED cooling plant (fixed),
                # not the live server population: a plant of nameplate P cools
                # IT_design = P / 0.47 at design PUE. So adding IT raises load toward
                # this fixed ceiling (PUE → 1.47), and cooling caps at P.
                itd = np_sum / _oh_design
                total_w = cooling_electrical_w(itl, itd, dc_city.get(_dc))
                # Plant-wide duty fraction: how hard the plant works vs its installed
                # nameplate. 1.0 at design (total_w == np_sum), <1 at part load. Sets
                # the VFD speed of every pump/fan in the DC.
                lf = min(1.0, total_w / np_sum)
                # Thermal part-load ratio (ambient-free) for the chiller kW/ton
                # curve: live IT heat vs the design IT the plant is sized to reject.
                plr = (itl / itd) if itd > 0 else 0.0
                _city = dc_city.get(_dc)
                # Hall inlet/return air temp → CRAH fan SPEED ramp (more airflow when
                # hot). The cube-law POWER cost is applied once, by affinity_power_kw
                # below — no double-cube.
                avg_inlet = (inlet_sum_dc.get(_dc, 0.0) / inlet_n_dc[_dc]
                             if inlet_n_dc.get(_dc) else 24.0)
                fan_spd_ratio = crah_fan_speed_ratio(avg_inlet)
                for _n, w, _t in units:
                    if _t in _VFD_FAN or _t in _VFD_PUMP:
                        # VFD centrifugal pump/fan — affinity law P ∝ speed³. Speed
                        # tracks the thermal duty (flow ∝ speed), floored at the drive
                        # turndown; CRAHs push extra airflow when the hall is hot. Draw
                        # equals nameplate only at full speed, far less when throttled.
                        duty = lf * fan_spd_ratio if _t == "crah" else lf
                        _min = FAN_MIN_SPEED if _t in _VFD_FAN else PUMP_MIN_SPEED
                        spd  = vfd_speed_frac(duty, _min)
                        tgt_w = affinity_power_kw(w, spd)
                    elif _t == "chiller":
                        # Chiller — part-load kW/ton curve × ambient/condenser
                        # factor. Compressor power is U-shaped in efficiency, not
                        # linear: nameplate at design, cheaper mid-load, penalised at
                        # very low PLR (fixed losses dominate). COP is the inverse —
                        # peaks mid-load, droops with a hot condenser.
                        tgt_w = chiller_electrical_w(w, plr, _city)
                        plant_cop[_n] = chiller_cop(plr, _city)
                    else:
                        # Valve / other: negligible actuator draw — nameplate share.
                        base = total_w * (w / np_sum)
                        tgt_w = min(base, w if w > 0 else base)
                    plant_power[_n] = tgt_w / 1000.0   # kW
            self._plant_power_by_name = plant_power
            self._plant_cop_by_name = plant_cop
        except Exception:
            log.exception("[StateStore] power flow error")
        self._through_live = through
        self._it_w = it_w
        self._facility_w = it_w + cool_w
        # Live downstream kW per EV2 meter IP, for the BACnet telemetry engines.
        self._ev2_live_kw = {ip: through.get(panel, 0.0) / 1000.0
                             for ip, panel in ctx.get("ev2_ip_panel", {}).items()}
        # Live kW per branch circuit (ordered), so each EV2 circuit meters the real
        # load of the PDU it clamps instead of a synthetic per-circuit random walk.
        self._ev2_circuit_kw = {
            ip: [through.get(pid, 0.0) / 1000.0 for pid in pids]
            for ip, pids in ctx.get("ev2_circuit_pdus", {}).items()}

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
        self._compute_power_flow()        # live watts up the power graph (server→PDU→UPS→EV2)
        for device in devices:
            self._step_device(device)
            self._step_ext_state(device)
            self._force_states_nominal(device)      # state-change faults are manual-only
            if not self.autonomous_faults:
                self._scrub_numeric_faults(device)  # quiet baseline — no threshold traps
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
                                       self.plant_alarm_overrides, live_kw_by_ip=self._ev2_live_kw,
                                       circuit_kw_by_ip=self._ev2_circuit_kw,
                                       plant_power_by_name=self._plant_power_by_name,
                                       plant_cop_by_name=self._plant_cop_by_name)
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
            "ups_runtime_min": 8.0,
            "gen_fuel_pct": random.uniform(75.0, 95.0),
            "gen_run_hours": 0.0,
            "gen_status": "standby",
            "gen_load_pct": 0.0,
            "gen_kw": 0.0,
            "gen_runtime_min": 0.0,
            "gen_start_attempts": 0,
            "gen_was_running": False,
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

        # Injected/overridden CPU pin: resolve it BEFORE the walk so cpu_temp,
        # fan, power and exhaust all derive from the pinned value this tick. The
        # walk would otherwise reset a >90 pin back to ~50 before those are
        # computed, leaving SNMP cpu% high but the thermal chain at idle. The
        # ramp/override engines re-apply at end-of-tick for the published value;
        # here it just feeds the derivations below.
        _cpu_pin     = self._pin_value(device, "cpu_usage")
        _cputemp_pin = self._pin_value(device, "cpu_temp")
        if _cpu_pin is not None:
            device.cpu_usage = int(max(0.0, min(100.0, _cpu_pin)))

        # CPU — brief vs sustained spike recovery (skipped while CPU is pinned)
        if mf["cpu_usage"] and _cpu_pin is None:
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

        # Disk: hover around a per-device baseline with small jitter. A growth-
        # biased walk would march every server to the 90 % cap and pin there; real
        # disks change slowly and sit at varied levels, so mean-revert to the
        # device's own anchor (its initial fill) instead.
        if mf["disk_used"]:
            _tot = max(1, device.disk_total)
            _anchor = self._disk_anchor.get(device.name)
            if _anchor is None:
                _anchor = min(0.85, max(0.08, device.disk_used / _tot))
                self._disk_anchor[device.name] = _anchor
            _frac = device.disk_used / _tot
            _frac += (_anchor - _frac) * 0.08 + random.uniform(-0.0035, 0.0035)
            _frac = max(0.05, min(0.90, _frac))
            device.disk_used = int(_tot * _frac)
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
            # Realistic CPU die temps: air-cooled idles ~40 °C and reaches ~83 °C
            # at 100 % load (Tjmax ~95–100 °C; Warning 85 / Critical 90). The load
            # slope is tuned so a fully-loaded server peaks below 90 — so it never
            # false-alarms — and only a cooling fault (warm intake / CDU leak) or an
            # injected fault pushes it past Critical. Direct-to-chip liquid holds the
            # die far cooler: a cold plate fed ~25 °C water keeps a loaded CPU ~65 °C.
            _liquid = (device.device_type == DeviceType.SERVER
                       and device.name in self._liquid_cooled_servers())
            if _liquid:
                _cpu_t = 35.0 + device.cpu_usage * 0.30 + random.uniform(-1.0, 1.0)
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
                _cpu_t = 38.0 + device.cpu_usage * 0.45 + random.uniform(-1.0, 1.0)
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
            # A pinned (injected/overridden) cpu_temp wins, so the fan below ramps
            # to cool the hot die — the realistic response to a thermal fault.
            if _cputemp_pin is not None:
                device.cpu_temp = round(max(20.0, min(95.0, _cputemp_pin)), 1)

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
            # Live draw tracks CPU load — idle ~55 % of the configured nominal,
            # 100 % at full load — the same curve Redfish _live_watts reports. So
            # higher load raises power, which raises exhaust ΔT and airflow, while
            # cpu_usage also drives cpu_temp and fan_rpm: load lifts all of them
            # together (power_draw_w is the static full-load nameplate).
            _pw = float(device.power_draw_w or 0.0) * (0.55 + 0.45 * load)
            # fan controllers track the HEAT dumped, i.e. live power, so flow
            # scales with it. pf normalises a typical ~450–760 W server band to 0..1.
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

        # Interface flapping is a LinkDown/LinkFlap state-change event → MANUAL
        # only (Send Trap / link break). No autonomous flapping in either mode.

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
            "baseline": None, "current": None, "clearing": False,
        }
        return True

    def clear_fault(self, device_id: str, metric: str) -> bool:
        """Reverse a ramp toward its captured baseline. The record is removed
        once the baseline is reached (after the recovery threshold is crossed)."""
        dmap = self._fault_ramps.get(device_id, {})
        r = dmap.get(metric)
        if not r:
            return False
        if r["current"] is None:
            # Never advanced a tick — nothing to ramp down; just drop it.
            dmap.pop(metric, None)
            if not dmap:
                self._fault_ramps.pop(device_id, None)
            return True
        r["target"] = r["baseline"]
        r["clearing"] = True
        return True

    def get_faults(self, device_id: str) -> dict:
        """Active ramps for a device, keyed by metric (for the UI ACTIVE state)."""
        return {m: {"target": r["target"], "clearing": r["clearing"]}
                for m, r in self._fault_ramps.get(device_id, {}).items()}

    def _pin_value(self, device: "Device", metric: str) -> "Optional[float]":
        """Current pinned value of a device-field metric (Metric-Tick override or
        active Inject-Fault ramp), or None if not pinned. Override wins. Used to
        feed the pinned cpu_usage/cpu_temp into the thermal/power/fan/exhaust
        derivations BEFORE the walk runs, so they reflect the injected value."""
        ov = self.device_overrides.get(device.id)
        if ov and metric in ov:
            try:
                return float(ov[metric])
            except (TypeError, ValueError):
                return None
        r = self._fault_ramps.get(device.id, {}).get(metric)
        if r and r.get("current") is not None:
            return float(r["current"])
        return None

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
            # The ramp owns its trajectory in r["current"] rather than re-reading
            # the device value each tick — derived metrics like cpu_temp are
            # recomputed by the walk every tick, which would otherwise reset the
            # ramp's progress and it could never reach the target.
            if r["current"] is None:                  # capture baseline on first tick
                r["current"] = self._ramp_read(device, kind, field)
                if r["baseline"] is None:
                    r["baseline"] = r["current"]
            cur = r["current"]
            target, rate = r["target"], r["rate"]
            if cur < target:
                cur = min(target, cur + rate)
            elif cur > target:
                cur = max(target, cur - rate)
            cur = max(lo, min(hi, cur))
            r["current"] = cur
            self._ramp_write(device, kind, field, cur)
            # Clearing ramp that has reached baseline → fault fully resolved.
            if r["clearing"] and abs(cur - target) < max(0.5, rate):
                done.append(metric)
        for m in done:
            ramps.pop(m, None)
        if not ramps:
            self._fault_ramps.pop(device.id, None)

    def _force_states_nominal(self, device: "Device") -> None:
        """State-change faults (UPS/PDU hardware states, smoke, breaker, ground
        fault, water, BGP session) are MANUAL only — fired via Send Trap. The
        walk's random state flips are scrubbed back to nominal every tick, in BOTH
        modes, so they never raise an autonomous trap. Runs before user Metric-Tick
        state overrides, which therefore still win. Numeric threshold metrics are
        handled separately (_scrub_numeric_faults) and DO fire autonomously when
        autonomous_faults is on. Interface flap is suppressed at its source."""
        st = self._ext_states.get(device.name)
        if st is None:
            return
        dt = device.device_type
        changed = False

        def setv(k: str, v) -> None:
            nonlocal changed
            if st.get(k) != v:
                st[k] = v
                changed = True

        if dt == DeviceType.UPS:
            setv("ups_status", "normal")
            setv("ups_battery_status", "normal")
            for c in ("ups_fan_status", "ups_charger_status",
                      "ups_rectifier_status", "ups_phase_status"):
                setv(c, "ok")
            setv("ups_bypass_status", "off")
        if dt in (DeviceType.PDU, DeviceType.FLOOR_PDU):
            setv("pdu_outlet_status", "on")
            setv("pdu_breaker_status", "ok")
            setv("pdu_outlet_failure", "ok")
            setv("pdu_smoke", "no")
            setv("pdu_ground_fault", "no")
        if "water_detection" in st:
            setv("water_detection", "dry")
        for sess in st.get("bgp_sessions", []):
            if sess.get("state") != "established":
                sess["state"] = "established"
                changed = True

        if changed:
            _ext_state_cache[device.name] = dict(st)

    def _scrub_numeric_faults(self, device: "Device") -> None:
        """Quiet baseline for THRESHOLD metrics (autonomous_faults OFF): clamp any
        numeric metric the walk pushed past its alert threshold back just inside
        the safe band, so no threshold trap fires. Skipped when autonomous_faults
        is ON, so the walk's spikes breach thresholds and fire traps organically.
        Ceilings sit just inside the thresholds in core/trap_rules.py, preserving
        normal sub-threshold variation. Runs before user overrides / ramps (which
        therefore still win)."""
        dt = device.device_type

        # An explicit, enabled Metric-Tick limit is deliberate operator intent and
        # must win over the quiet-baseline scrub (same as overrides/ramps). Only skip
        # the scrub cap when the user's floor sits above it — otherwise the organic
        # walk is still tamed as before.
        _cl = self.metric_limits.get("cpu_usage")
        _cpu_forced = bool(_cl and _cl["enabled"] and _cl["min"] > 89)
        if getattr(device, "cpu_usage", 0) > 89 and not _cpu_forced:   # HighCPU > 90
            device.cpu_usage = 89
        mtot = getattr(device, "memory_total", 0)
        _ml = self.metric_limits.get("memory_pct")
        _mem_forced = bool(_ml and _ml["enabled"] and _ml["min"] > 84.9)
        if mtot and device.memory_used > int(mtot * 0.849) and not _mem_forced:  # HighMemory > 85 %
            device.memory_used = int(mtot * 0.849)
        # cpu_temp is NOT clamped: even at 100 % load the walk peaks ~84 °C (plus a
        # few °C of intake coupling), staying below the 90 °C HighTemperature
        # threshold, so it never auto-alarms. It only crosses 90 via a user-injected
        # CDU leak, a CRAH/cooling fault, or Inject Fault — all of which SHOULD fire
        # the trap, so scrubbing it here would wrongly suppress them.

        if dt == DeviceType.SENSOR:
            device.inlet_temp  = min(device.inlet_temp, 31.9)        # ambient > 32
            device.humidity    = min(max(device.humidity, 30.1), 69.9)  # <30 / >70
            device.dewpoint    = min(device.dewpoint, 20.9)          # > 21
            device.airflow     = min(max(device.airflow, 0.31), 3.49)   # <0.3 / >3.5
            device.mid_temp    = min(device.mid_temp, 37.9)          # > 38
            device.outlet_temp = min(device.outlet_temp, 44.9)       # > 45

        st = self._ext_states.get(device.name)
        if st is None:
            return
        changed = False

        def clamp(k: str, lo: float, hi: float, default: float) -> None:
            nonlocal changed
            v = st.get(k, default)
            nv = min(max(v, lo), hi)
            if nv != v:
                st[k] = nv
                changed = True

        if dt == DeviceType.UPS:
            if st.get("ups_output_load", 0.0) > 89.9:             # overload > 90
                st["ups_output_load"] = 89.9; changed = True
            clamp("ups_input_voltage", 190.1, 249.9, 220.0)        # >250 / <190
            clamp("ups_input_frequency", 49.1, 50.9, 50.0)         # OOR <49 / >51
            if st.get("ups_battery_health", 100.0) < 50.1:         # low health < 50
                st["ups_battery_health"] = 50.1; changed = True
        if dt in (DeviceType.PDU, DeviceType.FLOOR_PDU):
            if st.get("pdu_load", 0.0) > 79.9:                     # load high > 80
                st["pdu_load"] = 79.9; changed = True
            clamp("pdu_voltage", 200.1, 239.9, 220.0)              # >240 / <200
            if st.get("pdu_power_factor", 1.0) < 0.701:            # PF low < 0.70
                st["pdu_power_factor"] = 0.701; changed = True
            if st.get("pdu_phase_imbalance", 0.0) > 19.9:          # imbalance > 20
                st["pdu_phase_imbalance"] = 19.9; changed = True
            if st.get("pdu_outlet_current", 0.0) > 19.9:           # current > 20
                st["pdu_outlet_current"] = 19.9; changed = True
            clamp("pdu_frequency", 49.6, 50.9, 50.0)               # fault < 49.5
            if st.get("pdu_temperature", 0.0) > 34.9:              # temp > 35
                st["pdu_temperature"] = 34.9; changed = True
            if st.get("pdu_humidity", 0.0) > 69.9:                 # humidity > 70
                st["pdu_humidity"] = 69.9; changed = True

        if changed:
            _ext_state_cache[device.name] = dict(st)

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
            "ups_runtime_min": 8.0,
            "gen_fuel_pct": random.uniform(75.0, 95.0),
            "gen_run_hours": 0.0,
            "gen_status": "standby",
            "gen_load_pct": 0.0,
            "gen_kw": 0.0,
            "gen_runtime_min": 0.0,
            "gen_start_attempts": 0,
            "gen_was_running": False,
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
                _rated = self._power_context()["rated_w"].get(device.id, 0.0)
                _thr = self._through_live.get(device.id, 0.0)
                if _rated > 0:
                    # Live: output load = watts flowing through this UPS / its rating.
                    load = max(0.0, min(100.0, _thr / _rated * 100.0 + random.uniform(-0.5, 0.5)))
                else:
                    # Not wired into the power graph — legacy self-contained walk.
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

            # Battery runtime (minutes remaining) ∝ 1/load — a heavier load drains
            # the battery faster. Anchored at ~8 min autonomy at full load (typical
            # double-conversion DC UPS), scaled by state-of-health and charge level
            # (on battery the charge falls, shortening runtime further).
            load_now = max(5.0, st.get("ups_output_load", 40.0))
            health   = max(10.0, st.get("ups_battery_health", 100.0)) / 100.0
            charge   = 0.4 if st.get("ups_status") == "low_battery" else (
                       0.7 if st.get("ups_status") == "on_battery" else 1.0)
            runtime  = self._UPS_DESIGN_MIN * (100.0 / load_now) * health * charge
            st["ups_runtime_min"] = round(min(120.0, runtime), 1)

        # ── Generator ─────────────────────────────────────────────────────
        if device.device_type == DeviceType.GENERATOR:
            self._step_generator(device, st)

        # ── PDU / Floor PDU ───────────────────────────────────────────────
        if is_pdu:
            _pdu_rated = self._power_context()["rated_w"].get(device.id, 0.0)
            _pdu_thr = self._through_live.get(device.id, 0.0)
            if mf["pdu_load"]:
                if _pdu_rated > 0:
                    # Live: load % = watts drawn by downstream gear / breaker rating.
                    ld = max(0.0, min(100.0, _pdu_thr / _pdu_rated * 100.0
                                      + random.uniform(-0.5, 0.5)))
                else:
                    # Not wired into the power graph — legacy self-contained walk.
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
                if _pdu_rated > 0:
                    # Live: phase current I = P / (V·√3·PF) for a 3-phase PDU.
                    _v = st.get("pdu_voltage", 220.0)
                    _pf = st.get("pdu_power_factor", 0.95)
                    oc = max(0.0, _pdu_thr / max(1.0, _v * 1.732 * _pf)
                             + random.uniform(-0.2, 0.2))
                else:
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

            # Energy accumulator: integrate real kW × tick interval.
            if mf["pdu_energy_kwh"]:
                if _pdu_rated > 0:
                    real_kw = _pdu_thr / 1000.0          # live draw through the PDU
                else:
                    volt_now = st.get("pdu_voltage", 220.0)
                    cur_now  = st.get("pdu_outlet_current", 10.0)
                    pf_now   = st.get("pdu_power_factor", 0.95)
                    real_kw  = (volt_now * cur_now * pf_now) / 1000.0
                st["pdu_energy_kwh"] = round(st.get("pdu_energy_kwh", 0.0)
                                             + real_kw * self._tick_interval / 3600.0, 3)

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