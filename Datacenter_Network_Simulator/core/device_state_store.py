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
import math
import random
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, TYPE_CHECKING

from core.device_manager import DeviceType, cooling_capacity_w, fan_rpm_range

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

# Airflow a CRAH loses to a clogged filter, as a fraction of its design flow.
# Must match the Filter_Dirty derate applied in core/bacnet_plant_generator.py:
# that side moves the published Airflow point, this side turns the same derate
# into lost room cooling. A filthy filter is a PARTIAL capacity loss — the unit
# still delivers cold air, just less of it — which is why it cannot be handled
# by the binary off/no-airflow path.
_CRAH_FILTER_DERATE = 0.20

# Fraction of a direct-to-chip (CDU cold-plate) server's heat that still leaves
# via AIR. Cold plates capture ~70 % of the load (CPU/GPU) into the liquid loop;
# the residual (VRMs, DIMMs, drives, PSUs) is air-cooled, so the air-side exhaust
# ΔT — and thus outlet_temp — is much lower than an all-air server's.
#
# Imported, not redefined: core.rack_capacity uses the same number to size a rack's
# AIR BUDGET. If the thermal model and the capacity model disagreed about how much
# heat a liquid server puts in the room, a rack could be filled to a limit its own
# exhaust math contradicts.
from core.rack_capacity import DTC_AIR_FRACTION as _DTC_AIR_FRACTION

# A direct-to-chip server's MINIMUM fan duty, as a fraction of the same chassis's
# air-cooled minimum (core.device_manager.fan_rpm_range). Fans never stop on a live
# server — the DIMMs, VRs, NICs and drives have no liquid path — but with the CPU
# heat in the coolant loop there is much less for them to hold at idle.
_DTC_IDLE_FACTOR = 0.60

# Plant running-status points: a value of 0 means the unit is stopped, which for
# cooling gear is as much a loss of cooling as an alarm — see _is_faulted().
#
# One entry per device class in core.bacnet_plant_generator, whose "on" set marks
# the binary that reads 1 on a healthy unit. Status_Modulating is the valve's,
# and it was missing here, which made a SHUT header valve a complete no-op — the
# one genuine single point of failure in a hydronic plant registering as nothing
# at all. If a device class gains a running binary there, it belongs here too.
_RUNNING_POINTS = frozenset({"Chiller_Running", "Run_Status", "Fan_Status",
                             "Unit_Running", "Status_Modulating"})

# CAPACITY alarms — the unit is healthy, it is simply outmatched by the load.
# They must NOT be read as lost cooling capacity, for two reasons:
#
#   • They are the ANNOUNCEMENT of a shortfall the thermal model has already
#     booked (_compute_chw_penalty). Counting them again would double-charge it.
#   • Worse, it would close a positive feedback loop: shortfall → alarm → the
#     alarm is scored as lost capacity → bigger shortfall → louder alarm, with
#     nothing in the physics driving it. A plant at 100 % load would run itself
#     to the thermal ceiling on its own annunciation.
#
# Nor should the BMS demote a train for one: a chiller at full compressor is the
# machine you most want running. Health alarms (the high-pressure CUTOUT, flow loss,
# actuator fault, leak) keep their existing meaning.
#
# Alarm_CondPressLimit is the third member, and it is here for a reason measured on
# the live sim: it is a property of the SHARED CONDENSER LOOP, not of one machine.
# After a genset restore the towers have been dark, condenser water is ~38 °C, and
# whichever chiller is lead unloads and raises it. Read as a health fault, that
# demotes the lead and promotes a standby — which inherits the same hot loop and
# raises the same limit one tick later, while the demoted machine goes quiet and
# looks healthy again. Measured: the lead ping-ponged CHL1↔CHL2 every 2–3 s for the
# whole restore, each swap a real compressor start from 0 % load, until the tower
# caught up and the condition cleared on its own. The cutout (Alarm_HighPressure,
# latched, machine off) is NOT exempt and still demotes — that one really is the
# machine.
#
# Kept deliberately small. Alarm_LowFlow is NOT here: on a pump that point means a
# blocked strainer, a shut valve or a failed impeller, which genuinely does cost
# cooling. A capacity alarm has to be a point that can ONLY mean "healthy but
# outmatched" — otherwise exempting it would blind the store to a real fault.
_CAPACITY_ALARMS = frozenset({"Alarm_HighCHWSupply", "Alarm_HighReturnAir",
                              "Alarm_CondPressLimit"})

# Which of them _compute_chw_loop OWNS — the evaporator-side annunciations it
# re-derives every pass, and therefore clears first so a cleared alarm cannot latch.
# Alarm_CondPressLimit is deliberately NOT here: it belongs to the CONDENSER pass,
# which runs earlier in the same tick. Clearing it here deleted the point one step
# after it was published, so the limit never reached BACnet at all — the alarm
# existed in the model and was invisible on the wire. Membership of _CAPACITY_ALARMS
# answers "is this a health fault?"; membership here answers "whose key is it?".
_CHW_OWNED_ALARMS = frozenset({"Alarm_HighCHWSupply", "Alarm_HighReturnAir"})

# ── Plant header probes ───────────────────────────────────────────────────────
# The chiller plant's headers carry instruments, not rack environmental probes:
# supply/return thermowells on both water loops, a basin thermistor in the tower
# sump, and a magnetic flow meter on the chilled-water main. In the topology they
# are DeviceType.SENSOR devices whose name LEADS with the point's role code (the
# unified naming scheme — see project docs), so the role is read off the name.
#
# In a real plant these are hardwired 4–20 mA / RTD inputs to the BMS controller,
# not networked devices of their own; the simulator models them as devices so each
# point is individually visible and alarmable. What matters is that they report the
# LIVE loop (see _compute_chw_loop / _compute_cond_loop) rather than the cold-aisle
# air a rack probe reads — a CHW supply thermowell has no opinion about room air.
_PROBE_ROLES = {
    "CHWS": "chw_supply",     # chilled-water supply header  (°C)
    "CHWR": "chw_return",     # chilled-water return header  (°C)
    "CWS":  "cw_supply",      # condenser-water supply       (°C)
    "CWR":  "cw_return",      # condenser-water return       (°C)
    "CTB":  "ct_basin",       # cooling-tower basin/sump     (°C)
    "FLOW": "chw_flow",       # chilled-water main flow      (l/s)
}
# Model-name prefix every plant header probe carries. Used to keep the cold-aisle
# air rules (ambient temp / humidity / dewpoint / airflow) off them: 35 °C condenser
# return water is a perfectly healthy reading and must not raise a room-temperature
# alarm, and a thermowell has no humidity to be "low".
_PROBE_MODEL_PREFIX = "Plant "


def _probe_role(device) -> "str | None":
    """The plant-header point a SENSOR device represents, or None if it is an
    ordinary rack environmental probe."""
    if not str(getattr(device, "model_name", "")).startswith(_PROBE_MODEL_PREFIX):
        return None
    return _probe_role_by_name(getattr(device, "name", ""))


def _probe_role_by_name(name: str) -> "str | None":
    """Role from the name prefix alone, with no Device object.

    A Modbus gateway republishes its trunk by NAME (device.modbus_children), and
    the SNMP generator resolves those names against _ext_state_cache — which is
    also name-keyed. Neither has a Device to hand, so the role lookup must work
    off the identifier the same way _probe_role does.
    """
    return _PROBE_ROLES.get(str(name).split("-")[0].upper())


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


# Module-level mirror of the CDU cold-plate loop membership, republished each tick.
# The dataset generators need it to know a server's fan floor (a direct-to-chip
# chassis idles below an air-cooled one) but hold no reference to the store, so they
# read it here — the same way they read _ext_state_cache.
_liquid_server_cache: set = set()


def _get_ext_state(device_name: str) -> dict:
    return _ext_state_cache.get(device_name, {})


def _is_liquid_server(device_name: str) -> bool:
    return device_name in _liquid_server_cache


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
        self._tick_interval   = tick_interval    # configured CADENCE (the sleep)
        self._last_tick_t     = None             # monotonic stamp of the previous tick
        self._dt              = tick_interval    # MEASURED elapsed seconds this tick
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
        # Device count the cached context was built from; a change means the
        # topology moved under us and the maps must be rebuilt.
        self._cool_ctx_sig: int = -1
        self._chw_pen: Dict[str, float] = {}

        # ── Condenser-water loop + chiller head-pressure protection ──────────
        # The towers reject the chillers' condenser heat. Lose tower capacity and
        # condenser water temperature climbs, which raises refrigerant condensing
        # pressure, and every centrifugal machine protects itself against that:
        # first by UNLOADING (capacity limit), then by tripping its high-pressure
        # safety. Without this the plant modelled a physically impossible state —
        # chillers happily making chilled water with nowhere to reject the heat.
        self._cond_water_c: Dict[str, float] = {}     # dc → condenser supply °C
        self._cond_base_c: Dict[str, float] = {}      # dc → CW the healthy bank can hold (wb+approach)
        self._chiller_derate: Dict[str, float] = {}   # chiller name → 0..1 capacity lost
        self._chiller_hp_lockout: set = set()         # chiller names latched out
        self._train_run_hours: Dict[str, float] = {}  # chiller name → accrued lead run-hours
        # Anti-recycle timers for lead selection (see _LEAD_MIN_RUN_S). Seconds the
        # machine has held lead without a break, and seconds since it lost it. A
        # chiller that has never led is absent from both, which reads as "idle long
        # enough" — a cold start must not have to wait out an anti-recycle timer.
        self._train_lead_s: Dict[str, float] = {}
        self._train_idle_s: Dict[str, float] = {}
        self._plant_auto_points: Dict[str, dict] = {} # device NAME → {point: value}
        self._cond_trip_s: Dict[str, float] = {}      # chiller name → s above trip temp

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
        self._modbus_ctrl = None                 # ModbusController, optional

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
        self.plant_alarm_overrides: Dict[str, Dict[str, float]] = {}   # device NAME -> {point: value}

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
        # One frequency per grid region (keyed by city). A power grid has a single
        # frequency across its whole interconnect, so every utility meter on the
        # same grid reads an identical value each tick; separate grids drift apart.
        # Stepped once per tick by the power-flow pass, read by the utility meters.
        self._grid_freq: Dict[str, float] = {}
        self._ev2_live_kw: Dict[str, float] = {}   # {ev2_ip: live downstream kW}
        self._ev2_circuit_kw: Dict[str, list] = {} # {ev2_ip: [per-circuit live kW]}
        # Persistent circuit→branch slot order per EV2 IP. Real EV2 CT channels are
        # physical: a branch keeps its slot for the meter's life and a new PDU takes
        # the next free channel — existing branches never reshuffle (that would
        # silently reassign per-slot kWh accumulators to other devices). Survives
        # power-context rebuilds so fleet churn appends rather than re-sorts.
        self._ev2_circuit_order: Dict[str, list] = {}  # {ev2_ip: [branch device_id]}
        self._plant_power_by_name: Dict[str, float] = {}  # {plant_name: live cooling kW}
        self._plant_cop_by_name: Dict[str, float] = {}    # {chiller_name: live COP}
        self._plant_loadfrac_by_name: Dict[str, float] = {}  # {plant_name: DC duty frac}
        self._plant_speed_by_name: Dict[str, float] = {}  # {plant_name: VFD speed frac}
        self._cdu_loop_heat_kw: Dict[str, float] = {}     # {cdu_name: live loop heat kW}
        # Frozen per-DC design cooling nameplate (first-seen), so IT_design stays a
        # fixed capacity ceiling even when the fleet adds CRAHs to new halls — those
        # are air distribution, not extra chiller/plant capacity.
        self._plant_np0_by_dc: Dict[str, float] = {}
        # ── Chiller-plant STAGING state ───────────────────────────────
        # The plant is installed for the fleet's ultimate capacity but SEQUENCES
        # modules on as live IT load climbs (BMS staging). it_design tracks the
        # ENABLED (staged) capacity, so part-load overhead — and PUE — follows the
        # running set instead of collapsing (fleet overload → fake-low PUE) or
        # spiking (full plant floor at low load). See core/cooling_model.stage_modules.
        self._plant_stage_on: Dict[str, int] = {}     # DC → modules currently running
        self._plant_trains_run: Dict[str, list] = {}  # DC → cooling trains the BMS has ON
        self._plant_standby_names: set = set()        # chiller names staged OFF (not faulted)
        self._plant_overload_kw: Dict[str, float] = {} # DC → IT beyond full installed plant
        # Anti-short-cycle timers: seconds since this DC's last stage UP / DOWN. Seeded
        # large so the first stage change of a run is free; a compressor's minimum-off
        # then gates every restart after it (core/cooling_model.stage_modules).
        self._plant_stage_since: Dict[str, tuple] = {}   # DC → (since_up_s, since_down_s)
        # RUN-STATUS PROOF. Seconds a unit has been COMMANDED ON while its running
        # binary still reads 0. A machine that dies quietly raises no alarm, so the
        # lead/lag ranking — which judges health by alarms — would leave it as lead
        # forever while the cooling-loss model counted its capacity as gone. Every
        # real chiller sequence (Trane Tracer, JCI Metasys, ASHRAE Guideline 36)
        # closes that gap with a failure-to-start timer, and this is it. Keyed by
        # device name; only units in a train the BMS has staged ON accumulate, so a
        # standby stopped on purpose never times out.
        self._run_proof_s: Dict[str, float] = {}
        # Evaporator flow: surviving chilled-water pumping as a fraction of what the
        # running trains require (0..1), the dwell timer behind the flow switch, and
        # the chillers it has shed. See _FLOW_TRIP_FRAC.
        self._chw_pump_frac: Dict[str, float] = {}
        self._cw_pump_frac: Dict[str, float] = {}   # same, condenser side
        # DC → MEASURED condenser range (return − supply, K). Derived from the flow
        # the CW pumps actually deliver, not pinned at design, so a stalled
        # condenser loop stops advertising a design range it cannot carry.
        self._cond_range_c: Dict[str, float] = {}
        self._chw_flow_lost_s: Dict[str, float] = {}
        self._chw_flow_interlock: set = set()
        # DC → CRAH discharge air (setpoint + CHW penalty). Published on the units
        # and read back by _room_supply_temp, so the penalty is counted once.
        self._supply_air_c: Dict[str, float] = {}
        # IT thermal protection: servers currently throttled (name → load factor)
        # and those the platform has tripped off. See _apply_thermal_protection.
        self._throttled: Dict[str, float] = {}
        self._thermal_shutdown: set = set()
        # Wall-clock instant each plant unit first read stopped while commanded
        # on. Debounces the PlantUnitStopped trap across staging transitions —
        # see _plant_status / _RUN_ALARM_S.
        self._stopped_since: Dict[str, float] = {}
        # Tower bank (NOT staged with the trains — every healthy cell runs slow):
        # DC → (cells the load needs, cells actually turning).
        self._tower_cells: Dict[str, tuple] = {}
        self._tower_reject: Dict[str, float] = {}        # DC → tower rejection capability 0..1
        self._tower_run_hours: Dict[str, float] = {}     # cell name → accrued run hours (rotation)
        # Cell name → seconds it has been COMMANDED to run while reporting its fan
        # stopped. The bank is unstaged, so a cycled-off cell is stopped on purpose
        # and stays available; this separates that from a cell that has genuinely
        # failed silently, which alarms alone cannot see.
        self._tower_silent_s: Dict[str, float] = {}
        self._tower_running_now: Dict[str, set] = {}     # DC → cells turning last tick (least-switching)
        # Per-DC installed module count is sized to the fleet server cap so the plant
        # never truly runs out until the cap; recomputed lazily from the live cap.
        self._plant_installed_mods: Dict[str, int] = {}
        self._cool_model_w: float = 0.0  # staged-model cooling electrical (all DCs), for PUE
        # Same figure kept per DC, because the delivered-capacity scaling that makes
        # it honest during a failure is per DC (_cool_loss_frac). See
        # get_power_summary.
        self._cool_model_w_by_dc: Dict[str, float] = {}
        self._facility_w: float = 0.0   # whole-DC draw (IT + cooling) for PUE
        self._it_w: float = 0.0         # IT-only draw for PUE denominator

        # ── Chilled-water (evaporator) loop, per DC ───────────────────────────
        # The condenser side has its own state above (_cond_water_c). This is the
        # side the room feels: what temperature water the plant is actually making,
        # how much of it is moving, and how wide the loop ΔT has opened. All three
        # are load-driven (see _compute_chw_loop / core.cooling_model), so they move
        # when the fleet grows instead of walking on a clock.
        self._chw_supply_c: Dict[str, float] = {}   # DC → CHW supply temp (°C)
        self._chw_return_c: Dict[str, float] = {}   # DC → CHW return temp (°C)
        self._chw_flow_lps: Dict[str, float] = {}   # DC → total loop flow (l/s)
        self._chw_dt_c: Dict[str, float] = {}       # DC → measured loop ΔT (K)
        self._it_live_by_dc: Dict[str, float] = {}  # DC → live IT heat (W)
        self._plant_duty: Dict[str, float] = {}     # DC → running-plant duty fraction
        self._cool_loss_frac: Dict[str, float] = {} # DC → cooling-loss fraction 0..1
        self._room_inlet_c: Dict[tuple, float] = {} # (dc, room) → mean server inlet (°C)
        self._room_outlet_c: Dict[tuple, float] = {} # (dc, room) → mean server exhaust (°C)
        # Seconds each chiller has held CHW supply off setpoint (alarm dwell), so a
        # momentary excursion during a stage change does not annunciate.
        self._chw_high_s: Dict[str, float] = {}
        self._chw_high_alarm: set = set()   # chillers currently annunciating it
        # Live reading for each plant header probe (CHWS/CHWR/CWS/CWR/CTB/FLOW),
        # keyed by device name → (role, value). Published by _compute_chw_loop and
        # consumed by _step_device so the probes report the loop instead of air.
        self._probe_reading: Dict[str, tuple] = {}

        # ── Utility → generator transfer ──────────────────────────────────────
        # One sequencer per DC, driven from the utility feed's health. Everything
        # downstream (genset start, ATS position, which mechanical load blocks are
        # energized, whether each UPS sees a qualified source) falls out of it.
        from core.power_transfer import TransferController
        self._transfer = TransferController()
        self._utility_failed: Dict[str, bool] = {}    # DC → injected utility outage
        self._ats_failed: Set[str] = set()            # ATS device ids failed by injection
        self._ats_not_in_auto: Set[str] = set()       # ATS ids in manual (annunciation only)
        self._ups_forced_battery: Dict[str, str] = {}  # UPS id → "on"|"low": injected on-battery
        self._gen_failed: Set[str] = set()             # generator ids that will NOT start (injected)
        self._gen_conditions: Dict[str, set] = {}      # generator id → active alarm conditions
        self._swgr_conditions: Dict[str, set] = {}     # switchgear id → breaker-trip / bus-fault
        # PDU id → latching conditions. Only breaker_trip is here: it is the one PDU
        # fault with a POWER consequence rather than an annunciation, so it has to be
        # state the energization walk can read. Everything else a PDU raises (outlet
        # failure, ground fault, smoke, threshold alarms) is a pinned metric handled
        # through the ordinary override path.
        self._pdu_conditions: Dict[str, set] = {}
        self._ups_input_live: Dict[str, bool] = {}     # UPS id → rectifier input path live (graph)
        self._broken_power: Set[frozenset] = set()     # power feeders currently open (this tick)
        # Plant units whose MCC is currently de-energized. Distinct from
        # _plant_standby_names: a staged-OFF unit is a healthy BMS decision and does
        # NOT count as lost cooling, whereas an unpowered unit genuinely is not
        # rejecting heat, so it must show up in the cooling-loss penalty.
        self._plant_unpowered_names: set = set()
        # IT loads whose every feed sits on a switched-off outlet — see
        # _compute_unpowered_loads(). Same shape and purpose as the plant set above.
        self._load_unpowered_names: set = set()
        # {frozenset((load_id, pdu_id))} for cords whose outlet relay is open. Same
        # shape as _broken_power because it has the same consequence: the cord
        # carries nothing, so the parent must drop out of the load's active set and
        # the SURVIVING side inherits the whole draw.
        self._dead_cord_pairs: set = set()
        # Seconds each DC's mechanical plant has been (even partly) de-energized.
        # Drives the chilled-water ride-through — see _CHW_RIDE_S.
        self._mech_dead_s: Dict[str, float] = {}
        # node id → is it delivering power downstream this tick (see _compute_energized)
        self._energized: Dict[str, bool] = {}

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
            "ups_input_voltage":   {"enabled": False, "min": 360.0, "max": 440.0},
            "ups_input_frequency": {"enabled": False, "min": 49.5,  "max": 50.5},
            "ups_battery_health":  {"enabled": False, "min": 0.0,   "max": 100.0},
            "pdu_load":            {"enabled": False, "min": 0.0,   "max": 100.0},
            "pdu_voltage":         {"enabled": False, "min": 205.0, "max": 235.0},
            "pdu_outlet_current":  {"enabled": False, "min": 0.0,   "max": 40.0},
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

        # Autonomous ATS event traps. cb(ats_device, event_kind) — the store stays
        # decoupled from core.trap_definitions; the wiring maps the kind to a
        # TrapType. Unset on the desktop path, so it is a no-op there.
        self._transfer_trap_cb: Optional[Callable[["Device", str], None]] = None
        # Cold-start trap on power-loss recovery. cb(device) — fired when a load that
        # went dark from total power loss gets a live feed back and reboots.
        self._coldstart_cb: Optional[Callable[["Device"], None]] = None

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

    def set_coldstart_callback(self, cb: Callable[["Device"], None]):
        """cb(device) — fired when a load that had gone dark from total power loss
        gets a live feed back and cold-boots (the recovery side of a blackout)."""
        self._coldstart_cb = cb

    def set_transfer_trap_callback(self, cb: Callable[["Device", str], None]):
        """cb(ats_device, event_kind) — fired autonomously as a transfer switch's
        source/position changes, so the utility-outage → genset → retransfer
        sequence lights up SNMP the way a real ATS does. event_kind is one of:
        "source_lost", "engine_start", "transfer_emergency", "transfer_normal"."""
        self._transfer_trap_cb = cb

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

    def enable_modbus(self, ctrl):
        """Register the Modbus controller — its tick() runs every tick cycle.

        Unlike BACnet this holds no telemetry of its own: the Modbus slaves
        re-render straight from _ext_state_cache, the same dict snmprec_generator
        renders into SNMP OIDs. There is deliberately no _publish_*_state()
        counterpart, because there is nothing for Modbus to publish back.
        """
        self._modbus_ctrl = ctrl
        self._log("[StateStore] Modbus telemetry sync enabled.", "info")

    def disable_modbus(self):
        """Deregister the Modbus controller."""
        self._modbus_ctrl = None
        self._log("[StateStore] Modbus telemetry sync disabled.", "info")

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

    # Simulated time advances by MEASURED elapsed seconds, not the configured cadence.
    # A stall longer than this multiple of the interval is clamped, so one long gap
    # cannot expire every timer at once.
    _DT_MAX_MULT = 5.0

    def _advance_clock(self) -> float:
        """Seconds elapsed since the previous tick, from a monotonic clock.

        The ticker sleeps `_tick_interval` and THEN does the tick's work, so the real
        period is always interval + work — never the interval itself. Billing every
        accumulator the configured constant made simulated time run SLOW by the work
        fraction (measured ~80 % of real time here), and the error grows with the
        topology: run-hours, stage timers, transfer sequencing, UPS autonomy, energy
        registers and SNMP uptime all drifted against the wall clock by an amount that
        varied with load and with the machine, so timing-sensitive runs were not
        reproducible. Measuring the gap keeps simulated time locked to real time.

        Clamped, because a debugger breakpoint, a laptop suspend or a long GC pause
        would otherwise inject one enormous dt and expire every timer at once — a
        UPS would drain to empty in a single tick. Under a stall, simulated time lags
        rather than jumping.
        """
        now = time.monotonic()
        prev = self._last_tick_t
        self._last_tick_t = now
        if prev is None:                       # first tick, or resuming from pause
            self._dt = self._tick_interval
        else:
            self._dt = max(0.0, min(now - prev,
                                    self._tick_interval * self._DT_MAX_MULT))
        return self._dt

    def _ticker_loop(self):
        while not self._stop_ev.wait(self._tick_interval):
            if self._pause_ev.is_set():
                # Drop the timestamp so the resuming tick is not billed for the whole
                # pause — a paused simulator is stopped, not running slowly.
                self._last_tick_t = None
                continue
            self._advance_clock()
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
        built from the cooling-layer edges.

        Keyed on the device inventory, exactly like _cooling_context and for the
        same reason. This used to cache UNCONDITIONALLY, and the ticker calls it
        every tick through _compute_leak_heat — including on a freshly started
        server, before any topology exists. So it froze at {} and `/topology/upload`
        never rebuilt it (nothing there invalidates the store; the only caller of
        invalidate_cooling_context is a device edit).

        Two consequences, both silent for the life of the process:
          * _leak_heat stays empty, so a CDU coolant leak cannot warm ANY die —
            the one mechanism in the model that can reach the 90 °C throttle.
          * _liquid_cooled_servers() is built from this map, so every DLC server
            reads as AIR-cooled: die = 38 + 0.45·usage instead of 35 + 0.30·usage,
            and the cold plate's decoupling from room air disappears.
        Measured live before the fix: a full leak forced on all six DC1 CDUs moved
        the hottest die 66.7 → 67.5 °C over ten minutes, and the fleet die mean sat
        on the air-cooled curve.
        """
        _sig = len(self._dm.get_all_devices()) if self._dm else 0
        if (self._cdu_loop_servers_cache is not None
                and getattr(self, "_cdu_loop_sig", None) == _sig):
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
        self._cdu_loop_sig = _sig
        # Derived from this map, so it has to be rebuilt on the same signature or
        # it keeps answering from the inventory that produced the previous one.
        self._liquid_servers_cache = None
        return out

    def fan_floor_rpm(self, device) -> float:
        """This server's MINIMUM healthy fan speed, in RPM.

        The chassis sets the range (fan_rpm_range: a 1U's 40 mm fans idle at 7 krpm,
        a 4U's 92 mm at 3 krpm) and direct-to-chip lowers the floor again, because
        with the CPU heat in the coolant loop the fans have far less to hold at idle.

        This is the reference the under-speed rule, the Redfish per-fan Health and
        the BMC SNMP fan status all measure against, so a fan that reads healthy on
        one interface cannot read failed on another. 0 for non-servers."""
        if device.device_type != DeviceType.SERVER:
            return 0.0
        lo, _hi = fan_rpm_range(getattr(device, "model_name", "") or "")
        if device.name in self._liquid_cooled_servers():
            lo *= _DTC_IDLE_FACTOR
        return float(lo)

    def _fan_speed_pct(self, device) -> float:
        """Chassis fan speed as a % of this device's own minimum duty — the metric
        the FanUnderSpeed/FanFailure rules evaluate. See DeviceFact.fan_speed_pct
        for why the rules cannot use raw RPM.

        A chassis that is POWERED OFF reports 100: its fans are stopped because the
        operator turned it off, which is not a fan fault. Alarming on it would fire a
        fan failure on every intentionally-powered-down server in the fleet."""
        if device.device_type != DeviceType.SERVER:
            return 100.0
        if getattr(device, "power_state", "On") == "Off":
            return 100.0
        floor = self.fan_floor_rpm(device)
        if floor <= 0:
            return 100.0
        return round(float(getattr(device, "fan_rpm", 0) or 0) / floor * 100.0, 1)

    def _liquid_cooled_servers(self) -> set:
        """Set of all server names sitting on a CDU cold-plate loop (direct-to-
        chip liquid cooling). Cached via the underlying CDU-loop map."""
        # Resolve the loop map FIRST — it drops this cache when the inventory
        # signature moves. Checking our own cache before asking would return the
        # stale set forever, since nothing else clears it, and every DLC server
        # would keep reading as air-cooled after the topology arrived.
        loops = self._cdu_loop_servers()
        if self._liquid_servers_cache is None:
            self._liquid_servers_cache = (set().union(*loops.values())
                                          if loops else set())
            # Publish for the dataset generators, which have no store reference but
            # must agree with it on where a server's fan floor sits.
            global _liquid_server_cache
            _liquid_server_cache = self._liquid_servers_cache
        return self._liquid_servers_cache

    # ── Plant-wide cooling cascade ──────────────────────────────────────────
    @staticmethod
    def _unit_index(name: str) -> int:
        """Trailing digits of a device's leading name segment: CHWP3-DC1-CP → 3."""
        head = (name or "").split("-", 1)[0]
        digits = ""
        for ch in reversed(head):
            if not ch.isdigit():
                break
            digits = ch + digits
        return int(digits) if digits else 0

    def _build_trains(self, dc: str, plant: Dict[str, list]) -> tuple:
        """Group this DC's plant into COOLING TRAINS from the cooling-loop topology.

        A train is the STAGED heat path: a chiller, the evaporator (chilled-water)
        pump that feeds it, and the condenser-water pump that carries its condenser
        heat. Lose any member and that train stops cooling — which is why the
        electrical feed must follow the train, not the device type. A plant with N+1
        trains rides out the loss of any one of them.

        The tower cell each chiller is piped to is recorded ("tower") for topology
        and display, but it is NOT a train member and is NOT staged with the train:
        cells sit on the common condenser-water header and ALL of them run, sharing
        the airflow at low fan speed (see core/cooling_model — tower_cells_needed /
        tower_cell_speed_frac). Rejection is therefore a header-level capability,
        applied once per DC in _compute_cond_loop / _compute_chw_penalty.

        Chilled-water pumps beyond the one matching each chiller (CHWP4 on a
        three-chiller plant) sit on the common CHW header as the N+1 standby: any
        train's evaporator pump can be backed up by it.

        Returns (trains, spare_chwp). Each train is
        {"chiller", "chwp", "cwp", "tower", "members"} — "members" excludes the tower.
        """
        trains: list = []
        spare: list = []
        if self._topology is None:
            return trains, spare
        try:
            id_by_name = {d.name: d.id for d in self._dm.get_all_devices()}
            name_by_id = {v: k for k, v in id_by_name.items()}
            nbrs: Dict[str, set] = {}
            for u, v, _w in self._topology.get_edges_by_layer("cooling"):
                nbrs.setdefault(u, set()).add(v)
                nbrs.setdefault(v, set()).add(u)

            def adj(name, pred):
                out = []
                for nid in nbrs.get(id_by_name.get(name, ""), ()):
                    n = name_by_id.get(nid)
                    if n and pred(n):
                        out.append(n)
                return sorted(out)

            pumps = set(plant.get("pump", []))
            towers = set(plant.get("cooling_tower", []))
            is_cwp = lambda n: n.upper().startswith("CWP") or "COND" in n.upper()
            claimed_chwp: set = set()
            for chiller in sorted(plant.get("chiller", []), key=self._unit_index):
                idx = self._unit_index(chiller)
                chwps = adj(chiller, lambda n: n in pumps and not is_cwp(n))
                cwps  = adj(chiller, lambda n: n in pumps and is_cwp(n))
                twrs  = adj(chiller, lambda n: n in towers)
                # Prefer the index-matched evaporator pump (CHL2 ↔ CHWP2); a chiller
                # that is also wired to the header standby must not claim it.
                chwp = next((n for n in chwps if self._unit_index(n) == idx),
                            next((n for n in chwps if n not in claimed_chwp), None))
                if chwp:
                    claimed_chwp.add(chwp)
                cwp = next((n for n in cwps if self._unit_index(n) == idx),
                           cwps[0] if cwps else None)
                tower = next((n for n in twrs if self._unit_index(n) == idx),
                             twrs[0] if twrs else None)
                # The tower is header equipment, not a train member: it neither stages
                # with the train nor takes the train down on its own.
                members = [m for m in (chiller, chwp, cwp) if m]
                trains.append({"chiller": chiller, "chwp": chwp, "cwp": cwp,
                               "tower": tower, "members": members,
                               "complete": bool(chwp and cwp)})
            spare = sorted(n for n in pumps
                           if not is_cwp(n) and n not in claimed_chwp)
        except Exception:
            log.exception("[StateStore] cooling train build error for %s", dc)
        return trains, spare

    def _cooling_context(self) -> dict:
        """Cached maps tying servers/rooms to the cooling plant that feeds them:
          crah_by_room  {(dc,room): [crah names]}     — CRAHs cooling each room
          cdu_by_server {server name: cdu name}       — which CDU cools a server
          plant_by_dc   {dc: {chiller|pump|cooling_tower: [names]}}
          trains_by_dc  {dc: [train dicts]}           — complete heat paths
          spare_chwp    {dc: [names]}                 — N+1 header standby pumps
          city_by_dc    {dc: city}                    — site weather for the tower model
          np_kw_by_name {name: nameplate kW}          — plant unit rated draw
          probes_by_dc  {dc: [(name, role)]}          — plant header instruments
        Built once from the device inventory; used to propagate upstream faults.
        """
        # Cache keyed on the device inventory, NOT cached unconditionally. The
        # maps below are derived from the topology, so they are only valid for the
        # inventory that produced them.
        #
        # Unconditional caching meant anything that touched this before a topology
        # was loaded — a poll of /plant/chiller-trips on a freshly started headless
        # server, say — froze it at {} for the life of the process. Every later
        # upload was ignored, _compute_chw_penalty iterated an empty plant_by_dc,
        # and the entire cooling model went silently inert while the BACnet engine
        # kept publishing plausible per-device values on top of it. That is the
        # worst failure mode available here: it looks like a working plant.
        _sig = len(self._dm.get_all_devices()) if self._dm else 0
        if self._cool_ctx is not None and self._cool_ctx_sig == _sig:
            return self._cool_ctx
        crah_by_room: Dict[tuple, list] = {}
        cdu_by_dc: Dict[str, list] = {}
        plant_by_dc: Dict[str, Dict[str, list]] = {}
        city_by_dc: Dict[str, str] = {}
        np_kw_by_name: Dict[str, float] = {}
        probes_by_dc: Dict[str, list] = {}
        try:
            for d in self._dm.get_all_devices():
                dt = d.device_type
                if d.datacenter and d.datacenter not in city_by_dc:
                    city_by_dc[d.datacenter] = getattr(d, "datacenter_city", None)
                if dt == DeviceType.SENSOR:
                    _role = _probe_role(d)
                    if _role:
                        probes_by_dc.setdefault(d.datacenter, []).append((d.name, _role))
                if dt in (DeviceType.CHILLER, DeviceType.PUMP, DeviceType.COOLING_TOWER,
                          DeviceType.CRAH, DeviceType.CDU, DeviceType.VALVE):
                    np_kw_by_name[d.name] = float(getattr(d, "power_draw_w", 0) or 0) / 1000.0
                if dt == DeviceType.CRAH:
                    crah_by_room.setdefault((d.datacenter, d.room), []).append(d.name)
                elif dt == DeviceType.CDU:
                    # Kept OUT of plant_by_dc on purpose: a CDU is not a cooling
                    # train member and must not enter the train/loss arithmetic. It
                    # is a heat exchanger hanging off the chilled-water loop, so it
                    # needs its own per-DC list for the loop model to publish it.
                    cdu_by_dc.setdefault(d.datacenter, []).append(d.name)
                elif dt in (DeviceType.CHILLER, DeviceType.PUMP,
                            DeviceType.COOLING_TOWER, DeviceType.VALVE):
                    plant_by_dc.setdefault(d.datacenter, {}).setdefault(dt.value, []).append(d.name)
                # (Plant overrides used to be keyed by IP, which needed a
                # name → IP map here. They are keyed by device name now, so the
                # head-pressure model publishes its synthetic points against the
                # name it already has and no address lookup is involved.)
        except Exception:
            log.exception("[StateStore] cooling context build error")
        trains_by_dc: Dict[str, list] = {}
        spare_chwp: Dict[str, list] = {}
        for dc, plant in plant_by_dc.items():
            trains_by_dc[dc], spare_chwp[dc] = self._build_trains(dc, plant)
        cdu_by_server: Dict[str, str] = {}
        for cdu_name, servers in self._cdu_loop_servers().items():
            for s in servers:
                cdu_by_server[s] = cdu_name
        self._cool_ctx_sig = _sig
        self._cool_ctx = {"crah_by_room": crah_by_room,
                          "cdu_by_server": cdu_by_server,
                          "cdu_by_dc": cdu_by_dc,
                          "plant_by_dc": plant_by_dc,
                          "trains_by_dc": trains_by_dc,
                          "spare_chwp": spare_chwp,
                          "city_by_dc": city_by_dc,
                          "np_kw_by_name": np_kw_by_name,
                          "probes_by_dc": probes_by_dc}
        return self._cool_ctx

    # Power-chain rank: source (0) → leaf load (4). A device's parents are its
    # lower-rank power neighbours (the feeds above it). Mirrors api/routers/bacnet.py.
    # Electrical hierarchy, source (0) → distribution → leaf load. BOTH sources sit
    # at rank 0: the utility service entrance and the gensets, each feeding a bus
    # below them (utility main board / generator paralleling board). The ATS picks
    # between those two buses, and everything below it is fed from whichever source
    # the ATS has closed onto.
    #
    #   utility_feed ─┐                      generator ─┐
    #                 └─> switchgear ─┐   ┌─ switchgear ┘
    #                                 └─> ats ─┬─> ups ─> rpp ─> pdu ─> IT leaves
    #                                          └─> mcc ─┬────────────> chiller plant
    #                                                   └─> mpp ─────> hall CRAHs
    #
    # The mcc sits at the SAME rank as the ups because both hang directly off the
    # transfer switch — mechanical load is NOT UPS-backed, it rides the transfer gap
    # on chilled-water thermal mass. The mpp is a hall panelboard one tier below the
    # mcc, the mechanical mirror of what the rpp is on the critical side.
    _POWER_RANK = {"utility_feed": 0, "generator": 0,
                   "switchgear": 1, "ats": 2,
                   "ups": 3, "mcc": 3,
                   "rpp": 4, "floor_pdu": 4, "mpp": 4, "pdu": 5}
    # Rank for anything outside the distribution ladder — IT loads, cooling plant,
    # sensors, meters. Must sit strictly below every tier above so the bottom-up
    # cascade visits leaves first and their watts flow up to the source.
    _LEAF_RANK = 9
    # Leaf IT load types whose live wattage drives the cascade.
    _IT_LEAF_TYPES = {"server", "switch", "router", "firewall",
                      "load_balancer", "oob_switch"}
    # Cooling-plant types — electrical loads counted toward facility / PUE.
    _COOLING_TYPES = {"crah", "chiller", "pump", "cooling_tower", "cdu"}
    # Bulk mechanical loads fed from an MCC, i.e. everything that goes dark during
    # a transfer. A CDU is excluded on purpose: it is dual-corded off the rack PDUs
    # and therefore UPS-backed, so it never sees the gap.
    _MECH_LEAF_TYPES = {"crah", "chiller", "pump", "cooling_tower", "valve"}
    # Device types with a real CPU/ASIC die-temperature sensor. ONLY these carry a
    # cpu_temp and are eligible for the HighTemperature (CPU over-temp) trap —
    # power/cooling gear (RPP/PDU/UPS/CRAH/chiller/pump) has no CPU. Mirrors the
    # device_types scoping on trap_rules.HighTemperature.
    _CPU_BEARING_TYPES = (DeviceType.SERVER, DeviceType.SWITCH, DeviceType.ROUTER,
                          DeviceType.FIREWALL, DeviceType.LOAD_BALANCER)
    # IT loads that go hard-dark when every power feed to them dies (total upstream
    # failure). Facility gear reports its own energized/dead state separately.
    _POWER_DEAD_TYPES = (DeviceType.SERVER, DeviceType.SWITCH, DeviceType.ROUTER,
                         DeviceType.FIREWALL, DeviceType.LOAD_BALANCER,
                         DeviceType.OOB_SWITCH)
    # Per-plant-type BACnet point that reports its live electrical draw (kW).
    _PLANT_POWER_POINTS = ("Active_Power", "Motor_Power", "Pump_Power", "Fan_Power")
    # Auxiliary electrical load a TRIPPED plant unit still carries, as a fraction
    # of the draw it would have had running: controls, oil pump, crankcase heater,
    # VFD standby. A machine that has shut down is not disconnected — its MCC
    # bucket is still closed — so its branch meter reads small, not zero. Only a
    # unit whose MCC is genuinely dead reads nothing.
    _PLANT_AUX_FRAC = 0.05
    # CPU die thermal time constant (s). The heatsink + chassis thermal mass mean
    # the die does not track its instantaneous target immediately: cpu_temp relaxes
    # toward the target with this first-order lag. Sized so a brief cooling gap (a
    # genset transfer's ~10-15 s dead-bus window) barely moves the die, while a
    # sustained cooling failure (CDU/CHW fault over minutes) still heats it fully and
    # trips HighTemperature. Steady-state is unchanged — only transients lag.
    _CPU_THERMAL_TAU_S = 150.0
    _UPS_DESIGN_MIN  = 8.0      # UPS autonomy (min) at full load, healthy battery
    # Fraction of the frozen autonomy at which the low-battery alarm asserts. Real
    # UPS ship this as a time-remaining threshold (typically the last 2-3 min of an
    # 8-min string); expressed as a fraction so it tracks a part-loaded string too.
    _UPS_LOW_BATT_FRAC = 0.75
    _GEN_FULL_HOURS  = 24.0     # genset full-tank runtime (h) at full load
    # Injected conditions that take a genset OUT of service: a flat starting battery
    # (cannot crank) plus the two engine-protection shutdowns — low coolant and high
    # temperature — which trip a running set and lock out a start. All render the set
    # non-operational, so it neither starts nor keeps running.
    _GEN_TRIP_CONDS = frozenset({"battery_failure", "low_coolant", "over_temp"})
    _DEFAULT_PDU_RATED_W = 7360.0   # rack-PDU breaker default: 32 A @ 230 V single-phase

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
        # Snapshot into a local before the check+return: the fleet thread can call
        # invalidate_power_context() (sets self._power_ctx = None) between the guard
        # and the return, which would otherwise hand the caller None mid-tick. A
        # local read is atomic, so we return the (possibly just-superseded but
        # non-None) dict and rebuild on the next tick.
        _cached = self._power_ctx
        if _cached is not None and getattr(self, "_power_ctx_sig", None) == _sig:
            return _cached
        children: Dict[str, list] = {}
        parents: Dict[str, list] = {}
        rank: Dict[str, int] = {}
        peak_w: Dict[str, float] = {}
        rated_w: Dict[str, float] = {}
        ev2_ip_panel: Dict[str, str] = {}
        ev2_meters: list = []
        ev2_circuit_pdus: Dict[str, list] = {}
        dc_gens: Dict[str, list] = {}
        dc_utility: Dict[str, str] = {}
        dc_util_swgr: Dict[str, str] = {}
        dc_ats: Dict[str, list] = {}
        dc_ups: Dict[str, list] = {}
        dc_mcc: Dict[str, list] = {}
        mcc_plant: Dict[str, list] = {}
        mcc_ats: Dict[str, Optional[str]] = {}
        ats_src_swgr: Dict[str, Dict[str, str]] = {}
        id_type: Dict[str, str] = {}     # referenced below even if the build throws
        try:
            _devs = self._dm.get_all_devices()
            id_dev = {d.id: d for d in _devs}
            id_type.update({d.id: d.device_type.value for d in _devs})
            id_draw = {d.id: float(getattr(d, "power_draw_w", 0) or 0) for d in _devs}
            id_rated = {d.id: float(getattr(d, "rated_power_w", 0) or 0) for d in _devs}
            for i in id_type:
                rank[i] = self._POWER_RANK.get(id_type[i], self._LEAF_RANK)
            edges = topo.get_edges_by_layer("power") if topo else []

            def _parents(i):
                out = []
                for u, v, _w in edges:
                    if i in (u, v):
                        nb = v if u == i else u
                        if id_type.get(nb) == "energy_monitor":
                            continue                 # meters clamp on, don't feed
                        if rank.get(nb, self._LEAF_RANK) < rank.get(i, self._LEAF_RANK):
                            out.append(nb)
                return out

            for i in id_type:
                parents[i] = _parents(i)
            for u, v, _w in edges:
                pu, pv = rank.get(u, self._LEAF_RANK), rank.get(v, self._LEAF_RANK)
                if pu < pv:
                    children.setdefault(u, []).append(v)
                elif pv < pu:
                    children.setdefault(v, []).append(u)

            # Nameplate peak flowing through each node (leaf→root, redundancy split).
            incoming: Dict[str, float] = {}
            for nid in sorted(id_type, key=lambda x: rank.get(x, self._LEAF_RANK),
                              reverse=True):
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
                # A rack PDU's breaker is FIXED hardware, not sized to how many
                # servers are currently installed. Floor its rating at the breaker
                # so a near-empty rack reads ~0 % from its live draw (not a phantom
                # high % from a rating shrunk to the tiny occupancy). A fuller rack
                # whose nameplate exceeds the default keeps the larger derived value.
                if id_type.get(nid) in ("pdu", "floor_pdu"):
                    r = max(r, self._DEFAULT_PDU_RATED_W)
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
                # A CT clamps a branch LEAVING the panel, never the incomer feeding
                # it. Rank does that test for every tier at once (utility, gen,
                # switchgear, ATS, UPS above an RPP; RPP above a rack PDU) instead of
                # a hand-kept upstream type list that silently misses the new tiers.
                _panel_rank = rank.get(panel, self._LEAF_RANK)
                brs = [b for b in brs if b is not None
                       and rank.get(b.id, self._LEAF_RANK) > _panel_rank]
                # Stable CT-channel assignment (see _ev2_circuit_order). Real EV2 CT
                # channels are physical, so:
                #   • a surviving branch keeps the exact slot it already holds;
                #   • a REMOVED branch leaves a hole (None) — the freed channel stays
                #     spare and reads 0, rather than pulling later branches up (which
                #     would reassign their per-slot kWh registers to other devices);
                #   • a NEW PDU fills the earliest hole first, then extends onto fresh
                #     channels (name-sorted for a deterministic first placement).
                # A fleet add/remove therefore never disturbs the other branches'
                # circuit numbers, live load, or energy accumulators.
                cur_ids = {b.id for b in brs}
                by_name = {b.id: (b.name or "") for b in brs}
                prev    = self._ev2_circuit_order.get(ip, [])
                slots   = [pid if pid in cur_ids else None for pid in prev]
                placed  = {pid for pid in slots if pid is not None}
                newcomers = sorted((b.id for b in brs if b.id not in placed),
                                   key=lambda bid: by_name.get(bid, ""))
                ni = 0
                for idx in range(len(slots)):        # fill freed holes first
                    if ni >= len(newcomers):
                        break
                    if slots[idx] is None:
                        slots[idx] = newcomers[ni]
                        ni += 1
                slots += newcomers[ni:]              # then extend onto new channels
                while slots and slots[-1] is None:   # don't grow with trailing spares
                    slots.pop()
                self._ev2_circuit_order[ip] = slots
                ev2_circuit_pdus[ip] = slots

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

            # ── Per-DC electrical upstream, for the transfer sequencer ────────
            # The genset no longer starts because a UPS happened to go on battery
            # (that inverted cause and effect — the UPS drops BECAUSE the source
            # died). The ATS watches the utility feed and drives everything else.
            for i, t in id_type.items():
                d = id_dev.get(i)
                _dc = (getattr(d, "datacenter", None) or "?") if d else "?"
                if t == "generator":
                    dc_gens.setdefault(_dc, []).append(i)
                elif t == "utility_feed":
                    dc_utility[_dc] = i
                elif t == "ats":
                    dc_ats.setdefault(_dc, []).append(i)
                elif t == "ups":
                    dc_ups.setdefault(_dc, []).append(i)
                elif t == "mcc":
                    dc_mcc.setdefault(_dc, []).append(i)

            # Mechanical loads under each MCC, as (name, device_type). Used to
            # de-energize exactly the half of the plant whose transfer switch is
            # mid-sequence, so an A-side event leaves the B-side chillers running.
            # Each MCC also remembers the ATS feeding it, so a failed transfer
            # switch takes down only its own half.
            # Which switchgear board each ATS sees on each of its two sources. An
            # ATS is the ONE node in the graph with genuinely alternative parents:
            # every other redundant feed (a dual-corded server, an A/B PDU pair)
            # really does share the load, but a transfer switch draws from exactly
            # one source at a time. Without this the cascade would push half the
            # site's watts into a standby genset.
            for aid in (i for i, t in id_type.items() if t == "ats"):
                for p in parents.get(aid, []):
                    if id_type.get(p) != "switchgear":
                        continue
                    gen_side = any(id_type.get(pp) == "generator"
                                   for pp in parents.get(p, []))
                    ats_src_swgr.setdefault(aid, {})[
                        "emergency" if gen_side else "normal"] = p

            # The utility MAIN board per DC — the switchgear fed directly by the
            # service. This is what the ATS actually senses on its normal terminals:
            # a dead main board (tripped main, bus fault, open service feeder) is
            # indistinguishable from a utility outage as far as the transfer switch
            # is concerned, and must crank the gensets just the same.
            for sid in (i for i, t in id_type.items() if t == "switchgear"):
                if any(id_type.get(p) == "utility_feed" for p in parents.get(sid, [])):
                    d = id_dev.get(sid)
                    dc_util_swgr[(getattr(d, "datacenter", None) or "?") if d else "?"] = sid

            for mid in (i for i, t in id_type.items() if t == "mcc"):
                mcc_ats[mid] = next((p for p in parents.get(mid, [])
                                     if id_type.get(p) == "ats"), None)
                for nid in _subtree(mid):
                    if id_type.get(nid) in self._MECH_LEAF_TYPES:
                        d = id_dev.get(nid)
                        if d is not None:
                            mcc_plant.setdefault(mid, []).append(
                                (d.name, id_type[nid]))
        except Exception:
            log.exception("[StateStore] power context build error")

        self._power_ctx = {"children": children, "parents": parents,
                           "rank": rank, "peak_w": peak_w, "rated_w": rated_w,
                           "ev2_ip_panel": ev2_ip_panel, "ev2_meters": ev2_meters,
                           "ev2_circuit_pdus": ev2_circuit_pdus,
                           "dc_gens": dc_gens, "dc_utility": dc_utility,
                           "dc_util_swgr": dc_util_swgr,
                           "dc_ats": dc_ats, "dc_ups": dc_ups, "dc_mcc": dc_mcc,
                           "mcc_plant": mcc_plant, "mcc_ats": mcc_ats,
                           "ats_src_swgr": ats_src_swgr,
                           "id_type": dict(id_type)}
        self._power_ctx_sig = _sig
        return self._power_ctx

    def invalidate_cooling_context(self) -> None:
        """Drop the cached CDU cold-plate loop map so it rebuilds from the cooling
        edges. Call this whenever a cooling link changes — a liquid-cooled server
        joins or leaves a CDU loop, or a CDU is added/removed. Both maps are built
        once per run, so without this a hot-added DLC server keeps reading as
        air-cooled: its heat would go on the room air balance instead of its CDU's
        loop, and the CDU would report a loop load that is missing a member."""
        self._cdu_loop_servers_cache = None
        self._liquid_servers_cache = None

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

    def get_ev2_circuit_pdus(self) -> Dict[str, list]:
        """Public: {ev2_ip: [branch device_id per CT slot]} in the authoritative,
        slot-stable order (None entries mark spare/freed channels). The API uses
        this for the per-circuit device-name column so the names line up with the
        live values, which meter this same ordered branch list.

        Reads the slot map maintained by the tick thread's power-context build
        directly (a cheap dict read) rather than calling _power_context(), so an
        API-thread read never forces a rebuild racing the tick thread."""
        return {ip: list(slots) for ip, slots in self._ev2_circuit_order.items()}

    # Chassis fan power. Fans are a few percent of a server's draw at a normal
    # intake and a real cost at a hot one, because fan power is CUBE-LAW in speed:
    # holding the same die temperature against warmer air means turning faster, and
    # the watts go up with the cube of it. Vendors publish 5–15 % of platform power
    # at elevated inlet, which is what this reproduces at the far end.
    _FAN_BASE_FRAC = 0.04    # fraction of nameplate at the low speed a cool intake needs
    _FAN_FULL_FRAC = 0.16    # fraction at full speed
    _FAN_RAMP_LO_C = 25.0    # below this the fans sit at their floor
    _FAN_RAMP_HI_C = 40.0    # by this the drive is at 100 %

    def _fan_speed_frac(self, device: "Device") -> float:
        """Fan drive speed 0..1 from intake temperature."""
        inlet = float(getattr(device, "inlet_temp", None) or _SUPPLY_SETPOINT_C)
        span = self._FAN_RAMP_HI_C - self._FAN_RAMP_LO_C
        return max(0.0, min(1.0, (inlet - self._FAN_RAMP_LO_C) / span))

    def _server_live_watts(self, device: "Device") -> float:
        """Per-leaf live draw: nameplate scaled by CPU load (idle ~55 %, full
        100 %) — the same curve Redfish _live_watts reports — PLUS chassis fan
        power, which rises with intake temperature. 0 if powered off.

        The fan term is what makes a cooling failure reach the power chain. Without
        it server draw was a function of CPU load alone, so the campaign could push
        fans +925 rpm at a 42.7 °C inlet and watch IT kW, PDU kW and UPS load stay
        flat within noise across all 38 scenarios. On a real floor a cooling event
        is visible on the UPS, and that is the mechanism.
        """
        if getattr(device, "power_state", "On") == "Off":
            return 0.0
        nominal = float(getattr(device, "power_draw_w", 0) or 0)
        if nominal <= 0:
            return 0.0
        load = max(0.0, min(1.0, getattr(device, "cpu_usage", 0) / 100.0))
        # Compute draw, with the base fan allowance already inside the published
        # nameplate curve — so only the RISE above that floor is added on top.
        compute_w = nominal * (0.55 + 0.45 * load)
        spd = self._fan_speed_frac(device)
        fan_w = nominal * (self._FAN_FULL_FRAC - self._FAN_BASE_FRAC) * (spd ** 3)
        return compute_w + fan_w

    def _native_power_points(self) -> Optional[dict]:
        """Facility / IT / mechanical kW read off the electrical gear's OWN meters.

        This is how a real DCIM computes PUE, and it is The Green Grid's Category 1
        measurement plane:

          facility  — at the incoming service. Read from the SWITCHGEAR, not the
                      utility feed: exactly one source board is energized at a time
                      (utility main, or the generator paralleling bus), so summing
                      both boards yields the live total across a transfer without
                      ever double-counting. A meter on the utility feed alone would
                      read zero the moment the site ran on generator.
          IT        — at the UPS OUTPUT (upsOutputPower). Summing both UPS gives the
                      whole dual-corded IT load, and it keeps reading correctly when
                      one side drops to battery, or when both do.
          mechanical— at the MCCs, which carry the bulk plant and nothing else.

        Only these three planes are read. Every ATS, RPP and rack PDU also reports
        kW, but those tiers are NESTED inside the ones above (ats ⊃ ups ⊃ rpp ⊃ pdu),
        so adding them would count the same watts several times over.

        None when the topology has no electrical upstream (an older saved file), so
        the caller falls back to the EV2 sub-meter hierarchy.
        """
        ctx = self._power_context() or {}
        id_type = ctx.get("id_type", {})
        main_kw = it_kw = mech_kw = 0.0
        seen_swgr = seen_ups = False
        for nid, dtv in id_type.items():
            if dtv not in ("switchgear", "ups", "mcc"):
                continue
            d = self._dm.get_device(nid)
            if d is None:
                continue
            st = self._ext_states.get(d.name) or _ext_state_cache.get(d.name) or {}
            if dtv == "switchgear":
                seen_swgr = True
                main_kw += float(st.get("swgr_kw", 0.0) or 0.0)
            elif dtv == "ups":
                seen_ups = True
                it_kw += float(st.get("ups_output_kw", 0.0) or 0.0)
            else:
                mech_kw += float(st.get("mcc_kw", 0.0) or 0.0)
        if not (seen_swgr and seen_ups) or it_kw <= 0.0:
            # No electrical upstream, or the tick loop has not populated the gear's
            # telemetry yet (first tick). Either way there is nothing to read.
            return None
        return {"main_kw": main_kw, "it_kw": it_kw, "mech_kw": mech_kw}

    def get_power_summary(self) -> dict:
        """Live facility power + PUE, the way a real DCIM derives it:
        PUE = facility kW ÷ IT kW.

        Three sources, in descending order of fidelity:

          "native"   — the gear's own meters (switchgear / UPS output / MCC). This is
                       The Green Grid Category 1 plane. See _native_power_points.
          "meters"   — EV2 sub-meter hierarchy, classified by what each clamped
                       panel's subtree contains. Used for topologies with no modeled
                       electrical upstream.
          "computed" — internal power sums, when metering is absent or incoherent.
        """
        ctx = self._power_context()
        through = self._through_live
        it_m = main_m = cool_m = 0.0
        native = self._native_power_points()
        if native is not None:
            it_m, main_m, cool_m = native["it_kw"], native["main_kw"], native["mech_kw"]
        else:
            for m in ctx.get("ev2_meters", []):
                kw = through.get(m["panel"], 0.0) / 1000.0
                role = m.get("role") or ("main" if m.get("facility") else "it")
                if role == "it":
                    it_m += kw           # IT branch sub-meter
                elif role == "main":
                    main_m += kw         # building-feed meter — whole facility (IT+cooling)
                elif role == "cool":
                    cool_m += kw         # cooling-plant sub-meter

        # CDU reclassification. A CDU (in-rack coolant distribution) is dual-corded off
        # the rack PDUs, so its pump draw sits inside the IT reading — on the UPS
        # output in the native plane, on an IT-role sub-meter in the EV2 plane — and
        # would be counted as IT load, understating PUE. Its work is mechanical cooling
        # overhead (Green Grid/ASHRAE put it in the numerator), and the computed path
        # (_COOLING_TYPES) already treats it as cooling. Shift live CDU watts IT→cool so
        # the metered PUE matches: facility is unchanged (the total is conserved), only
        # the IT denominator drops. Bounded by the metered IT so we never go negative.
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

        # Facility power. IT and mechanical are non-overlapping and together cover the
        # whole load, so their sum is the primary facility figure. The building-main
        # reading is a cross-check, not the primary: on a dead bus mid-transfer BOTH
        # source boards read zero while the UPS batteries still carry IT, so a main
        # meter alone would say the site draws nothing. Take the larger.
        # Cooling for PUE uses the STAGED-MODEL cooling electrical (the plant sized
        # to the fleet and sequenced with load), not the metered plant draw — the
        # latter is capped by the small curated device nameplates, which understates
        # cooling and collapses PUE toward 1.0 as the fleet outgrows the curated
        # plant. Take the larger so a genuinely higher metered draw still wins.
        # The model term is scaled by DELIVERED capacity, per DC. It answers "what
        # would a healthy plant draw for this load" — a demand figure — so on its own
        # it cannot see a plant that has stopped running. During a total loss of
        # chilled water it held PUE flat while the branch meters showed the
        # mechanical panel fall by a third; a DCIM integrating meters and one reading
        # this headline disagreed by ~15 % during exactly the failure the simulator
        # exists to rehearse.
        #
        # Scaled, NOT replaced by the meters. The max() below is deliberate: metered
        # plant draw is capped by the small curated device nameplates and collapses
        # PUE toward 1.0 as the fleet outgrows the plant. Dropping it reintroduces
        # that bug. Scaling keeps a healthy plant on today's behaviour and lets the
        # meters through once the plant is genuinely short.
        # DIAGNOSTIC: both sides of the max() are reported below, because the headline
        # figure is their maximum and therefore cannot show which one is winning.
        # That matters now: half the reasoning above is obsolete. The model term is no
        # longer a demand figure — it is the sum of what the plant devices actually
        # draw, so it DOES see a stopped plant (the live campaign measured -22 to
        # -25 kW across four total-loss scenarios). Whether the max() still earns its
        # keep therefore rests entirely on whether cool_m ever exceeds it, which is
        # unanswerable from outside while only the maximum is published.
        cool_model = self.cooling_model_kw()
        cool_for_pue = max(cool_m, cool_model)
        sub_fac = it_m + cool_for_pue
        fac_m = max(sub_fac, main_m)
        # A trustworthy facility reading must be ≥ IT (the facility carries IT +
        # cooling). If it isn't, metering is incomplete → fall back to computed.
        metered = it_m > 0 and fac_m >= it_m
        it_w  = it_m * 1000.0 if it_m > 0 else self._it_w
        fac_w = fac_m * 1000.0 if fac_m > 0 else self._facility_w
        pue = (fac_w / it_w) if it_w > 0 else 0.0
        if not metered:
            source = "computed"
        else:
            source = "native" if native is not None else "meters"
        return {
            "it_watts":       round(it_w, 1),
            "cooling_watts":  round(max(0.0, fac_w - it_w), 1),
            "facility_watts": round(fac_w, 1),
            "pue":            round(pue, 3),
            "source":         source,
            # Diagnostic only — the two inputs to the max() above, so a caller can
            # see which one set the headline. Additive keys; nothing reads them yet.
            "cooling_metered_kw": round(cool_m, 3),
            "cooling_model_kw":   round(cool_model, 3),
            "cooling_source":     ("metered" if cool_m > cool_model else "model"),
        }

    # ── Utility / transfer control surface ────────────────────────────────────
    def set_utility_outage(self, dc: str, failed: bool) -> None:
        """Drop or restore the utility feed for one datacenter. Everything else —
        genset start, ATS transfer, mechanical load-block restart, UPS battery
        discharge — follows from this one input, the way it does on site."""
        self._utility_failed[dc] = bool(failed)

    def set_ats_failed(self, ats_id: str, failed: bool) -> None:
        """Fail (or clear) one transfer switch. In a 2N electrical plant this kills
        only its own side: that UPS goes to battery and that MCC's half of the
        mechanical plant stops, leaving the other side carrying the site."""
        if failed:
            self._ats_failed.add(ats_id)
        else:
            self._ats_failed.discard(ats_id)

    def set_ats_condition(self, ats_id: str, kind: str, on: bool) -> None:
        """Raise or clear a stateful ATS condition (the Simulate-Fault menu's
        latching ATS faults). Each holds until cleared and fires a raise trap on
        assert and a clear trap on deassert — the way a real ASCO 7000 annunciates.

          not_in_auto     — control switch in manual. Annunciation only: no cascade,
                            the switch just stops auto-transferring (a latent risk).
          fail_to_transfer— latched transfer fault. Reuses the _ats_failed model, so
                            it cascades: this switch's UPS drops to battery and its
                            MCC's mechanical leg stops (2N contains it to one side).
          source_lost     — the ATS's normal (utility) source is gone. Drives the
                            real genset/transfer/ride-through sequence for its DC;
                            the autonomous ATS/UPS traps annunciate it.
        """
        dev = self._dm.get_device(ats_id) if self._dm else None
        cb = self._transfer_trap_cb
        if kind == "not_in_auto":
            if on:
                self._ats_not_in_auto.add(ats_id)
            else:
                self._ats_not_in_auto.discard(ats_id)
            if cb and dev is not None:
                cb(dev, "not_in_auto" if on else "returned_to_auto")
        elif kind == "fail_to_transfer":
            self.set_ats_failed(ats_id, on)
            if cb and dev is not None:
                cb(dev, "fail_to_transfer" if on else "transfer_fault_cleared")
        elif kind == "source_lost":
            # Real source loss for this switch's DC — reuse the utility-outage path
            # so the full EPS cascade + its autonomous SOURCE_LOST/transfer traps run.
            dc = getattr(dev, "datacenter", None) if dev is not None else None
            if dc:
                self.set_utility_outage(dc, on)

    def set_ups_forced_battery(self, ups_id: str, kind: "str | None") -> None:
        """Inject (kind="on"|"low") or clear (kind=None) an on-battery fault on one
        UPS. It flips the rectifier's source to 'lost' for THIS unit, so the real
        drain machinery runs: the string counts its finite autonomy down, escalates
        to low-battery, and finally exhausts — at which point the inverter drops the
        load and, on a dual-corded 2N feed, the other side carries it. "low" pre-ages
        the string to its low-battery threshold so the alarm and the drop come quickly
        instead of after the full autonomy (~8 min at load, longer part-loaded)."""
        if kind not in ("on", "low"):
            self._ups_forced_battery.pop(ups_id, None)
            return
        self._ups_forced_battery[ups_id] = kind
        if kind == "low":
            dev = self._dm.get_device(ups_id) if self._dm else None
            st = self._ext_states.get(dev.name) if dev is not None else None
            if st is not None:
                autonomy = max(60.0, float(
                    st.get("ups_runtime_min", self._UPS_DESIGN_MIN)) * 60.0)
                st["ups_autonomy_s"] = autonomy
                # Just past the low-battery fraction, so the next tick alarms low and
                # the short remaining stretch drains to exhaustion.
                st["ups_on_battery_s"] = autonomy * self._UPS_LOW_BATT_FRAC + 1.0

    def get_ups_forced_battery(self, ups_id: str) -> "str | None":
        """Injected on-battery kind for this UPS ("on"/"low"), or None."""
        return self._ups_forced_battery.get(ups_id)

    def set_gen_failed(self, gen_id: str, failed: bool) -> None:
        """Inject or clear a genset fail-to-start. A failed genset never qualifies
        the emergency source, so if the utility is also down the bus stays dead and
        the UPS carries alone until it exhausts — the classic double power failure."""
        if failed:
            self._gen_failed.add(gen_id)
        else:
            self._gen_failed.discard(gen_id)
        cb = self._transfer_trap_cb
        dev = self._dm.get_device(gen_id) if self._dm else None
        if cb and dev is not None and failed:
            cb(dev, "gen_fail_start")   # overcrank: engine failed to start on demand

    def is_gen_failed(self, gen_id: str) -> bool:
        return gen_id in self._gen_failed

    def _gen_offline(self, gen_id: str) -> bool:
        """A genset that cannot be online — an injected fail-to-start, or a trip
        condition (flat battery, low coolant, over-temp). It will not start, and a
        running set trips out."""
        return (gen_id in self._gen_failed
                or bool(self._gen_conditions.get(gen_id, set()) & self._GEN_TRIP_CONDS))

    def set_gen_condition(self, gen_id: str, kind: str, on: bool) -> None:
        """Raise or clear a genset alarm condition (Simulate-Fault menu). Each holds
        until cleared and fires a raise trap on assert and a clear trap on deassert —
        annunciation only, like a real genset controller's discrete alarm inputs.
        kind ∈ {low_fuel, low_coolant, battery_failure, transfer_switch, over_temp}."""
        conds = self._gen_conditions.setdefault(gen_id, set())
        dev = self._dm.get_device(gen_id) if self._dm else None
        st = self._ext_states.get(dev.name) if dev is not None else None
        if on:
            conds.add(kind)
            # Low fuel is a real tank level, not just an alarm bit: drop the gauge to
            # the alarm level NOW — before the trap fires — so a poll on trap receipt
            # already reads low. The alarm asserts BECAUSE the tank is low.
            if kind == "low_fuel" and st is not None:
                st["gen_fuel_pct"] = min(st.get("gen_fuel_pct", 100.0), 8.0)
                st["gen_alarm_low_fuel"] = 1.0
                _ext_state_cache[dev.name] = dict(st)
        else:
            conds.discard(kind)
            if kind == "low_fuel" and st is not None:
                # Clearing = refuelled. Restore the gauge (the walk holds it there).
                st["gen_fuel_pct"] = 100.0
                st["gen_alarm_low_fuel"] = 0.0
                _ext_state_cache[dev.name] = dict(st)
            if not conds:
                self._gen_conditions.pop(gen_id, None)
        cb = self._transfer_trap_cb
        if cb and dev is not None:
            cb(dev, f"gen_{kind}" if on else f"gen_{kind}_clear")

    def get_gen_conditions(self, gen_id: str) -> list:
        return sorted(self._gen_conditions.get(gen_id, set()))

    def set_swgr_condition(self, swgr_id: str, kind: str, on: bool) -> None:
        """Raise or clear a switchgear fault (Simulate-Fault menu). Either fault takes
        the board out of service — the bus goes dead and everything it feeds (its ATS's
        source, so the UPS below) de-energizes — and fires a raise/clear trap, the way
        a Digitrip / protective relay annunciates. kind ∈ {breaker_trip, bus_fault}."""
        conds = self._swgr_conditions.setdefault(swgr_id, set())
        if on:
            conds.add(kind)
        else:
            conds.discard(kind)
            if not conds:
                self._swgr_conditions.pop(swgr_id, None)
        cb = self._transfer_trap_cb
        dev = self._dm.get_device(swgr_id) if self._dm else None
        if cb and dev is not None:
            cb(dev, f"swgr_{kind}" if on else f"swgr_{kind}_clear")

    def get_swgr_conditions(self, swgr_id: str) -> list:
        return sorted(self._swgr_conditions.get(swgr_id, set()))

    def set_pdu_condition(self, pdu_id: str, kind: str, on: bool) -> None:
        """Raise or clear a latching PDU fault. kind ∈ {breaker_trip}.

        A tripped PDU breaker is not an annunciation, it is a POWER EVENT: the
        outlets go dead and everything corded to them loses that feed. A dual-corded
        load rides it on its other PSU; a single-corded one goes down. That is the
        whole reason to rehearse it, and it is why this is store state the
        energization walk reads rather than another pinned status metric.

        It LATCHES, like the switchgear main it mirrors — a real branch breaker
        stays open until somebody physically resets it, so clearing is an explicit
        operator action and nothing self-heals.

        Modelled at the whole-PDU level, i.e. the input breaker rather than one
        branch pole. A real rack PDU trips a branch feeding a SUBSET of outlets;
        representing that faithfully needs per-outlet energization, which the power
        graph does not carry — cords attach to the PDU. Killing the whole strip is
        the honest simplification: it overstates the blast radius of a branch trip
        and exactly matches an input-breaker or feed loss.
        """
        conds = self._pdu_conditions.setdefault(pdu_id, set())
        if on:
            conds.add(kind)
        else:
            conds.discard(kind)
            if not conds:
                self._pdu_conditions.pop(pdu_id, None)
        self.invalidate_power_context()
        # No trap fired from here, deliberately. trap_rules already carries the
        # raise/clear pair on pdu_breaker_status (ok <-> tripped) and the pin below
        # flips that point, so the rule engine annunciates it. Firing one here too
        # would double-annunciate — and the transfer-trap callback is keyed by an
        # ATS/switchgear map that has no PDU entry, so it would raise KeyError.

    def get_pdu_conditions(self, pdu_id: str) -> list:
        return sorted(self._pdu_conditions.get(pdu_id, set()))

    def break_power_feed(self, device_id: str, which: str, on: bool) -> None:
        """Open (on) or restore a power feeder into a device. `which`:
          "input"     — every upstream power feeder (a single-source load's only cord,
                        or a switchgear's utility/generator feed).
          "normal"    — an ATS's normal (utility-board) feeder only.
          "emergency" — an ATS's emergency (generator-board) feeder only.
        Energization follows intact feeders (see _compute_energized), so an open feeder
        de-energizes everything strictly downstream of it — and drops any UPS below it
        to battery — with the sources still healthy, which is the real cause of most
        on-battery events."""
        topo = self._topology
        if topo is None:
            return
        ctx = self._power_context() or {}
        if which in ("normal", "emergency"):
            board = ctx.get("ats_src_swgr", {}).get(device_id, {}).get(which)
            edges = [(device_id, board)] if board else []
        else:
            edges = [(device_id, p)
                     for p in ctx.get("parents", {}).get(device_id, [])]
        for a, b in edges:
            if on:
                topo.break_link(a, b, "power")
            else:
                topo.restore_link(a, b, "power")

    def is_power_feed_broken(self, device_id: str, which: str) -> bool:
        """Whether the named power feeder into this device is currently open."""
        ctx = self._power_context() or {}
        if which in ("normal", "emergency"):
            board = ctx.get("ats_src_swgr", {}).get(device_id, {}).get(which)
            return board is not None and frozenset((device_id, board)) in self._broken_power
        return any(frozenset((device_id, p)) in self._broken_power
                   for p in ctx.get("parents", {}).get(device_id, []))

    def normal_source_ok(self, dc: str, ctx: Optional[dict] = None) -> bool:
        """Is the NORMAL source available at this DC's transfer switches?

        A transfer switch senses voltage on its own normal terminals — it has no
        idea whether the utility itself is up. Three things put that sensing point
        dead, and all three start the engines on real gear:

          • the utility service fails (the injected outage),
          • the utility MAIN switchgear goes dead — main breaker tripped or bus
            faulted (an ASCO/Eaton ATS reads under-voltage either way),
          • the service feeder between the meter and that board is open.

        Modelling only the first would let a bus fault on the utility board black
        the site out with two healthy gensets sitting in standby — which is exactly
        the failure the emergency system exists to prevent.

        A DC with no modelled utility feed has no normal source to lose, so it
        reports OK and behaves as it did before this model existed.
        """
        ctx = ctx if ctx is not None else (self._power_context() or {})
        if dc not in ctx.get("dc_utility", {}):
            return True
        if self._utility_failed.get(dc, False):
            return False
        board = ctx.get("dc_util_swgr", {}).get(dc)
        if board is None:
            return True                      # no modelled main board — feed decides
        if self._swgr_conditions.get(board):
            return False                     # main tripped / bus faulted
        feed = ctx["dc_utility"][dc]
        return frozenset((board, feed)) not in self._broken_power

    def get_ats_conditions(self, ats_id: str) -> list:
        """Active stateful ATS conditions on this switch, for the fault UI."""
        out = []
        if ats_id in self._ats_not_in_auto:
            out.append("not_in_auto")
        if ats_id in self._ats_failed:
            out.append("fail_to_transfer")
        dev = self._dm.get_device(ats_id) if self._dm else None
        dc = getattr(dev, "datacenter", None) if dev is not None else None
        # Normal source lost — from the utility OR from a dead main board, since
        # the switch cannot tell the two apart.
        if dc and not self.normal_source_ok(dc):
            out.append("source_lost")
        return out

    def get_electrical_status(self) -> dict:
        """Per-DC view of the utility/generator transfer state, for the API."""
        ctx = self._power_context() or {}
        out = {}
        for dc, st in self._transfer.all_status().items():
            out[dc] = {
                "state":           st.state,
                "ats_source":      st.source,
                "bus_energized":   st.source_live,
                "utility_ok":      not self._utility_failed.get(dc, False),
                # What the ATS actually senses: utility OK *and* its main board live.
                "normal_source_ok": self.normal_source_ok(dc, ctx),
                "gen_status":      st.gen_status,
                "gen_at_voltage":  st.gen_at_voltage,
                "ups_input_ok":    st.ups_input_ok,
                "mech_blocks_on":  st.mech_blocks_on,
                "seconds_in_state": round(st.t, 1),
                "ats_failed": [i for i in ctx.get("dc_ats", {}).get(dc, [])
                               if i in self._ats_failed],
            }
        return out

    def get_electrical_device_metrics(self) -> list:
        """Per-device live metrics for the electrical upstream (utility feed /
        switchgear / ATS / MCC / MPP / generator) — the same values served over SNMP,
        for the Live Metrics page tabs. Each row carries the device's own metric
        fields."""
        _pfx = {"utility_feed": "util_", "switchgear": "swgr_", "ats": "ats_",
                "mcc": "mcc_", "mpp": "mpp_", "generator": "gen_"}
        dm = getattr(self, "_dm", None)
        if dm is None:
            return []
        out = []
        for d in dm.get_all_devices():
            dtv = d.device_type.value
            pfx = _pfx.get(dtv)
            if pfx is None:
                continue
            st = _ext_state_cache.get(d.name, {})
            row = {"id": d.id, "name": d.name, "device_type": dtv,
                   "datacenter": getattr(d, "datacenter", "") or "",
                   "room": getattr(d, "room", "") or ""}
            for k, v in st.items():
                if k.startswith(pfx):
                    row[k] = v
            out.append(row)
        return out

    def _step_transfer(self) -> None:
        """Advance every DC's transfer sequence, then publish the two things the
        rest of the tick needs from it: which mechanical units are unpowered, and
        whether each UPS is seeing a live source."""
        ctx = self._power_context() or {}
        dc_gens = ctx.get("dc_gens", {})
        dc_utility = ctx.get("dc_utility", {})
        dc_mcc = ctx.get("dc_mcc", {})
        mcc_plant = ctx.get("mcc_plant", {})
        mcc_ats = ctx.get("mcc_ats", {})
        dcs = set(dc_gens) | set(dc_utility) | set(dc_mcc)
        unpowered: set = set()
        for dc in dcs:
            # What the transfer switch senses on its NORMAL terminals: the utility
            # service AND the main board it is fed from. A tripped main or a faulted
            # bus reads as under-voltage to the ATS and cranks the engines, same as
            # a grid outage. A topology with no modeled utility feed (an older saved
            # file) has no source to lose, so it stays on "utility" forever.
            utility_ok = self.normal_source_ok(dc, ctx)
            # A genset can crank if it has fuel. With both gensets on a common
            # paralleling bus, one is enough to qualify the emergency source. A
            # genset the tick loop has not initialised yet is assumed fuelled, so
            # the first tick after load doesn't read as a site-wide failed start.
            # A genset can crank only with fuel and no out-of-service condition — a
            # fail-to-start, a flat starting battery, or an engine-protection lockout
            # (low coolant / over-temp). Either machine on the paralleling bus is
            # enough to qualify the emergency source.
            gens_startable = any(
                self._gen_state(gid).get("gen_fuel_pct", 100.0) > 0.5
                and not self._gen_offline(gid)
                for gid in dc_gens.get(dc, [])
            )
            st = self._transfer.step(dc, utility_ok, gens_startable, self._dt)
            live_types = st.mech_types_on()
            dc_dark = 0
            src_ok, _tie = self._mcc_tie_state(dc, ctx)
            for mid in dc_mcc.get(dc, []):
                dead = not src_ok.get(mid, True)
                for nm, dtv in mcc_plant.get(mid, []):
                    if dead or dtv not in live_types:
                        unpowered.add(nm)
                        dc_dark += 1
            # Ride-through clock: how long this DC's plant has been short of full
            # power. Reset the instant every unit is energized again.
            self._mech_dead_s[dc] = (0.0 if dc_dark == 0
                                     else self._mech_dead_s.get(dc, 0.0) + self._dt)
        self._plant_unpowered_names = unpowered

    def _compute_unpowered_loads(self) -> None:
        """Names of IT loads left with no live cord, from the per-outlet relay state.

        A load dies only when EVERY feed it has is switched off. That is the whole
        point of dual-cording: kill the A outlet on a 1+1 server and it keeps running
        on B, drawing the same total through the surviving strip. Treating one dead
        outlet as a dead server would erase the redundancy the estate is built on and
        make an A/B test look like an outage.

        A load with no recorded cord at all is left alone rather than assumed dead —
        absence of cabling data is not evidence of a missing feed.
        """
        off_by_pdu: Dict[str, set] = {}
        for dev in self._dm.get_all_devices():
            if dev.device_type.value not in ("pdu", "floor_pdu"):
                continue
            st = self._ext_states.get(dev.name) or {}
            offs = st.get("pdu_outlets_off") or []
            # The strip-level fault stays meaningful: "everything off" is a PDU that
            # has lost its own feed, not 42 relays opened one at a time.
            if st.get("pdu_outlet_status", "on") == "off":
                off_by_pdu[dev.id] = None          # sentinel: the whole strip is dead
            elif offs:
                off_by_pdu[dev.id] = {int(o) for o in offs}

        dead: set = set()
        dead_cords: set = set()
        if not off_by_pdu:
            self._load_unpowered_names = dead
            self._dead_cord_pairs = dead_cords
            return
        for dev in self._dm.get_all_devices():
            if dev.device_type.value not in self._IT_LEAF_TYPES:
                continue
            try:
                feeds = self._topology.power_feeds(dev.id)
            except Exception:
                continue
            if not feeds:
                continue
            live = 0
            for f in feeds.values():
                sup, outlet = f.get("supply_id"), f.get("outlet")
                if sup not in off_by_pdu:
                    live += 1
                    continue
                offs = off_by_pdu[sup]
                if offs is not None and outlet not in offs:
                    live += 1
                else:
                    dead_cords.add(frozenset((dev.id, sup)))
            if live == 0:
                dead.add(dev.name)
        self._load_unpowered_names = dead
        self._dead_cord_pairs = dead_cords

    def _mcc_tie_state(self, dc: str, ctx: dict) -> tuple:
        """Which MCCs have a source, and whether the bus tie is closed.

        An N+1 chiller plant cannot be fed by statically splitting its trains across
        two transfer switches: with three trains and two sources, losing the source
        that carries two of them leaves one, and the load needs two. Real plants
        solve this with a MAIN-TIE-MAIN mechanical switchboard — the two MCC buses
        sit either side of a normally-open tie breaker, and on loss of one source the
        tie closes so the surviving source carries the whole mechanical load. Each
        MCC is rated for it (800 A ≈ 499 kW against a mechanical load well under).

        The tie only answers for a failed TRANSFER SWITCH. When the utility drops,
        both ATS are on a dead bus and there is nothing to tie to — that outage is
        handled by the gensets and the staged mechanical restart.

        Returns ({mcc_id: has_source}, tie_closed).
        """
        mcc_ats = ctx.get("mcc_ats", {})
        mccs = ctx.get("dc_mcc", {}).get(dc, [])
        own = {m: (mcc_ats.get(m) not in self._ats_failed) for m in mccs}
        any_ok = any(own.values())
        tie_closed = any_ok and not all(own.values())
        return {m: (own[m] or tie_closed) for m in mccs}, tie_closed

    def ext_state_for(self, device: "Device") -> dict:
        """The LIVE ext-state dict for a device, created if the tick has not yet.

        Returns the real dict, not a copy: callers that switch an outlet relay need
        their write to be the value the next tick reads. The read-only helpers
        elsewhere fall back to the module cache and may hand back a snapshot, which
        is fine for display and wrong for control.
        """
        st = self._ext_states.get(device.name)
        if st is None:
            st = dict(_ext_state_cache.get(device.name) or {})
            self._ext_states[device.name] = st
        return st

    def _gen_state(self, gen_id: str) -> dict:
        """Ext-state dict for a generator device id (empty if unknown)."""
        d = self._dm.get_device(gen_id)
        if d is None:
            return {}
        return self._ext_states.get(d.name) or _ext_state_cache.get(d.name) or {}

    def _ups_source_ok(self, device: "Device") -> bool:
        """Is this UPS's rectifier seeing a qualified source right now? False while
        its ATS is mid-transfer, its own ATS has failed, or the whole DC is riding
        a dead bus — which is precisely when a real UPS drops to battery. An injected
        On-Battery fault forces this False so the real autonomy countdown runs and the
        string physically drains to exhaustion."""
        if device.id in self._ups_forced_battery:
            return False
        # Input-side faults the rectifier cannot ride: a dead rectifier, a lost phase,
        # or an input voltage/frequency out of the acceptance window all make the UPS
        # reject the feeder and run from battery even though the cord is live. Read
        # from the injected override (the operator's intent), not the mid-tick walk,
        # so it is stable and never trips on the healthy ±V jitter. Thresholds match
        # the trap rules, so the alarm and the battery agree.
        ov = self.device_overrides.get(device.id)
        if ov:
            if ov.get("ups_rectifier_status") == "failure":
                return False
            if ov.get("ups_phase_status") == "failure":
                return False
            _v = ov.get("ups_input_voltage")
            if _v is not None and (_v > 440.0 or _v < 360.0):
                return False
            _f = ov.get("ups_input_frequency")
            if _f is not None and (_f < 49.0 or _f > 51.0):
                return False
        ctx = self._power_context() or {}
        dc = getattr(device, "datacenter", None) or "?"
        if dc not in ctx.get("dc_utility", {}) and dc not in ctx.get("dc_gens", {}):
            return True                      # not wired into a modeled electrical plant
        # Graph-accurate: the rectifier sees a source iff an INTACT feeder reaches a
        # live ATS output (computed edge-aware in _compute_energized). This captures
        # EVERY cause of on-battery — a source loss, a failed or mid-transfer ATS, OR a
        # broken feeder anywhere on util→switchgear→ATS→UPS — not just a DC-wide event.
        return self._ups_input_live.get(device.id, True)

    def _step_grid_freq(self, grid_key: str) -> float:
        """Advance one grid region's frequency by a small mean-reverting random walk
        and return it. Nominal 50 Hz, pulled back toward 50 and held inside the ±0.05
        Hz normal operating band. Called once per tick per city by the power-flow pass
        so every utility meter on that grid reads the same frequency this tick."""
        f = self._grid_freq.get(grid_key, 50.0)
        f += (50.0 - f) * 0.1 + random.uniform(-0.008, 0.008)
        f = max(49.90, min(50.10, f))
        self._grid_freq[grid_key] = f
        return f

    def _grid_frequency(self, grid_key: str) -> float:
        """Read this grid region's current frequency. The power-flow pass steps it
        once per tick; if a meter is read before that (or its city is unknown), fall
        back to a one-off step so the reading is never a flat 50.00."""
        if grid_key in self._grid_freq:
            return self._grid_freq[grid_key]
        return self._step_grid_freq(grid_key)

    def _step_electrical(self, device: "Device", st: dict) -> None:
        """Live telemetry for the electrical upstream, all of it derived from the
        DC's transfer state so the one-line reads consistently end to end.

        Real protocol coverage, for reference:
          • utility_feed — a revenue / ION-class meter at the service entrance,
            typically Modbus TCP (Schneider PowerLogic, Eaton PXM).
          • switchgear   — breaker status and bus metering over Modbus or SNMP.
          • ats          — SNMP is the norm here (ASCO 7000 with an ACC card,
            Eaton ATC-900, APC). Position, source availability and a transfer
            counter are the points every vendor exposes.
          • mcc          — per-bucket breaker meters, Modbus RTU behind a gateway.
        The sim serves these over SNMP because that is the transport it already
        speaks for power gear; Modbus is not modelled."""
        ctx = self._power_context() or {}
        dc = getattr(device, "datacenter", None) or "?"
        ats = self._transfer.status(dc)
        dtv = device.device_type.value
        rated = ctx.get("rated_w", {}).get(device.id, 0.0)
        thr = self._through_live.get(device.id, 0.0)
        load_pct = round(max(0.0, min(100.0, thr / rated * 100.0)), 1) if rated > 0 else 0.0
        utility_ok = not self._utility_failed.get(dc, False)
        # utility_ok is the SERVICE (what the revenue meter sees). normal_ok is what
        # the ATS senses on its normal terminals — the service through a live main
        # board. They differ when the main board itself is dead, which is precisely
        # the case the ATS must treat as a source loss.
        normal_ok = self.normal_source_ok(dc, ctx)
        util_board = ctx.get("dc_util_swgr", {}).get(dc)

        # 3-phase line current from real power: I = P / (sqrt(3) * V_LL * PF).
        def _amps(kw: float, volts: float, pf: float) -> float:
            if volts <= 0 or pf <= 0:
                return 0.0
            return round(kw * 1000.0 / (1.7320508 * volts * pf), 1)

        if dtv == "utility_feed":
            # Schneider PowerLogic ION9000 revenue/PQ meter — reports MEASURED
            # quantities (per-phase V/I, imbalance, THD, kW/kVAR/kVA, peak demand),
            # NOT a "% of rating" (a meter has no rating to load against).
            st["util_status"] = "normal" if utility_ok else "failed"
            if not utility_ok:
                # Dead feed — every instantaneous measurement reads zero. Cumulative
                # registers (energy, peak demand) are NOT reset: a real meter holds
                # them through an outage.
                for _k in ("util_voltage", "util_current", "util_frequency", "util_kw",
                           "util_power_factor", "util_xfmr_loss_kw", "util_kvar",
                           "util_kva", "util_va", "util_vb", "util_vc", "util_ia",
                           "util_ib", "util_ic", "util_phase_imbalance",
                           "util_thd_v", "util_thd_i"):
                    st[_k] = 0.0
            else:
                # Class-0.2 revenue meter: every reported quantity carries a small
                # (±0.2 %) measurement error, so readings aren't the exact model sum.
                def _m(x: float) -> float:
                    return x * (1.0 + random.uniform(-0.002, 0.002))

                load_frac = (thr / rated) if rated > 0 else 0.0
                # MV/LV service transformer between this revenue meter and the LV main
                # switchgear: the import reads MORE than the downstream load. Constant
                # core/iron loss (magnetising) + copper/I²R loss ∝ load². ~0.4 % no-load
                # + ~1.1 % at full load ⇒ 98.5–99.6 % efficient. Downstream gear is LV;
                # its busbar/breaker losses are <0.1 % and are not modelled, so only
                # this meter marks the transformer up.
                xfmr_loss_w = ((0.004 + 0.011 * load_frac * load_frac) * rated
                               if rated > 0 else thr * 0.015)
                st["util_xfmr_loss_kw"] = round(xfmr_loss_w / 1000.0, 2)
                kw_true = (thr + xfmr_loss_w) / 1000.0
                # PFC front-ends run best near full load — PF climbs with load, not RNG.
                pf = round(min(0.995, 0.955 + 0.035 * load_frac
                               + random.uniform(-0.005, 0.005)), 3)
                # LV bus droops slightly under load (~2 % at full). System V is L-L;
                # per-phase meters read L-N (= V_LL / √3).
                v_ll_true = 400.0 * (1.0 - 0.02 * load_frac)
                v_ln = v_ll_true / 1.7320508
                # Per-phase quantities with small realistic imbalance: voltages within
                # ~±0.6 %, currents within ~±2 % (DC loads are well balanced but not
                # perfectly). Phase imbalance % = worst phase-current deviation / mean.
                v_ph = [_m(v_ln * (1.0 + random.uniform(-0.006, 0.006))) for _ in range(3)]
                i_avg = (kw_true * 1000.0) / (3.0 * v_ln * pf) if (v_ln > 0 and pf > 0) else 0.0
                i_ph = [_m(i_avg * (1.0 + random.uniform(-0.02, 0.02))) for _ in range(3)]
                i_mean = sum(i_ph) / 3.0 if i_ph else 0.0
                imbal = (max(abs(x - i_mean) for x in i_ph) / i_mean * 100.0) if i_mean > 0 else 0.0
                # Reported aggregates. kVA = kW / PF; kVAR = √(kVA² − kW²).
                kw = round(_m(kw_true), 1)
                kva = round(kw / pf, 1) if pf > 0 else 0.0
                kvar = round((kva * kva - kw * kw) ** 0.5, 1) if kva >= kw else 0.0
                st["util_va"] = round(v_ph[0], 1)
                st["util_vb"] = round(v_ph[1], 1)
                st["util_vc"] = round(v_ph[2], 1)
                st["util_ia"] = round(i_ph[0], 1)
                st["util_ib"] = round(i_ph[1], 1)
                st["util_ic"] = round(i_ph[2], 1)
                st["util_voltage"] = round(_m(v_ll_true), 1)     # L-L system voltage
                st["util_current"] = round(i_mean, 1)            # avg line current
                st["util_phase_imbalance"] = round(imbal, 1)
                st["util_frequency"] = round(
                    self._grid_frequency(getattr(device, "datacenter_city", None) or dc), 2)
                st["util_kw"] = kw
                st["util_kvar"] = kvar
                st["util_kva"] = kva
                st["util_power_factor"] = pf
                # Voltage THD is stiff at the service (~1–2.5 %); current THD is higher
                # even behind PFC front-ends (~4–8 %).
                st["util_thd_v"] = round(random.uniform(1.0, 2.5), 1)
                st["util_thd_i"] = round(random.uniform(4.0, 8.0), 1)
                # Peak demand — the billing quantity is a 15-min sliding demand; a
                # running peak of measured kW is the sim's proxy.
                st["util_peak_kw"] = round(max(st.get("util_peak_kw", 0.0), kw), 1)
                # cumulative energy (kWh) — ~1-min tick, same convention as UPS/PDU.
                st["util_energy_kwh"] = round(st.get("util_energy_kwh", 0.0) + kw / 60.0, 3)

        elif dtv == "switchgear":
            # Which source this board belongs to decides whether it is live: the
            # utility main dies with the utility, the paralleling board comes alive
            # when the gensets reach voltage.
            def _is_gen(pid: str) -> bool:
                p = self._dm.get_device(pid)
                return p is not None and p.device_type == DeviceType.GENERATOR

            gen_side = any(_is_gen(p) for p in ctx.get("parents", {}).get(device.id, []))
            _swc = self._swgr_conditions.get(device.id, set())
            _tripped, _busfault = "breaker_trip" in _swc, "bus_fault" in _swc
            # The utility main board is live off the SERVICE through an intact feeder
            # (normal_ok covers both); the paralleling board comes alive when the
            # gensets reach voltage. Either way its own main/bus must be healthy.
            _norm_src = normal_ok if device.id == util_board else utility_ok
            live = (ats.gen_at_voltage if gen_side else _norm_src) and not (_tripped or _busfault)
            st["swgr_source"] = "generator" if gen_side else "utility"
            st["swgr_bus_status"] = ("fault" if _busfault
                                     else "energized" if live else "dead")
            st["swgr_breaker_status"] = ("tripped" if _tripped
                                         else "closed" if live else "open")
            # Eaton Magnum DS main / ASCO paralleling board — a Digitrip trip unit with
            # energy metering (~class 1, ±0.5 %). Reports per-phase V/I, imbalance, PF,
            # kW/kVAR/kVA and kWh, but NOT the revenue-grade PQ (THD, peak demand) of
            # the ION9000 upstream — that is the distinction between a trip-unit meter
            # and a revenue/PQ meter.
            if not live:
                for _k in ("swgr_voltage", "swgr_current", "swgr_kw", "swgr_load_pct",
                           "swgr_va", "swgr_vb", "swgr_vc", "swgr_ia", "swgr_ib",
                           "swgr_ic", "swgr_phase_imbalance", "swgr_frequency",
                           "swgr_kvar", "swgr_kva", "swgr_power_factor"):
                    st[_k] = 0.0
            else:
                def _m(x: float) -> float:            # class-1 trip-unit metering error
                    return x * (1.0 + random.uniform(-0.005, 0.005))

                lf = load_pct / 100.0
                # PFC-corrected load — PF climbs with load (same load model as utility).
                pf = round(min(0.995, 0.955 + 0.035 * lf + random.uniform(-0.005, 0.005)), 3)
                # LV bus voltage droops with load (~2.5 % at full). System V is L-L;
                # per-phase reads L-N (= V_LL / √3).
                v_ll_true = 400.0 * (1.0 - 0.025 * lf)
                v_ln = v_ll_true / 1.7320508
                v_ph = [_m(v_ln * (1.0 + random.uniform(-0.006, 0.006))) for _ in range(3)]
                i_avg = thr / (3.0 * v_ln * pf) if (v_ln > 0 and pf > 0) else 0.0
                i_ph = [_m(i_avg * (1.0 + random.uniform(-0.02, 0.02))) for _ in range(3)]
                i_mean = sum(i_ph) / 3.0
                imbal = (max(abs(x - i_mean) for x in i_ph) / i_mean * 100.0) if i_mean > 0 else 0.0
                kw = round(_m(thr / 1000.0), 1)
                kva = round(kw / pf, 1) if pf > 0 else 0.0
                kvar = round((kva * kva - kw * kw) ** 0.5, 1) if kva >= kw else 0.0
                # Frequency: the grid on the utility board; the genset governor (less
                # stiff than the grid) on the paralleling board.
                freq = (round(random.uniform(49.8, 50.2), 2) if gen_side
                        else round(self._grid_frequency(
                            getattr(device, "datacenter_city", None) or dc), 2))
                st["swgr_voltage"] = round(_m(v_ll_true), 1)     # L-L system voltage
                st["swgr_va"] = round(v_ph[0], 1)
                st["swgr_vb"] = round(v_ph[1], 1)
                st["swgr_vc"] = round(v_ph[2], 1)
                st["swgr_ia"] = round(i_ph[0], 1)
                st["swgr_ib"] = round(i_ph[1], 1)
                st["swgr_ic"] = round(i_ph[2], 1)
                st["swgr_current"] = round(i_mean, 1)            # avg line current
                st["swgr_phase_imbalance"] = round(imbal, 1)
                st["swgr_frequency"] = freq
                st["swgr_kw"] = kw
                st["swgr_kvar"] = kvar
                st["swgr_kva"] = kva
                st["swgr_power_factor"] = pf
                st["swgr_load_pct"] = load_pct
            # Cumulative energy accrues only while energized (swgr_kw is 0 when dead).
            st["swgr_energy_kwh"] = round(
                st.get("swgr_energy_kwh", 0.0) + st.get("swgr_kw", 0.0) / 60.0, 3)

        elif dtv == "ats":
            failed = device.id in self._ats_failed
            pos = "none" if failed else ats.source
            prev = st.get("ats_position", "normal")
            if pos != prev and pos in ("normal", "emergency"):
                st["ats_transfer_count"] = int(st.get("ats_transfer_count", 0)) + 1
            # Autonomous ATS event notifications (ASCO 7000 ACC / Eaton ATC-900).
            # A transfer switch natively speaks SNMP and emits these itself as its
            # source and position change — no operator action. Each edge fires once;
            # prev-state flags live in this device's own ext_state. A failed switch
            # in a 2N plant reports nothing (its ACC card is dead with its side).
            _cb = self._transfer_trap_cb
            if _cb is not None and not failed:
                _norm_ok = bool(normal_ok)
                if st.get("_ats_norm_ok", True) and not _norm_ok:
                    _cb(device, "source_lost")          # normal (utility) source lost
                st["_ats_norm_ok"] = _norm_ok
                _estart = (not _norm_ok) and ats.gen_status in ("cranking", "running")
                if _estart and not st.get("_ats_estart", False):
                    _cb(device, "engine_start")         # engine-start contact asserted
                st["_ats_estart"] = _estart
                # A retransfer is an OPEN transition (dead-bus): pos steps
                # emergency → none → normal, so the landing edge's prev is "none",
                # not "emergency". Latch that we transferred to the genset and clear
                # it only once we're back on normal, so the retransfer trap fires
                # regardless of the dead-bus step in between.
                if pos != prev:
                    if pos == "emergency":
                        _cb(device, "transfer_emergency")   # load → generator
                        st["_ats_on_emg"] = True
                    elif pos == "normal" and st.get("_ats_on_emg", False):
                        _cb(device, "transfer_normal")      # retransfer → utility
                        st["_ats_on_emg"] = False
            live_ats = ats.source_live and not failed
            st["ats_position"] = pos
            st["ats_state"] = "failed" if failed else ats.state
            st["ats_normal_available"] = "yes" if (normal_ok and not failed) else "no"
            st["ats_emergency_available"] = "yes" if (ats.gen_at_voltage and not failed) else "no"
            # ASCO 7000 (ACC SNMP): source voltages + per-source frequency, position,
            # transfer count and time-on-emergency. It is a SWITCH — it does NOT meter
            # kW/load%. The two sources it senses are the utility main switchgear
            # (normal) and the generator paralleling board (emergency), so read those
            # boards' live bus V/Hz directly: the ATS reports the SAME numbers as the
            # switchgear it is wired to, not an independent random reading. (Switchgear
            # is stepped before the ATS each tick; a cold start falls back to nominal.)
            norm_v = norm_hz = emer_v = emer_hz = 0.0
            for _pid in ctx.get("parents", {}).get(device.id, []):
                _pd = self._dm.get_device(_pid)
                if _pd is None or _pd.device_type != DeviceType.SWITCHGEAR:
                    continue
                _pst = _ext_state_cache.get(_pd.name, {})
                if _pst.get("swgr_source") == "generator":
                    emer_v = float(_pst.get("swgr_voltage", 0.0) or 0.0)
                    emer_hz = float(_pst.get("swgr_frequency", 0.0) or 0.0)
                else:
                    norm_v = float(_pst.get("swgr_voltage", 0.0) or 0.0)
                    norm_hz = float(_pst.get("swgr_frequency", 0.0) or 0.0)
            _normal_sensed = normal_ok and not failed
            emerg_ok = ats.gen_at_voltage and not failed
            st["ats_normal_voltage"]      = round(norm_v if norm_v > 0 else 400.0, 1) if _normal_sensed else 0.0
            st["ats_emergency_voltage"]   = round(emer_v if emer_v > 0 else 400.0, 1) if emerg_ok else 0.0
            st["ats_normal_frequency"]    = round(norm_hz if norm_hz > 0 else 50.0, 2) if _normal_sensed else 0.0
            st["ats_emergency_frequency"] = round(emer_hz if emer_hz > 0 else 50.0, 2) if emerg_ok else 0.0
            # Frequency of the source currently connected to the load.
            if not live_ats:
                st["ats_frequency"] = 0.0
            elif pos == "emergency":
                st["ats_frequency"] = st["ats_emergency_frequency"]
            else:
                st["ats_frequency"] = st["ats_normal_frequency"]
            # minutes the load has been on the emergency (generator) source
            on_emg = pos == "emergency" and not failed
            st["ats_time_on_emergency"] = round(
                (st.get("ats_time_on_emergency", 0.0) + 1.0 / 60.0) if on_emg else 0.0, 2)
            # Latching condition points (ASCO ACC discrete inputs): 1 = asserted.
            # not-in-auto is annunciation only; fail-to-transfer is the failed state.
            st["ats_not_in_auto"] = 1.0 if device.id in self._ats_not_in_auto else 0.0
            st["ats_fail_to_transfer"] = 1.0 if failed else 0.0
            st["ats_in_auto"] = 0.0 if device.id in self._ats_not_in_auto else 1.0

        elif dtv == "mpp":
            # A hall's mechanical panelboard with a panel-main meter (Schneider
            # PowerLogic PM5000): per-phase V/I, imbalance, PF, kW/kVAR/kVA, Hz, kWh.
            # Passive — live exactly when the MCC feeding it is live. Its CRAHs are
            # mechanical load block 3, so it re-energizes last on a genset restart. It
            # feeds EC/VFD CRAH fans: good PF, modest imbalance.
            live = bool(self._energized.get(device.id, False))
            st["mpp_status"] = "energized" if live else "dead"
            if not live:
                for _k in ("mpp_voltage", "mpp_current", "mpp_kw", "mpp_load_pct",
                           "mpp_va", "mpp_vb", "mpp_vc", "mpp_ia", "mpp_ib", "mpp_ic",
                           "mpp_phase_imbalance", "mpp_frequency", "mpp_kvar",
                           "mpp_kva", "mpp_power_factor"):
                    st[_k] = 0.0
            else:
                def _m(x: float) -> float:            # class-0.5 panel-meter error
                    return x * (1.0 + random.uniform(-0.005, 0.005))

                lf = load_pct / 100.0
                # EC/VFD CRAH fans — better PF than uncorrected motors, climbing with load.
                pf = round(min(0.96, 0.88 + 0.06 * lf + random.uniform(-0.008, 0.008)), 3)
                v_ll_true = 400.0 * (1.0 - 0.02 * lf)
                v_ln = v_ll_true / 1.7320508
                v_ph = [_m(v_ln * (1.0 + random.uniform(-0.006, 0.006))) for _ in range(3)]
                i_avg = thr / (3.0 * v_ln * pf) if (v_ln > 0 and pf > 0) else 0.0
                i_ph = [_m(i_avg * (1.0 + random.uniform(-0.025, 0.025))) for _ in range(3)]
                i_mean = sum(i_ph) / 3.0
                imbal = (max(abs(x - i_mean) for x in i_ph) / i_mean * 100.0) if i_mean > 0 else 0.0
                kw = round(_m(thr / 1000.0), 1)
                kva = round(kw / pf, 1) if pf > 0 else 0.0
                kvar = round((kva * kva - kw * kw) ** 0.5, 1) if kva >= kw else 0.0
                # Frequency: grid on utility; genset governor when the DC is on emergency.
                freq = (round(random.uniform(49.8, 50.2), 2) if ats.source == "emergency"
                        else round(self._grid_frequency(
                            getattr(device, "datacenter_city", None) or dc), 2))
                st["mpp_voltage"] = round(_m(v_ll_true), 1)      # L-L system voltage
                st["mpp_va"] = round(v_ph[0], 1)
                st["mpp_vb"] = round(v_ph[1], 1)
                st["mpp_vc"] = round(v_ph[2], 1)
                st["mpp_ia"] = round(i_ph[0], 1)
                st["mpp_ib"] = round(i_ph[1], 1)
                st["mpp_ic"] = round(i_ph[2], 1)
                st["mpp_current"] = round(i_mean, 1)             # avg line current
                st["mpp_phase_imbalance"] = round(imbal, 1)
                st["mpp_frequency"] = freq
                st["mpp_kw"] = kw
                st["mpp_kvar"] = kvar
                st["mpp_kva"] = kva
                st["mpp_power_factor"] = pf
                st["mpp_load_pct"] = load_pct
            # Cumulative mechanical energy accrues only while energized.
            st["mpp_energy_kwh"] = round(
                st.get("mpp_energy_kwh", 0.0) + st.get("mpp_kw", 0.0) / 60.0, 3)

        elif dtv == "mcc":
            src_ok, tie_closed = self._mcc_tie_state(dc, ctx)
            own_ok = ctx.get("mcc_ats", {}).get(device.id) not in self._ats_failed
            dead = not src_ok.get(device.id, True)
            live = ats.source_live and not dead
            st["mcc_status"] = "energized" if live else "dead"
            st["mcc_tie"] = "closed" if tie_closed else "open"
            # Which source is actually feeding this bus: its own transfer switch, or
            # the sibling's across a closed tie.
            st["mcc_source"] = "normal" if own_ok else ("tie" if tie_closed else "none")
            # Eaton Freedom 2100 with a metered main (Power Xpert/IQ) — per-phase V/I,
            # imbalance, motor PF, kW/kVAR/kVA, Hz and kWh. This bus carries the
            # cooling-plant MOTOR load, so PF is lower than IT and drops at part load,
            # imbalance runs higher (one large motor skews a phase), and kVAR is
            # significant.
            if not live:
                for _k in ("mcc_voltage", "mcc_current", "mcc_kw", "mcc_load_pct",
                           "mcc_va", "mcc_vb", "mcc_vc", "mcc_ia", "mcc_ib", "mcc_ic",
                           "mcc_phase_imbalance", "mcc_frequency", "mcc_kvar",
                           "mcc_kva", "mcc_power_factor"):
                    st[_k] = 0.0
            else:
                def _m(x: float) -> float:            # class-0.5 metered-main error
                    return x * (1.0 + random.uniform(-0.005, 0.005))

                lf = load_pct / 100.0
                # Motor/VFD load: PF lower than IT and worse at part load (a lightly
                # loaded motor is highly reactive), climbing toward ~0.90 near full.
                pf = round(min(0.90, 0.78 + 0.12 * lf + random.uniform(-0.01, 0.01)), 3)
                # LV motor bus droops more than the IT bus (~3 % at full load).
                v_ll_true = 400.0 * (1.0 - 0.03 * lf)
                v_ln = v_ll_true / 1.7320508
                # Motor buses are less balanced than IT — one large motor skews a phase.
                v_ph = [_m(v_ln * (1.0 + random.uniform(-0.008, 0.008))) for _ in range(3)]
                i_avg = thr / (3.0 * v_ln * pf) if (v_ln > 0 and pf > 0) else 0.0
                i_ph = [_m(i_avg * (1.0 + random.uniform(-0.035, 0.035))) for _ in range(3)]
                i_mean = sum(i_ph) / 3.0
                imbal = (max(abs(x - i_mean) for x in i_ph) / i_mean * 100.0) if i_mean > 0 else 0.0
                kw = round(_m(thr / 1000.0), 1)
                kva = round(kw / pf, 1) if pf > 0 else 0.0
                kvar = round((kva * kva - kw * kw) ** 0.5, 1) if kva >= kw else 0.0
                # Frequency: grid on utility; genset governor when the DC is on emergency.
                freq = (round(random.uniform(49.8, 50.2), 2) if ats.source == "emergency"
                        else round(self._grid_frequency(
                            getattr(device, "datacenter_city", None) or dc), 2))
                st["mcc_voltage"] = round(_m(v_ll_true), 1)      # L-L system voltage
                st["mcc_va"] = round(v_ph[0], 1)
                st["mcc_vb"] = round(v_ph[1], 1)
                st["mcc_vc"] = round(v_ph[2], 1)
                st["mcc_ia"] = round(i_ph[0], 1)
                st["mcc_ib"] = round(i_ph[1], 1)
                st["mcc_ic"] = round(i_ph[2], 1)
                st["mcc_current"] = round(i_mean, 1)             # avg line current
                st["mcc_phase_imbalance"] = round(imbal, 1)
                st["mcc_frequency"] = freq
                st["mcc_kw"] = kw
                st["mcc_kvar"] = kvar
                st["mcc_kva"] = kva
                st["mcc_power_factor"] = pf
                st["mcc_load_pct"] = load_pct
            # Cumulative mechanical energy accrues only while energized.
            st["mcc_energy_kwh"] = round(
                st.get("mcc_energy_kwh", 0.0) + st.get("mcc_kw", 0.0) / 60.0, 3)

        _ext_state_cache[device.name] = dict(st)

    def _step_generator(self, device: "Device", st: dict) -> None:
        """Live generator state, slaved to its DC's transfer sequence.

        A genset sits in STANDBY until the ATS closes its start contact. It then
        cranks, reaches rated voltage, and once the ATS transfers it carries the
        live downstream load (server→…→generator through). Fuel burn is
        proportional to load and sets the remaining runtime. After the utility
        returns the engine keeps turning through a COOLDOWN run at no load.

        Both gensets close onto a common paralleling bus and start together, so the
        site load divides evenly across them and each reads about half the facility
        kW. Either machine is rated to carry the whole site on its own."""
        ctx = self._power_context() or {}
        dc = getattr(device, "datacenter", None) or "?"
        ats = self._transfer.status(dc)
        rated = ctx.get("rated_w", {}).get(device.id, 0.0)
        thr   = self._through_live.get(device.id, 0.0)
        dt_h  = self._dt / 3600.0
        out_of_fuel = st.get("gen_fuel_pct", 100.0) <= 0.5
        # This engine cannot be online — a fail-to-start, a flat starting battery, or
        # an engine-protection trip (low coolant / over-temp) that shuts a running set
        # down and locks out a start. A healthy SIBLING can still qualify the emergency
        # source, but THIS set reports its own fault, not the shared bus status.
        failed = self._gen_offline(device.id)

        if out_of_fuel or failed:
            st["gen_was_running"] = False
            st["gen_status"] = "fault"
            st["gen_load_pct"] = 0.0
            st["gen_kw"] = 0.0
            st["gen_runtime_min"] = 0.0
        elif ats.gen_demand:
            if not st.get("gen_was_running"):
                st["gen_start_attempts"] = int(st.get("gen_start_attempts", 0)) + 1
                st["gen_was_running"] = True
            st["gen_status"] = ats.gen_status
            st["gen_run_hours"] = round(st.get("gen_run_hours", 0.0) + dt_h, 3)
            # Load only once the ATS has actually closed onto the emergency source.
            # While cranking, or during the open-transition dead time, or on a
            # post-retransfer cooldown run, the machine turns at no load.
            carrying = ats.source == "emergency"
            load_pct = (thr / rated * 100.0) if (carrying and rated > 0) else 0.0
            st["gen_load_pct"] = round(max(0.0, min(100.0, load_pct)), 1)
            st["gen_kw"]       = round(thr / 1000.0, 1) if carrying else 0.0
            # Fuel burn ∝ load; a full tank lasts _GEN_FULL_HOURS at full load. An
            # unloaded engine still burns roughly a tenth of its full-load rate.
            lf_burn = max(0.10, st["gen_load_pct"] / 100.0)
            burn = lf_burn * dt_h / self._GEN_FULL_HOURS * 100.0
            st["gen_fuel_pct"] = round(max(0.0, st.get("gen_fuel_pct", 0.0) - burn), 2)
            lf = max(0.05, st["gen_load_pct"] / 100.0)
            st["gen_runtime_min"] = round(st["gen_fuel_pct"] / 100.0
                                          * self._GEN_FULL_HOURS / lf * 60.0, 1)
        else:
            st["gen_was_running"] = False
            st["gen_status"] = "standby"
            st["gen_load_pct"] = 0.0
            st["gen_kw"] = 0.0
            st["gen_runtime_min"] = 0.0

        # Injected alarm conditions → discrete controller alarm points (annunciation).
        _gc = self._gen_conditions.get(device.id, set())
        st["gen_alarm_low_fuel"]    = 1.0 if "low_fuel" in _gc else 0.0
        st["gen_alarm_low_coolant"] = 1.0 if "low_coolant" in _gc else 0.0
        st["gen_battery_status"]    = "failure" if "battery_failure" in _gc else "ok"
        st["gen_alarm_transfer"]    = 1.0 if "transfer_switch" in _gc else 0.0
        st["gen_alarm_temp"]        = 1.0 if "over_temp" in _gc else 0.0
        if "low_fuel" in _gc:       # gauge sits at the low-fuel alarm level, still running
            st["gen_fuel_pct"] = min(st.get("gen_fuel_pct", 100.0), 8.0)

        # Autonomous running/stopped notifications: the genset emits these itself as it
        # starts on the ATS start-contact and stops after the utility returns. Fired on
        # the edge into/out of "running", once each.
        _cb = self._transfer_trap_cb
        if _cb is not None:
            _now = st["gen_status"]
            _prev = st.get("_gen_run_prev", "standby")
            if _now != _prev:
                if _now == "running":
                    _cb(device, "gen_running")
                elif _prev == "running":
                    _cb(device, "gen_stopped")
            st["_gen_run_prev"] = _now

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

    def _dc_of(self, nid: str) -> str:
        d = self._dm.get_device(nid)
        return (getattr(d, "datacenter", None) or "?") if d else "?"

    def _compute_energized(self, ctx: dict) -> Dict[str, bool]:
        """Which power nodes are delivering power downstream right now.

        Walked top-down by rank, because energization flows from the sources:

          utility feed — live while the utility is up
          generator    — live once the gensets reach rated voltage
          ATS          — live while it is closed onto a source and not failed
          UPS          — live even with a dead input: that is what a UPS is for. It
                         goes dark only once its battery is exhausted.
          everything else — live if any feed above it is live

        A node with no power parents at all (a router, a mgmt-only sensor) is left
        live, so this never zeroes something that was simply never wired into the
        power graph.
        """
        rank = ctx.get("rank", {})
        parents = ctx.get("parents", {})
        id_type = ctx.get("id_type", {})
        ats_src_swgr = ctx.get("ats_src_swgr", {})
        # Broken power feeders, read LIVE this tick: a cable break flips the edge's
        # `broken` flag without changing the ctx cache signature (the edge count is
        # unchanged), so energization must consult the topology directly, not ctx.
        broken: Set[frozenset] = set()
        topo = self._topology
        if topo is not None:
            try:
                for u, v, w in topo.get_edges_by_layer("power"):
                    if w.get("broken"):
                        broken.add(frozenset((u, v)))
            except Exception:
                pass
        self._broken_power = broken

        def _intact(nid):
            return [p for p in parents.get(nid, [])
                    if frozenset((nid, p)) not in broken]

        en: Dict[str, bool] = {}
        input_live: Dict[str, bool] = {}
        for nid in sorted(rank, key=lambda x: rank.get(x, self._LEAF_RANK)):
            dtv = id_type.get(nid)
            if dtv == "utility_feed":
                en[nid] = not self._utility_failed.get(self._dc_of(nid), False)
            elif dtv == "generator":
                en[nid] = self._transfer.status(self._dc_of(nid)).gen_at_voltage
            elif dtv == "switchgear":
                # Live if it has an intact feeder to a live source (utility board ←
                # utility feed, paralleling board ← generators) AND its own main breaker
                # hasn't tripped / bus isn't faulted. A trip/fault takes the board dead,
                # so its ATS loses that source and the UPS below drops to battery.
                full = parents.get(nid, [])
                _src_live = (any(en.get(p, False) for p in _intact(nid))
                             if full else True)
                en[nid] = _src_live and not self._swgr_conditions.get(nid)
            elif dtv in ("pdu", "floor_pdu"):
                # A tripped PDU breaker takes the strip dead: live only if a feed
                # above it is live AND its own breaker is closed. Without this the
                # trip was pure annunciation — status point flips, trap fires, and
                # every server corded to it carries on drawing from a dead strip.
                # Dual-corded loads still ride it on their other PSU, which is the
                # behaviour worth rehearsing; single-corded ones drop.
                full = parents.get(nid, [])
                _src_live = (any(en.get(p, False) for p in _intact(nid))
                             if full else True)
                en[nid] = _src_live and not self._pdu_conditions.get(nid)
            elif dtv == "ats":
                # Live only if it is not failed, the transfer sequence has it closed
                # onto a source, AND that source's switchgear is reachable through an
                # INTACT feeder and is itself energized. The last clause is what makes
                # a switchgear↔ATS (or any upstream) cable break drop the ATS output —
                # and so its UPS to battery — even with the source perfectly healthy.
                stt = self._transfer.status(self._dc_of(nid))
                sel = ats_src_swgr.get(nid, {}).get(stt.source)
                # Additive gate only: when the selected board IS identified, require an
                # intact feeder to a live board. When it is not (unmapped ATS, or the
                # dead-bus "none" source), fall back to the transfer verdict so this can
                # never manufacture a false blackout on gear it doesn't understand.
                src_reachable = (True if sel is None else
                                 (frozenset((nid, sel)) not in broken
                                  and en.get(sel, False)))
                en[nid] = (nid not in self._ats_failed
                           and stt.source_live and src_reachable)
            elif dtv == "ups":
                # Rectifier input = an intact feeder to a live ATS. This drives the
                # battery drain (see _ups_source_ok). The UPS still DELIVERS downstream
                # on battery until the string is exhausted, so its own energized state
                # is the battery state, not the input.
                input_live[nid] = any(en.get(p, False) for p in _intact(nid))
                d = self._dm.get_device(nid)
                ext = (self._ext_states.get(d.name) if d else None) or {}
                en[nid] = not ext.get("ups_battery_exhausted", False)
            elif dtv == "mcc":
                # Bus tie: an MCC whose own transfer switch failed is picked up by its
                # sibling's source. It still needs SOME source to be live, so the
                # parent check below still applies through the sibling's ATS.
                dc = self._dc_of(nid)
                src_ok, _tie = self._mcc_tie_state(dc, ctx)
                live_ats = any(en.get(a, False) for a in ctx.get("dc_ats", {}).get(dc, []))
                en[nid] = bool(src_ok.get(nid, True)) and live_ats
            else:
                # A node with power parents is live iff at least one INTACT feeder
                # reaches a live parent; break every feeder and it goes dark. A node
                # with no power parents at all (never wired in) is left live so this
                # never zeroes gear that simply isn't in the power graph.
                full = parents.get(nid, [])
                en[nid] = (any(en.get(p, False) for p in _intact(nid))
                           if full else True)
        self._ups_input_live = input_live
        return en

    def _active_parents(self, nid: str, ctx: dict) -> list:
        """The upstream nodes actually carrying this node's watts right now.

        Normally that is every power parent — a dual-corded server really does draw
        from both its PDUs, so each side sees half. Three rules bend that:

          • an ATS draws from exactly ONE source. Splitting its load across the
            utility board and the generator board would show a standby genset
            carrying half the datacenter.
          • a UPS whose input source is dead is running from its batteries, so its
            load stops there and never reaches the ATS above it. Returning []
            simply ends the cascade at that node, which is what a battery does.
          • a DEAD cord carries nothing. When one side of a 2N feed drops, the
            surviving side picks up the whole load rather than politely continuing
            to take half of it — which is the entire reason a 2N plant is designed
            to sit below 50 % load in normal operation.
        """
        parents = ctx.get("parents", {})
        ps = parents.get(nid, [])
        if not ps:
            return ps
        dtv = ctx.get("id_type", {}).get(nid)
        _bp = self._broken_power
        if dtv == "ats":
            if nid in self._ats_failed:
                return []
            src = self._transfer.status(self._dc_of(nid)).source   # normal|emergency|none
            board = ctx.get("ats_src_swgr", {}).get(nid, {}).get(src)
            # An open feeder to the selected board carries nothing.
            return ([board] if board and frozenset((nid, board)) not in _bp else [])
        if dtv == "ups":
            d = self._dm.get_device(nid)
            if d is not None and not self._ups_source_ok(d):
                return []                     # running on battery — load stops here
            return [p for p in ps if frozenset((nid, p)) not in _bp]
        if dtv == "mcc":
            # With the bus tie closed, this MCC's watts come from a SIBLING's transfer
            # switch, not from its own dead one. Without this the mechanical load would
            # stop at the MCC and never reach the facility main meter.
            dc = self._dc_of(nid)
            own = ctx.get("mcc_ats", {}).get(nid)
            if own is not None and own not in self._ats_failed:
                return [p for p in ps if self._energized.get(p, True)]
            siblings = [ctx.get("mcc_ats", {}).get(m)
                        for m in ctx.get("dc_mcc", {}).get(dc, []) if m != nid]
            live = [a for a in siblings
                    if a and a not in self._ats_failed and self._energized.get(a, False)]
            return live[:1]
        return [p for p in ps if self._energized.get(p, True)
                and frozenset((nid, p)) not in _bp
                and frozenset((nid, p)) not in self._dead_cord_pairs]

    def _compute_power_flow(self) -> None:
        """Per tick: sum live IT load bottom-up through the power graph so each
        PDU/UPS/EV2 node carries the real watts flowing through it right now.
        Also totals IT vs facility (IT + cooling plant) draw for live PUE."""
        ctx = self._power_context()
        rank, parents = ctx["rank"], ctx["parents"]
        self._energized = self._compute_energized(ctx)
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
            # Per-HALL inlet tallies so each room's CRAHs ramp on ITS air temp (local
            # return-air control), not a DC-wide average — a hot hall speeds up its own
            # fans while a cool hall stays quiet.
            inlet_sum_room: Dict[tuple, float] = _dd(float)  # Σ inlet per (dc, room)
            inlet_n_room: Dict[tuple, int] = _dd(int)        # server count per (dc, room)
            # Σ server EXHAUST per hall — what the CRAH return-air sensor is looking
            # at once the hot aisle mixes. Air-side ΔT widens with load, so this is
            # how a growing fleet shows up on the return-air point.
            outlet_sum_room: Dict[tuple, float] = _dd(float)
            outlet_n_room: Dict[tuple, int] = _dd(int)
            # Σ live IT WATTS per hall. A CRAH's fan is sized to the heat in ITS room,
            # so this is the duty a room-level unit actually answers to — the DC-wide
            # electrical ratio it used before is a plant-level quantity that says
            # nothing about how hot this particular hall is.
            it_live_room: Dict[tuple, float] = _dd(float)
            crah_room: Dict[str, tuple] = {}                 # CRAH name → (dc, room)
            dc_city: Dict[str, str] = {}
            plant_dc: Dict[str, list] = _dd(list)       # DC → [(name, nameplate_w, type)]
            plant_model: Dict[str, str] = {}            # plant device name → SKU model
            # Per-server live + nominal draw by NAME, for per-CDU loop-heat coupling:
            # a CDU's duty tracks the live heat of the cold-plate servers on ITS loop.
            it_w_by_name: Dict[str, float] = {}
            it_nom_by_name: Dict[str, float] = {}
            cdu_loops = self._cdu_loop_servers()        # {cdu name: {server names}}
            for d in devices:
                dtv = d.device_type.value
                _dc = getattr(d, "datacenter", None) or "?"
                if _dc not in dc_city:
                    dc_city[_dc] = getattr(d, "datacenter_city", None)
                if dtv in self._IT_LEAF_TYPES:
                    # An outlet switched off kills its load exactly as a Redfish
                    # power-off does; both end at 0 W through the cord.
                    w = (0.0 if d.name in self._load_unpowered_names
                         else self._server_live_watts(d))
                    own[d.id] = w
                    it_w += w
                    it_live_dc[_dc] += w
                    it_w_by_name[d.name] = w
                    it_nom_by_name[d.name] = float(getattr(d, "power_draw_w", 0) or 0)
                    # Room key normalised the SAME way crah_room does it below — a
                    # server keyed on None and a CRAH keyed on "" are the same hall,
                    # and keying them apart would silently give every room zero heat.
                    it_live_room[(_dc, getattr(d, "room", "") or "")] += w
                    _inl = getattr(d, "inlet_temp", None)
                    if _inl is not None:
                        inlet_sum_dc[_dc] += float(_inl)
                        inlet_n_dc[_dc] += 1
                        _rk = (_dc, getattr(d, "room", "") or "")
                        inlet_sum_room[_rk] += float(_inl)
                        inlet_n_room[_rk] += 1
                        _out = getattr(d, "outlet_temp", None)
                        if _out is not None:
                            outlet_sum_room[_rk] += float(_out)
                            outlet_n_room[_rk] += 1
                elif dtv in self._COOLING_TYPES:
                    # Cooling plant is also an electrical load on the power graph,
                    # so a facility meter downstream reads IT + cooling → PUE > 1.
                    # A unit whose MCC is de-energized draws nothing, regardless of
                    # what its last BACnet telemetry said.
                    #
                    # TWO SOURCES, AND THE SECOND IS NOT OPTIONAL. _plant_watts reads
                    # the BACnet present-value cache, which _publish_plant_state fills
                    # only while a BACnet controller is registered — stop the BACnet
                    # simulator and it is empty for every unit. With no fallback the
                    # whole mechanical branch then contributed 0 W: four MCCs and eight
                    # MPPs published ENERGIZED, nominal voltage, ZERO current over
                    # SNMP, and PUE lost its numerator. That reading is worse than an
                    # error because it is a plausible one — a real MCC's trip unit is a
                    # CT on the bus and reads load whether or not the BMS integration
                    # is alive, so nothing downstream can tell the difference.
                    #
                    # The staged cooling model is the honest stand-in: it is computed
                    # every tick from live IT heat and weather whatever BACnet is
                    # doing, and it already carries the same state the meters would
                    # show — standby 0, unpowered 0, tripped at the auxiliary floor,
                    # normalised to the plant's staged demand. It lags by one tick
                    # (this pass publishes it below), which on a 1 s tick is invisible
                    # against plant time constants.
                    unpowered = d.name in self._plant_unpowered_names
                    w = 0.0 if unpowered else self._plant_watts(d.name)
                    if w <= 0.0 and not unpowered:
                        w = self._plant_power_by_name.get(d.name, 0.0) * 1000.0
                    if w > 0:
                        own[d.id] = w
                        cool_w += w
                    plant_dc[_dc].append(
                        (d.name, float(getattr(d, "power_draw_w", 0) or 0), dtv))
                    plant_model[d.name] = getattr(d, "model_name", "") or ""
                    if dtv == "crah":
                        crah_room[d.name] = (_dc, getattr(d, "room", "") or "")
            for nid in sorted(rank, key=lambda x: rank.get(x, self._LEAF_RANK),
                              reverse=True):
                thr = own.get(nid, 0.0) + incoming.get(nid, 0.0)
                through[nid] = thr
                ps = self._active_parents(nid, ctx)
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
            cool_ctx = self._cooling_context()
            from core.cooling_model import (
                cooling_electrical_w, crah_fan_speed_ratio, vfd_speed_frac,
                affinity_power_kw, chiller_electrical_w, CHILLER_PLF,
                stage_modules, installed_modules_for, PLANT_MODULE_KW,
                PUMP_MIN_SPEED, FAN_MIN_SPEED, OH_FLOOR, OH_VAR,
                tower_cell_demand, tower_cells_needed, tower_cells_running,
                tower_cell_speed_frac, tower_chiller_factor, rotation_rank)
            _oh_design = (OH_FLOOR + OH_VAR) or 0.47
            _VFD_FAN  = ("crah", "cooling_tower")   # centrifugal fans
            _VFD_PUMP = ("pump", "cdu")             # centrifugal pumps
            plant_power: Dict[str, float] = {}
            plant_cop: Dict[str, float] = {}
            plant_loadfrac: Dict[str, float] = {}   # {unit_name: its DC's plant duty}
            # {unit_name: its OWN VFD speed fraction}. Distinct from loadfrac: duty is
            # what the loop asks for, speed is what the drive actually runs at after
            # the turndown floor. Published speed used to be back-derived from the
            # normalised electrical share instead, which made it an artifact of how
            # many units were staged rather than a property of the machine — stage a
            # second train and the same plant total splits two ways, so each pump's
            # apparent speed HALVED while its flow stayed put. That reported 24.6 %
            # on a pump moving 4.4x the water of one reading 29.1 %, below the 35 %
            # drive floor a running pump cannot be under.
            plant_speed: Dict[str, float] = {}
            self._plant_standby_names = set()
            _cool_model_w = 0.0
            _cool_model_by_dc: Dict[str, float] = {}
            # Per-room mean server inlet — the air a CRAH's return sensor sees.
            # Kept for _compute_chw_loop so the return-air point tracks the hall
            # instead of walking on a clock.
            self._room_inlet_c = {rk: inlet_sum_room[rk] / n
                                  for rk, n in inlet_n_room.items() if n}
            self._room_outlet_c = {rk: outlet_sum_room[rk] / n
                                   for rk, n in outlet_n_room.items() if n}
            for _dc, units in plant_dc.items():
                itl = it_live_dc.get(_dc, 0.0)          # live IT heat (W)
                self._it_live_by_dc[_dc] = itl
                np_sum = sum(w for _n, w, _t in units) or 1.0
                # ── STAGING: install for the fleet cap, sequence modules on with
                # live load. it_design tracks the ENABLED (running) capacity, so the
                # fixed cooling floor scales with the plant that's actually on — the
                # part-load overhead follows load and PUE holds ~1.5 instead of
                # collapsing (fleet overload → fake-low PUE) or spiking (full plant
                # floor at light load). See core/cooling_model.stage_modules.
                inst_mods = self._installed_modules(_dc)
                # ANTI-SHORT-CYCLE TIMERS: a stage change is a compressor start/stop,
                # not a spreadsheet edit. Age both timers by the tick, let the sequence
                # decide, then zero whichever direction actually fired. Load hysteresis
                # alone would let a sawtooth IT load bounce a stage every tick.
                _t_up, _t_dn = self._plant_stage_since.get(_dc, (1e9, 1e9))
                _t_up += self._dt
                _t_dn += self._dt
                _prev_on = self._plant_stage_on.get(_dc, 1)
                on = stage_modules(itl / 1000.0, inst_mods, _prev_on,
                                   since_up_s=_t_up, since_down_s=_t_dn)
                if on > _prev_on:
                    _t_up = 0.0
                elif on < _prev_on:
                    _t_dn = 0.0
                self._plant_stage_since[_dc] = (_t_up, _t_dn)
                self._plant_stage_on[_dc] = on
                enabled_kw   = on * PLANT_MODULE_KW
                installed_kw = inst_mods * PLANT_MODULE_KW
                itd = enabled_kw * 1000.0               # W the enabled plant cools
                # Overload: live IT beyond the FULL installed plant — every module is
                # on and cooling still can't keep up (feeds the thermal backstop).
                self._plant_overload_kw[_dc] = max(0.0, itl / 1000.0 - installed_kw)
                # ── TOWER BANK — decoupled from train staging ────────────────────
                # Cells are header equipment: every healthy, energized cell runs, and
                # the required airflow is shared across all of them at low fan speed.
                # Cube-law fan power makes the surplus cells nearly free, and the extra
                # fill area buys colder condenser water (a compressor credit, applied to
                # the DC total below so it reaches PUE rather than being normalised away).
                _towers_all = [_n for _n, _w, _t in units if _t == "cooling_tower"]
                # AVAILABLE cells — healthy and energized. A cell this bank cycled off
                # last tick is stopped but perfectly available, so health is judged by
                # alarms only; _is_faulted would drop it for being off, shrinking the
                # bank to whatever is currently spinning and oscillating every tick.
                # SILENT STOP. Judging availability by alarms alone made a cell that
                # simply stopped INVISIBLE: stopping every fan in the bank changed
                # nothing at all — rejection stayed 1.00, the condenser never moved,
                # no high-pressure trip. That is F6's defect (a stop costs capacity
                # but announces nothing) surviving in the one place F6's fix
                # deliberately did not reach, because the bank is unstaged and
                # _is_faulted would drop every cycled-off cell.
                #
                # The discrimination the bank needs is not health, it is INTENT: a
                # cell the bank cycled off is in _plant_standby_names and is
                # perfectly available, while a cell the bank COMMANDED to run last
                # tick and which now reports its fan stopped has failed. One tick of
                # grace, so a cell promoted this tick — status 0 until it spins up —
                # is not dropped for existing.
                # The timer is cleared ONLY by the cell reporting that it is turning —
                # never by it leaving the commanded set. Clearing on demotion makes
                # the condition unreachable: with every fan stopped the bank promotes
                # the next cell, the previous one's timer resets, and it rotates
                # through the dead cells one tick each, forever. Measured exactly
                # that: CT1 → CT2 → CT1 …, each flagged for a single tick, rejection
                # never moving off 1.00. Same shape as the run-proof timers that
                # answered "healthy" through a total loss of chilled water.
                _prev_run = self._tower_running_now.get(_dc, set())
                for _tn in _towers_all:
                    if not self._run_status_off(_tn):
                        self._tower_silent_s.pop(_tn, None)     # it is turning
                    elif _tn in _prev_run or _tn in self._tower_silent_s:
                        self._tower_silent_s[_tn] = (
                            self._tower_silent_s.get(_tn, 0.0) + self._dt)
                _towers_ok = [_n for _n in _towers_all if self._tower_available(_n)]
                _duty_inst = (itl / 1000.0) / max(1e-6, installed_kw)
                _demand = tower_cell_demand(_duty_inst, len(_towers_all))
                _cells_need = tower_cells_needed(_duty_inst, len(_towers_all))
                # Below the fan turndown the bank CYCLES cells rather than idling them
                # all at min speed — running fewer cells at a controllable speed. The
                # cycled-off cells stay healthy and available, so they still count as
                # rejection capacity in _compute_cond_loop; only the approach credit
                # (and their fan draw) goes away.
                _cells_run = tower_cells_running(_demand, len(_towers_ok))
                # RUNTIME EQUALIZATION, bucketed. Sorting on raw run-hours would swap
                # the lead cell EVERY TICK — the running cell accrues hours instantly
                # and loses its place to an idle one — which is not rotation, it is
                # chatter, and it smears one cell's duty across the whole bank. Real
                # plants rotate the lead on a SCHEDULE (weekly is typical), so cells
                # are ranked by whole rotation periods of accrued runtime and, within
                # a period, an already-running cell outranks an idle one. The set then
                # holds still until a cell has genuinely run a period longer.
                # _prev_run is captured above, before the availability filter needs it.
                _towers_ok.sort(key=lambda n: (
                    *rotation_rank(self._tower_run_hours.get(n, 0.0),
                                   n in _prev_run, self._TOWER_ROTATE_H), n))
                _towers_run = _towers_ok[:_cells_run]
                self._tower_running_now[_dc] = set(_towers_run)
                _dt_h = self._dt / 3600.0
                for _tn in _towers_run:
                    self._tower_run_hours[_tn] = self._tower_run_hours.get(_tn, 0.0) + _dt_h
                self._tower_cells[_dc] = (_cells_need, _cells_run)
                # A CYCLED-OFF cell is staged off by the BMS — precisely what standby
                # means — so it belongs in the standby set even though the bank as a
                # whole is unstaged. Decoupling the towers removed the WRONG membership
                # (staged with a chiller); dropping them from the set entirely went too
                # far and left an idle cell advertising Fan_Status=1 with zero speed and
                # zero draw. Membership zeroes its points and fades it on the canvas,
                # exactly like a standby chiller. Rejection capability is unaffected:
                # _compute_cond_loop counts cells by health and power, not by staging.
                self._plant_standby_names |= (set(_towers_ok) - set(_towers_run))
                _cond_f = (tower_chiller_factor(_cells_need, _cells_run)
                           if _cells_run else 1.0)
                # The design ENVELOPE for this DC. No longer accumulated into the
                # cooling total — that is summed from the devices after the collapse
                # below. Still computed because `lf` is derived from it, and `lf` is
                # the fallback duty for a CRAH whose SKU carries no rated capacity
                # and a CDU with no mapped coolant loop.
                total_w = cooling_electrical_w(itl, itd, dc_city.get(_dc),
                                               cond_factor=_cond_f)
                # ── TRAIN STAGING ────────────────────────────────────────────────
                # The plant stages whole COOLING TRAINS (chiller + its evaporator
                # pump + its condenser pump), never individual devices: a chiller with
                # no pump moves no water. Running the "first N of each device type"
                # independently — as this used to — can leave a running chiller whose
                # pump is staged off, and made an electrical side-loss take out every
                # stage at once. Tower cells are deliberately NOT in this set: the
                # bank is unstaged header equipment (see the tower block above).
                #
                # Trains are ordered lead/lag by their fitness: healthy and energized
                # first. So when one MCC drops, the BMS runs the trains that still
                # have power, and only counts a shortfall if there are not enough of
                # them — which is exactly what an N+1 plant is supposed to do.
                _trains = cool_ctx["trains_by_dc"].get(_dc, [])
                _spare_chwp = cool_ctx["spare_chwp"].get(_dc, [])
                _n_run = 0
                # Capture the CURRENT lead set before clearing it — least-switching is
                # decided against what this store ran last tick, which is authoritative
                # and always available. Reading it back off the BACnet cache instead
                # made lead selection depend on the telemetry plane being up.
                _prev_lead = {tr.get("chiller")
                              for tr in self._plant_trains_run.get(_dc, [])
                              if tr.get("chiller")}
                # RUN-STATUS PROOF, accrued against last tick's COMMANDED set. Only
                # units the BMS actually asked to run can fail to start, so the timer
                # is driven from the previous run set rather than from every unit in
                # the plant — a standby is stopped on purpose and must never time out.
                self._accrue_run_proof(
                    (m for tr in self._plant_trains_run.get(_dc, [])
                     for m in tr["members"]),
                    scope={_n for _n, _w, _t in units})
                self._plant_trains_run[_dc] = []
                if _trains:
                    _n_run = max(1, min(len(_trains),
                                        math.ceil(on / inst_mods * len(_trains))))

                    def _fitness(i_tr):
                        i, tr = i_tr
                        dead = any(m in self._plant_unpowered_names for m in tr["members"])
                        # A latched HP trip is read straight from the lockout set, not
                        # via its alarm point — the trip is set later in the tick, so a
                        # point-only check could still rank a tripped chiller as lead and
                        # never fail over. Marking its train bad promotes a standby (N+1).
                        # HEALTH, not run-state: a standby train is stopped on purpose.
                        # Using _is_faulted here (which counts "stopped" as a fault)
                        # marked every standby train bad, so no idle train could ever be
                        # promoted and the lead never rotated.
                        # A silent stop is a failure too. Without the proof timer the
                        # only way to lose a machine was for it to complain: an ALARMED
                        # chiller was replaced within a tick and cost nothing, while the
                        # same machine merely STOPPED stayed lead and took the plant
                        # down — because the cooling-loss model counts a stopped unit as
                        # gone while this ranking judged it healthy. Two predicates, two
                        # different answers, and the run-status bit fell between them.
                        bad = any(m not in self._plant_unpowered_names
                                  and (self._is_alarmed(m)
                                       or m in self._chiller_hp_lockout
                                       or self._run_proof_failed(m))
                                  for m in tr["members"])
                        _ch = tr.get("chiller")
                        # LEAST-SWITCHING: a train already running outranks an equal idle
                        # one, so a recovered chiller does NOT displace the standby that
                        # took over — no pointless swap-back on reset.
                        #
                        # Sourced from THIS store's last-tick run set, not the BACnet
                        # present-value cache. That cache is empty whenever the BACnet
                        # controller is stopped (get_telemetry_snapshot returns [] when
                        # not running), which made every train read "not running" and
                        # killed the least-switching term — leaving raw run-hours to
                        # decide, and those flip every tick because the current lead is
                        # the only train accruing them. The result was a lead that
                        # round-robined each tick until BACnet came up and froze it on
                        # whichever train happened to hold it, so the boot-time lead
                        # varied run to run and, with BACnet off, never settled at all.
                        running = (_ch is not None
                                   and (_ch in _prev_lead
                                        or float(_plant_state_cache.get(_ch, {})
                                                 .get("Chiller_Running", 0.0)) >= 0.5))
                        # RUNTIME EQUALIZATION, BUCKETED. Ranking on raw hours would
                        # swap the lead every tick for the same reason the tower bank
                        # did. Whole rotation periods instead: inside a period the
                        # running train holds, and only a train that has run a full
                        # period more than an idle peer hands over. Chiller lead/lag is
                        # rotated on a schedule in real plants, not continuously.
                        rh = self._train_run_hours.get(_ch, 0.0) if _ch else 0.0
                        # ANTI-RECYCLE — a compressor that has just stopped CANNOT be
                        # started again yet. That is a property of the candidate, not
                        # a preference, so it sorts ABOVE health: with a flapping
                        # condition the alternative to a sick running machine is a
                        # machine whose anti-recycle timer has not expired, and a real
                        # plant keeps the sick one turning rather than short-cycling
                        # its peer. Putting this below `bad` made the term inert —
                        # an alternating fault simply ping-ponged through it.
                        #
                        # Only a train that HAS led and lost it carries the timer; a
                        # machine that has never run is startable immediately, so
                        # first-ever failover is not delayed by a cold-start timer.
                        recycle = (_ch is not None
                                   and _ch not in _prev_lead
                                   and self._train_idle_s.get(_ch, 1e9)
                                   < self._LEAD_MIN_OFF_S)
                        # MINIMUM RUN — a final tiebreak, BELOW rotation. Scheduled
                        # rotation is the one swap the plant is supposed to make, and
                        # it is already chatter-proof (a whole period of runtime has
                        # to separate the two machines), so the hold must not veto it:
                        # sorting this above rotation stalled the weekly handover for
                        # the length of the timer. Here it only decides between
                        # candidates that rotation itself has tied.
                        held = (_ch is not None and _ch in _prev_lead
                                and self._train_lead_s.get(_ch, 0.0)
                                < self._LEAD_MIN_RUN_S)
                        return (dead, recycle, bad,
                                *rotation_rank(rh, running, self._TRAIN_ROTATE_H),
                                not held, i)

                    _order = [i for i, _ in sorted(enumerate(_trains), key=_fitness)]
                    _run_idx = set(_order[:_n_run])
                    self._plant_trains_run[_dc] = [_trains[i] for i in _order[:_n_run]]
                    # Accrue lead run-hours for the trains actually carrying the load,
                    # so the equalization key reflects real duty.
                    _dt_h = self._dt / 3600.0
                    for tr in self._plant_trains_run[_dc]:
                        _ch = tr.get("chiller")
                        if _ch:
                            self._train_run_hours[_ch] = self._train_run_hours.get(_ch, 0.0) + _dt_h
                    for i, tr in enumerate(_trains):
                        if i not in _run_idx:
                            self._plant_standby_names |= set(tr["members"])
                    # Age the timers off the set just chosen. Lead time is CONTINUOUS
                    # — losing the lead zeroes it, so a train cannot bank minimum-run
                    # credit across a handover. The stopped timer starts the moment a
                    # machine gives the lead up, and a train that has NEVER led stays
                    # absent from it: no entry means startable, which is what a
                    # never-run machine is. Reading a default of "just stopped" would
                    # make every standby ineligible at boot and block first failover.
                    for i, tr in enumerate(_trains):
                        _ch = tr.get("chiller")
                        if not _ch:
                            continue
                        if i in _run_idx:
                            self._train_lead_s[_ch] = self._train_lead_s.get(_ch, 0.0) + self._dt
                            self._train_idle_s.pop(_ch, None)
                        else:
                            self._train_lead_s[_ch] = 0.0
                            if _ch in _prev_lead:
                                self._train_idle_s[_ch] = 0.0      # just handed over
                            elif _ch in self._train_idle_s:
                                self._train_idle_s[_ch] += self._dt
                # The header standby CHW pump only runs when a RUNNING train's own
                # evaporator pump is out; otherwise it idles as the N+1 spare.
                _need_spare = any(
                    tr["chwp"] and (tr["chwp"] in self._plant_unpowered_names
                                    or self._is_faulted(tr["chwp"]))
                    for tr in self._plant_trains_run.get(_dc, []))
                if _spare_chwp and not _need_spare:
                    self._plant_standby_names |= set(_spare_chwp)
                # Plant-wide duty fraction: how hard the RUNNING plant works vs the
                # nameplate of the units that are actually on. Standby trains carry no
                # duty, so counting their nameplate would make a lightly-loaded plant
                # look busier than it is. Drives the VFD speed of every pump/fan.
                _running_np = sum(w for _n, w, _t in units
                                  if _n not in self._plant_standby_names) or np_sum
                lf = min(1.0, total_w / _running_np)
                self._plant_duty[_dc] = lf
                # Cooling DEMAND fraction for this DC — drives modulating valve
                # position (a control valve opens toward 100 % as load rises). Keyed
                # per unit name so the BACnet controller can look it up like the power
                # map. A staged-off unit's valve is handled as "off" downstream.
                for _n2, _w2, _t2 in units:
                    plant_loadfrac[_n2] = lf
                # Thermal part-load ratio for the chiller kW/ton curve. This is the
                # chiller's OWN load ratio — live IT heat divided by the rated cooling
                # capacity of the chillers currently running — not the staged plant's
                # load ratio. Feeding the plant's ratio (≈0.8 by design) into a machine
                # that is really at 11 % load put it on the wrong point of its part-load
                # curve, reporting a flattering COP and an inflated compressor draw.
                # Falls back to the old proxy for a topology whose chiller SKU carries
                # no catalog capacity.
                _run_cap_w = sum(cooling_capacity_w(plant_model.get(tr["chiller"], ""))
                                 for tr in self._plant_trains_run.get(_dc, []))
                plr = ((itl / _run_cap_w) if _run_cap_w > 0
                       else ((itl / itd) if itd > 0 else 0.0))
                plr = max(0.0, min(1.0, plr))
                _city = dc_city.get(_dc)
                # Hall inlet/return air temp → CRAH fan SPEED ramp (more airflow when
                # hot). Per-hall (below): each CRAH ramps on ITS room's inlet temp; the
                # DC average is the fallback for a room with no servers yet. The cube-law
                # POWER cost is applied once, by affinity_power_kw below — no double-cube.
                avg_inlet = (inlet_sum_dc.get(_dc, 0.0) / inlet_n_dc[_dc]
                             if inlet_n_dc.get(_dc) else 24.0)
                for _n, w, _t in units:
                    if _t in _VFD_FAN or _t in _VFD_PUMP:
                        # Staged-off pump (sequenced down with its chiller): standby,
                        # ~0 draw. CRAHs, CDUs and tower cells are never in the standby
                        # set — they always run and just VFD-modulate below.
                        if _n in self._plant_standby_names:
                            plant_power[_n] = 0.0
                            continue
                        # VFD centrifugal pump/fan — affinity law P ∝ speed³. Speed
                        # tracks the thermal duty (flow ∝ speed), floored at the drive
                        # turndown; draw equals nameplate only at full speed, far less
                        # when throttled.
                        if _t == "crah":
                            # Per-hall control: this CRAH ramps on its OWN room's average
                            # inlet temp (local return-air sensor), so a hot hall speeds
                            # up its fans while a cool hall stays quiet. DC-average
                            # fallback for a room with no servers.
                            _rk = crah_room.get(_n)
                            _rn = inlet_n_room.get(_rk, 0)
                            _rinl = (inlet_sum_room[_rk] / _rn) if _rn else avg_inlet
                            # THERMAL duty, from this room's own heat against the
                            # cooling its CRAHs are rated for. It was `lf` — cooling
                            # ELECTRICAL over running plant nameplate — which is a
                            # plant-level ratio that says nothing about how hot this
                            # hall is: two rooms on one plant got identical fan duty
                            # however their loads differed. It also made the total
                            # circular, because lf comes from the top-down cooling
                            # figure that this fan's own draw is supposed to add up to.
                            #
                            # Falls back to lf where the CRAH SKU carries no catalog
                            # capacity, so a topology without rated air-side gear keeps
                            # its old behaviour rather than dividing by zero.
                            _room_cap_kw = sum(
                                cooling_capacity_w(plant_model.get(_c, "")) or 0.0
                                for _c, _k in crah_room.items() if _k == _rk) / 1000.0
                            if _room_cap_kw > 1e-6:
                                duty = (it_live_room.get(_rk, 0.0) / 1000.0
                                        / _room_cap_kw)
                            else:
                                duty = lf
                            duty *= crah_fan_speed_ratio(_rinl)
                        elif _t == "cdu":
                            # Per-loop control: a CDU's pump ramps on the LIVE heat of
                            # the cold-plate servers on ITS loop, not the DC-wide plant
                            # duty — flow tracks heat (fixed ΔT). Duty = live loop heat
                            # / the loop's full-load (nameplate) heat, so an idle GPU
                            # loop coasts and a busy one drives the pump toward full.
                            _members = cdu_loops.get(_n, ())
                            _live_hw = sum(it_w_by_name.get(s, 0.0) for s in _members)
                            _nom_hw = sum(it_nom_by_name.get(s, 0.0) for s in _members)
                            duty = (_live_hw / _nom_hw) if _nom_hw > 0 else lf
                            self._cdu_loop_heat_kw[_n] = _live_hw / 1000.0
                            plant_loadfrac[_n] = duty     # per-loop, not DC-wide lf
                        elif _t == "cooling_tower":
                            # Bank control: the airflow the load needs (duty × the
                            # cells it requires) is SHARED across every running cell,
                            # so each fan turns slower than it would carrying the duty
                            # alone. P ∝ speed³ per cell, so the whole bank at low
                            # speed costs (needed/running)² of the same rejection.
                            # Faulted, de-energized, or cycled-off cells move no air and
                            # draw nothing; the cells in _towers_run share the whole
                            # airflow demand between them.
                            spd = (tower_cell_speed_frac(_demand, _cells_run)
                                   if _n in _towers_run else 0.0)
                            plant_loadfrac[_n] = spd
                            plant_speed[_n] = spd
                            plant_power[_n] = affinity_power_kw(w, spd) / 1000.0 if spd else 0.0
                            continue
                        else:
                            # CHW/CW pumps. THERMAL duty, not `lf`: this is a
                            # variable-primary loop, so the pumps ride a differential-
                            # pressure setpoint as the two-way coil valves stroke, and
                            # what strokes those valves is the heat in the water.
                            #
                            # `lf` is cooling-ELECTRICAL over running nameplate, which
                            # is a staging artifact — bring a second train up and lf
                            # falls because the denominator grew, so the pumps slowed
                            # down at the exact moment the plant took on more load.
                            # The CHW publisher already derives header FLOW from the
                            # thermal duty; taking speed from a different duty put the
                            # two on the wire together, contradicting each other: a
                            # pump passing 8.0 l/s published the same 35 % as one
                            # passing 1.8 l/s. Flow ∝ speed on a fixed system curve,
                            # so they have to come from one number.
                            # Against INSTALLED capacity, not the staged subset.
                            # Staging is a capacity decision; the flow the coil valves
                            # demand does not shrink because another chiller came up,
                            # and the ΔP setpoint the pumps ride is held either way.
                            # Dividing by staged capacity also quantises the ratio on
                            # module boundaries, so it is not even monotonic in load:
                            # live, a DC carrying 2x the heat (chiller 38.9 vs 19.2 kW)
                            # and moving more water (5.3 vs 4.1 l/s) published the
                            # SLOWER pump (50.5 % vs 77.8 %) and burnt less doing it,
                            # because its load rounded up to more modules while the
                            # lighter DC's rounded down to fewer.
                            #
                            # _duty_inst is the same installed-capacity ratio the tower
                            # bank already stages on, so the two air/water sides of the
                            # plant now answer to one definition of duty.
                            #
                            # SCALED BY HOW MANY PUMPS ACTUALLY CARRY IT. _duty_inst is
                            # flow ÷ the whole installed plant's design flow, which is
                            # the right speed only if every installed pump is running.
                            # Stage two trains of three out and the survivors carry the
                            # entire header between them, so they must turn faster — a
                            # pump's speed is set by ITS share of the water, not the
                            # plant's average. Without this the loop published a pump at
                            # the 35 % floor passing 100 % of its design flow, which is
                            # off the end of its curve: the head model correctly derived
                            # zero differential pressure for an operating point that
                            # cannot exist.
                            _n_all = len(cool_ctx["trains_by_dc"].get(_dc, ())) or 1
                            _n_run = len(self._plant_trains_run.get(_dc, ())) or 1
                            duty = min(1.0, _duty_inst * (_n_all / _n_run))
                        _min = FAN_MIN_SPEED if _t in _VFD_FAN else PUMP_MIN_SPEED
                        spd  = vfd_speed_frac(duty, _min)
                        plant_speed[_n] = spd
                        tgt_w = affinity_power_kw(w, spd)
                    elif _t == "chiller":
                        # Staged-off chiller: standby, drawing ~0 (its load is carried
                        # by the running units). NOT a cooling loss — excluded from the
                        # thermal-penalty fault calc.
                        if _n in self._plant_standby_names:
                            plant_power[_n] = 0.0
                            continue
                        # Chiller — part-load kW/ton curve × ambient/condenser
                        # factor. Compressor power is U-shaped in efficiency, not
                        # linear: nameplate at design, cheaper mid-load, penalised at
                        # very low PLR (fixed losses dominate). COP is the inverse —
                        # peaks mid-load, droops with a hot condenser.
                        tgt_w = chiller_electrical_w(w, plr, _city, cond_factor=_cond_f)
                        # COP is NOT set here — see the derivation after the
                        # normalisation below. The raw curve value is computed
                        # against an un-normalised draw, and publishing it beside a
                        # normalised Active_Power made the pair describe a machine
                        # that does not exist.
                    else:
                        # Valve / other: its OWN nameplate, not a share of the plant
                        # total. A modulating actuator is a small fixed load — it
                        # holds position against spring and flow force and draws
                        # essentially its rating whenever it is energised, which has
                        # nothing to do with how hard the chillers are working.
                        # Scaling it by total_w also made the actuator a function of
                        # the very sum it contributes to, which is the top-down
                        # inversion in miniature.
                        tgt_w = max(0.0, w)
                    plant_power[_n] = tgt_w / 1000.0   # kW
                # NO RECONCILIATION. This DC's cooling total is whatever its plant
                # devices draw — the sum is an OUTPUT of the device physics, not a
                # target the devices are fitted to.
                #
                # It used to be the other way round: cooling_electrical_w() set the
                # magnitude top-down and every running unit was scaled so the DC
                # summed to it. That is backwards from how a real plant meters, and
                # it cost this codebase a long run of defects, because a calibration
                # constant that silently disagrees with the device curves cannot fail
                # a test — each side is internally consistent. OH_VAR implied a plant
                # COP of 3.1 while the chiller module rated 5.5, and only OH_VAR
                # reached PUE; per-chiller COP was then back-derived from the scaled
                # draw, so a machine published ~2.2 beside a curve saying 5.5 and
                # neither number was wrong. Later the same scaling made two identical
                # pumps at one speed draw differently.
                #
                # Every plant device now computes its own draw from its own physics:
                # VFD gear on the affinity law from its speed, chillers on the
                # part-load curve at their PLR and condenser conditions. Summing that
                # is the meter reading. cooling_electrical_w() survives as the design
                # ENVELOPE — it still anchors `lf` for the two fallback duties, and
                # it is the right thing to compare against when asking whether a
                # plant is behaving — but it no longer decides what anything draws.
                _dc_names = [_n2 for _n2, _w2, _t2 in units]
                # DELIVERED-CAPACITY COLLAPSE. Runs AFTER the normalisation above,
                # deliberately. A STAGED-OFF unit's load transfers to the units
                # still running — that is what sequencing means, and its zero is
                # already inside the sum, so the survivors absorb its share. A
                # FAILED unit's load transfers to NOBODY: the capacity is gone.
                # Normalising first and collapsing second is the whole distinction;
                # collapsing first would let the scale factor hand a dead machine's
                # share straight to its neighbours and hold the panel flat.
                #
                # Without this the metered branch never saw a plant failure at all.
                # plant_power was normalised to the UNSCALED demand every tick, so a
                # total loss of chilled water left the mechanical meters at full draw
                # while cooling_model_kw() correctly collapsed — and get_power_summary
                # takes max(metered, model), so the meters won and cooling moved the
                # WRONG WAY: 132.9 → 140.3 kW with PUE falling 1.672 → 1.651, because
                # the chassis-fan term was driving IT up underneath it. Fixing the
                # model term alone was never enough; the meters it is maxed against
                # had to follow the plant too.
                #
                # The evaporator flow interlock counts here too. lost_weight already
                # scores a shed chiller as delivering NOTHING, and shedding it is
                # exactly what the flow switch does — it stops the compressor. Left
                # out, a chiller shed on loss of evaporator flow kept full draw while
                # contributing no cooling, which is the F9 defect surviving in a path
                # the first fix missed. Found by re-running the live campaign: with
                # every CHW pump faulted, cooling ROSE 130.2 -> 133.6 kW.
                #
                # A tripped machine is not electrically dead, so the draw collapses to
                # an auxiliary floor rather than to zero. An UNPOWERED unit is the one
                # exception — its MCC really is dead. The surviving plant is untouched,
                # which is what makes the result a shape and not a scale: the CRAH fans
                # ramp UP into the hot hall on the same tick the compressors drop out,
                # exactly as a real mechanical panel reads it.
                for _x in _dc_names:
                    if _x not in plant_power or _x in self._plant_standby_names:
                        continue
                    if _x in self._plant_unpowered_names:
                        plant_power[_x] = 0.0
                    elif (self._is_faulted(_x) or self._run_unproven(_x)
                            or _x in self._chiller_hp_lockout
                            or _x in self._chw_flow_interlock):
                        plant_power[_x] *= self._PLANT_AUX_FRAC

                # THE DC's COOLING TOTAL — the sum of what its plant actually draws,
                # taken HERE because the collapse above has already dropped faulted,
                # shed and unpowered machines. That ordering is the point: a plant
                # losing its chillers now shows a FALLING cooling figure because the
                # compressors stopped drawing, not because a separate model term was
                # taught to collapse alongside the meters. The two used to be
                # independent, and keeping them agreeing through every failure mode
                # was a standing source of bugs — the live campaign found cooling
                # RISING 130.2 -> 133.6 kW with every CHW pump faulted.
                _dc_cool_w = sum(plant_power.get(_x, 0.0)
                                 for _x in _dc_names) * 1000.0
                _cool_model_w += _dc_cool_w
                _cool_model_by_dc[_dc] = (_cool_model_by_dc.get(_dc, 0.0)
                                          + _dc_cool_w)

                # ── Chiller COP ──────────────────────────────────────────────
                # DERIVED, and derived LAST: COP = the cooling this machine is
                # actually delivering ÷ the draw actually published for it.
                #
                # It used to be chiller_cop(plr) taken straight off the part-load
                # curve inside the loop above. That number is only true against the
                # curve's OWN draw — and the normalisation a few lines up rescales
                # every running unit's kW so the plant sum matches the staged demand
                # ("a chiller reads its real ~100 kW, not the tiny curated
                # nameplate"), while the collapse below it drops a shed machine to an
                # auxiliary floor. Neither touched the COP. The published pair was
                # therefore one normalised number beside one un-normalised one, and
                # their product was not a quantity of heat: a header carrying 113 kW
                # was reported by a chiller claiming COP 4.08 on a 50.27 kW draw —
                # 205 kW of cooling, 1.8x the load on the loop it serves.
                #
                # Both inputs here are post-normalisation and post-collapse, so the
                # identity holds by construction however the draw was scaled.
                #
                # SHARE, not total: chillers on a common header are sequenced to
                # equal part-load, so each carries the evaporator load in proportion
                # to its RATED capacity. Equal shares would be wrong the moment a
                # plant mixes machine sizes.
                #
                # A machine that is delivering nothing gets COP 0, not a curve value:
                # standby (sequenced off), unpowered, faulted, unproven after a
                # start, high-head locked out, or shed by the evaporator flow
                # interlock. The flow-interlock case is the one that bit before —
                # a shed chiller kept its draw AND a healthy-looking COP, which is
                # precisely the reading that hides a plant failure.
                _cop_cap: Dict[str, float] = {}
                for _n2, _w2, _t2 in units:
                    if _t2 != "chiller":
                        continue
                    if (_n2 in self._plant_standby_names
                            or _n2 in self._plant_unpowered_names
                            or self._is_faulted(_n2) or self._run_unproven(_n2)
                            or _n2 in self._chiller_hp_lockout
                            or _n2 in self._chw_flow_interlock):
                        plant_cop[_n2] = 0.0
                        continue
                    _cop_cap[_n2] = float(
                        cooling_capacity_w(plant_model.get(_n2, "")) or 0.0)
                _cap_sum = sum(_cop_cap.values())
                # Capped at what the surviving machines can actually produce. The
                # evaporator load is a DEMAND figure and does not shrink when a
                # chiller drops out — so without this cap, shedding half the plant
                # would hand the survivor the whole load and RAISE its COP, turning
                # a failure into a machine that reads more efficient than before.
                # Above the cap the loop is simply in deficit; the hall temperatures
                # are where that shows, not here.
                _deliver_kw = min(itl / 1000.0, _cap_sum / 1000.0) if _cap_sum > 0 \
                    else itl / 1000.0
                for _n2, _cap in _cop_cap.items():
                    _frac = ((_cap / _cap_sum) if _cap_sum > 0
                             else 1.0 / max(1, len(_cop_cap)))
                    _p_kw = plant_power.get(_n2, 0.0)
                    plant_cop[_n2] = (round(_deliver_kw * _frac / _p_kw, 2)
                                      if _p_kw > 1e-6 else 0.0)
            self._plant_power_by_name = plant_power
            self._plant_cop_by_name = plant_cop
            self._plant_loadfrac_by_name = plant_loadfrac
            self._plant_speed_by_name = plant_speed
            self._cool_model_w = _cool_model_w
            self._cool_model_w_by_dc = _cool_model_by_dc
        except Exception:
            log.exception("[StateStore] power flow error")
        self._through_live = through
        # Step each grid region's frequency once this tick, so every utility meter on
        # the same grid reads an identical value (a grid has one frequency).
        try:
            for _city in {c for c in dc_city.values() if c}:
                self._step_grid_freq(_city)
        except NameError:
            pass
        self._it_w = it_w
        self._facility_w = it_w + cool_w
        # Live downstream kW per EV2 meter IP, for the BACnet telemetry engines.
        self._ev2_live_kw = {ip: through.get(panel, 0.0) / 1000.0
                             for ip, panel in ctx.get("ev2_ip_panel", {}).items()}
        # Live kW per branch circuit (ordered), so each EV2 circuit meters the real
        # load of the PDU it clamps instead of a synthetic per-circuit random walk.
        # A None slot is a freed/spare CT channel (branch removed) — passed through
        # as None (not 0.0) so the engine can tell it apart from a real 0-load branch
        # and zero that channel's energy register.
        self._ev2_circuit_kw = {
            ip: [None if pid is None else through.get(pid, 0.0) / 1000.0
                 for pid in pids]
            for ip, pids in ctx.get("ev2_circuit_pdus", {}).items()}

    # Lead/lag rotation periods (hours of accrued runtime). Weekly is the common BMS
    # default for both chiller trains and heat-rejection cells.
    _TOWER_ROTATE_H = 168.0
    _TRAIN_ROTATE_H = 168.0

    # MINIMUM RUN / ANTI-RECYCLE on the lead train. Runtime equalization is already
    # bucketed (see rotation_rank) so it cannot chatter, but the health terms sort
    # ABOVE it and could flip the lead every tick — any condition that comes and goes
    # with the machine that has it produces a ping-pong, which is how the condenser
    # limit did it before that point was split out. This is the general guard: once a
    # train is lead it holds for _LEAD_MIN_RUN_S, and a demoted train is not eligible
    # again for _LEAD_MIN_OFF_S. Every chiller controller enforces the same thing —
    # a compressor anti-recycle timer capping starts at roughly 6/hour.
    #
    # A train that is DEAD (unpowered) or genuinely faulted outranks both timers, so
    # failover stays immediate; the hold only suppresses discretionary swaps.
    #
    # Real timers are 15–30 min. Compressed to 5 min for the same reason TDEN is
    # (core/power_transfer): a restore has to be observable end-to-end in a session.
    _LEAD_MIN_RUN_S = 300.0
    _LEAD_MIN_OFF_S = 300.0

    # The plant is installed for the fleet's ULTIMATE server cap and staged; sized
    # here so it never truly runs out of modules before the cap is hit.
    _PLANT_DESIGN_SERVER_CAP = 3000

    def _installed_modules(self, dc: str) -> int:
        """Installed cooling modules for datacenter *dc* — sized to the fleet's
        ultimate server cap split across the datacenters present. Oversizing the
        INSTALLED plant is harmless (staging only runs what the load needs); it just
        sets how far the fleet can grow before genuine overload."""
        if dc not in self._plant_installed_mods:
            from core.cooling_model import installed_modules_for
            try:
                n_dc = len({(getattr(d, "datacenter", None) or "")
                            for d in self._dm.get_all_devices()
                            if getattr(d, "datacenter", None)}) or 1
            except Exception:
                n_dc = 2
            self._plant_installed_mods[dc] = installed_modules_for(
                self._PLANT_DESIGN_SERVER_CAP / max(1, n_dc))
        return self._plant_installed_mods[dc]

    @staticmethod
    def _is_faulted(name: str) -> bool:
        """True if this plant device is in a cooling-loss state — either any
        Alarm_* point is set, or its running-status point reads 0 (unit stopped).
        A stopped unit is as much a loss of cooling as an alarm."""
        pv = _plant_state_cache.get(name)
        if not pv:
            return False
        for k, v in pv.items():
            if (k.startswith("Alarm_") and k not in _CAPACITY_ALARMS
                    and float(v) >= 0.5):
                return True
            if k in _RUNNING_POINTS and float(v) < 0.5:
                return True
        return False

    @staticmethod
    def _run_status_off(name: str) -> bool:
        """True if this unit publishes a running-status point and it reads 0.

        Absence is NOT a stop. A device whose BACnet points have not been seen yet
        — or a class that publishes no running binary at all — must read as
        'no evidence', or every such unit would fail its start proof on the first
        tick and the plant would stage itself to nothing on boot.
        """
        pv = _plant_state_cache.get(name)
        if not pv:
            return False
        return any(k in _RUNNING_POINTS and float(v) < 0.5 for k, v in pv.items())

    def _accrue_run_proof(self, commanded, scope=None) -> None:
        """Advance the failure-to-start timer for units commanded ON this tick.

        Counts only silent stops: a unit that is also ALARMED is already handled by
        the fitness ranking, and an UNPOWERED one by the dead check, so neither
        needs a dwell. Anything running, or not commanded, resets to zero — the
        timer measures a continuous failure to prove run status, not a tally.

        *scope* is the set of unit names this pass is allowed to expire — the
        plant of ONE datacenter. This runs once per DC, and the drop-stale sweep
        below used to walk the whole timer map, so the second site's pass deleted
        the first site's timers every tick and only the last DC processed kept any.
        `_run_unproven` therefore answered False forever on every other site, and
        `cooling_degraded` reported a healthy plant through a total silent loss of
        chilled water. Invisible to a single-DC fixture, which is why the unit test
        passed while the live two-site topology was broken.
        """
        seen = set()
        for name in commanded:
            seen.add(name)
            if (self._run_status_off(name)
                    and name not in self._plant_unpowered_names
                    and not self._is_alarmed(name)):
                self._run_proof_s[name] = self._run_proof_s.get(name, 0.0) + self._dt
            else:
                self._run_proof_s.pop(name, None)
        # A unit that has left the commanded set (staged off, or its train dropped)
        # is no longer failing to start; drop its timer so a later promotion starts
        # from zero rather than inheriting a stale dwell. Bounded by *scope* — a
        # machine at another site has not left anything, and expiring it here is
        # how one DC's tick silently disarmed every other DC's proof timer.
        for name in [n for n in self._run_proof_s
                     if n not in seen and (scope is None or n in scope)]:
            self._run_proof_s.pop(name, None)

    def _run_proof_failed(self, name: str) -> bool:
        """True once a commanded-on unit has failed to prove run status for the
        full dwell — a failure-to-start, and grounds to promote the standby.

        Use this for FAILOVER decisions only. The dwell exists so a slow start is
        not mistaken for a dead machine; it is not a statement about how much
        cooling the plant is making right now. For that, see _run_unproven.
        """
        return self._run_proof_s.get(name, 0.0) >= self._RUN_PROOF_S

    def _run_unproven(self, name: str) -> bool:
        """True if this commanded unit is silent RIGHT NOW — no run status for at
        least one full tick.

        The difference from _run_proof_failed matters. When every train is dead the
        proof timer promotes a standby, that one goes silent too, and its timer
        restarts from zero — so 'has failed the dwell' is true for one tick in
        ninety while the plant is short continuously. A health readout sampling in
        between saw a healthy plant through a total loss of chilled water. Capacity
        is lost the moment a commanded machine stops; only the FAILOVER waits.

        One tick of grace, not zero, so a standby promoted this tick — which reads
        running=0 until it starts — is not called short for existing.
        """
        return self._run_proof_s.get(name, 0.0) >= self._dt

    @staticmethod
    def _is_alarmed(name: str) -> bool:
        """True if this plant device is genuinely UNHEALTHY — an Alarm_* point is set.

        Deliberately narrower than _is_faulted: it does NOT treat a stopped unit as a
        fault. The two questions are different and must not share an answer:

          • "is this costing us cooling right now?"  — _is_faulted. A unit that should
            be running but isn't IS a loss, so stopped counts. Right for the penalty.
          • "can the BMS pick this unit?"            — _is_alarmed. A standby unit is
            stopped ON PURPOSE and is perfectly healthy; that is the entire point of
            N+1. Judging availability with _is_faulted disqualified every standby unit
            for being on standby, so the lead could never rotate and cycled-off tower
            cells oscillated in and out of the available set every tick.
        """
        pv = _plant_state_cache.get(name)
        if not pv:
            return False
        return any(k.startswith("Alarm_") and k not in _CAPACITY_ALARMS
                   and float(v) >= 0.5 for k, v in pv.items())

    # Command-to-feedback deviation a modulating valve shows in NORMAL service.
    # Commanded leads and the disc follows it across the actuator's stroke time,
    # so a valve tracking a moving load is always a little behind its command.
    # Below this it is trimming, not failing.
    _VALVE_DEV_DEADBAND = 0.10

    def _valve_lost_weight(self, name: str) -> float:
        """How much of a header valve's duty is lost, 0..1.

        Severity is WHERE the disc froze, not that it froze. A plant-header valve
        is FAIL-IN-PLACE: the electric actuators used at that torque (Rotork,
        Bettis, Belimo PR) have no spring return, so losing control leaves the disc
        standing where it was rather than driving it to a safe end. One stuck wide
        open still passes design flow and costs nothing; one stuck shut takes the
        loop out. Scoring both as a flat total loss — which is what a bare
        `_is_faulted` does — makes a non-event and a catastrophe read alike.

        Measured as the command-to-feedback deviation the BMS itself alarms on:
        Position against Commanded_Position, both already on the wire. Only the
        SHORTFALL counts — a valve stuck further OPEN than asked is not a cooling
        loss, it is a pumping-energy penalty this model does not charge for.

        Applied only to a valve that is genuinely out of control. A healthy valve
        scores zero from its fault state, never from its travel, so ordinary
        modulation can never be mistaken for a loss no matter how far it strokes.

        No position feedback is NOT no loss. A modulating valve that has stopped
        reporting travel is unknown, and the conservative reading — the one a real
        BMS takes when the feedback signal drops — is that it is shut.
        """
        if not self._is_faulted(name):
            return 0.0
        pv = _plant_state_cache.get(name) or {}
        pos, cmd = pv.get("Position"), pv.get("Commanded_Position")
        if pos is None or cmd is None:
            return 1.0
        try:
            pos, cmd = float(pos), float(cmd)
        except (TypeError, ValueError):
            return 1.0
        if cmd <= 0.0:
            return 0.0            # nothing is being asked of it
        dev = (cmd - pos) / cmd
        db = self._VALVE_DEV_DEADBAND
        return max(0.0, min(1.0, (dev - db) / (1.0 - db)))

    # Cooling penalty model constants.
    #
    # RATES ARE PER SECOND, and both branches of the penalty scale by the measured
    # tick length — the same contract the condenser loop already states. This is
    # not a style point: the two loops sit in the same tick, and while this one
    # integrated per TICK the room heated at a rate set by how often the simulator
    # happened to run. Measured before the fix: the identical total chilled-water
    # loss reached 7.9 K in 60 s at a 1 s tick and 4.0 K at a 2 s tick. A hall does
    # not cool more slowly because the host is busy, and two sites on one host must
    # not heat at different rates.
    _COOL_TOL = 0.34     # cooling-loss fraction the plant rides out (N+1 + thermal mass)
    _COOL_RUN = 0.20     # runaway integration gain (°C/s per unit deficit)
    _COOL_MAX = 28.0     # ceiling — equipment thermal-limit territory (inlet → ~50 °C)
    # Time constant of the bounded branch, chosen so that a 1 s tick reproduces the
    # 0.06 per-tick EMA this model was calibrated with: 1 − exp(−1/16.16) = 0.06.
    # Every number the existing suite encodes therefore survives the change, and
    # only the tick-rate DEPENDENCE goes away.
    _COOL_TAU_S = 16.16
    # Seconds of chilled-water + room-air thermal mass. This is the whole reason
    # bulk mechanical load is allowed to sit on an MCC instead of a UPS: the loop
    # keeps rejecting heat for about a minute after the pumps stop, which comfortably
    # covers the ~12 s transfer and the ~25 s staged restart. An UNPOWERED unit's
    # contribution to the cooling-loss fraction therefore ramps in over this window
    # rather than landing at full weight on the first dead tick. A FAULTED unit gets
    # no such grace — the rest of the plant has already been compensating for it.
    _CHW_RIDE_S = 60.0

    # Failure-to-start dwell. Commanded on with run status still off for this long
    # marks the train unfit and promotes a standby. Real sequences use 30–120 s;
    # 90 s sits in the middle and comfortably clears a staged restart (~25 s) and a
    # generator transfer (~12 s), so a legitimately slow start is not mistaken for
    # a dead machine.
    _RUN_PROOF_S = 90.0
    # Debounce before a commanded-on unit reading stopped is ANNUNCIATED. Only
    # has to outlast a staging transition (one or two ticks), so it is far
    # shorter than the failover proof above: an operator should hear about a
    # genuinely dead machine promptly, just not about a lead/lag handover.
    _RUN_ALARM_S = 10.0

    # ── Evaporator flow switch ──────────────────────────────────────────────
    # Every chiller is safety-interlocked to a flow switch on its evaporator: no
    # water through the barrel and the machine must stop, because running on would
    # freeze and split the tubes. Modelled on the fraction of required chilled-water
    # pumping that survives, which is what actually determines whether water moves.
    #
    # Before this existed the interlock was unreachable: loop flow was computed from
    # IT heat and merely divided across the pumps still running, so faulting every
    # pump RAISED the published flow. CHWFlowLoss fired once in 38 campaign
    # scenarios — only when the meter was pinned by hand — and no chiller stopped.
    # An interlock that cannot trip is not an interlock.
    _FLOW_TRIP_FRAC = 0.25   # below a quarter of required pumping, flow is "lost"
    _FLOW_PROOF_S   = 10.0   # dwell, so a stage change or valve step cannot trip it
    # Auto-resetting, unlike the high head-pressure lockout. A flow-loss interlock
    # is a protective cutout that permits a restart once flow proves — it is the
    # repeated-trip counter, not the first trip, that demands a manual reset on a
    # real machine.

    # ── Chiller head-pressure protection ────────────────────────────────────
    # Design condenser water is 29.4/35 °C (85/95 °F). Thresholds are expressed
    # in condenser SUPPLY temperature because that is what the tower actually
    # controls; Cond_Pressure is published alongside for the BACnet/SNMP view.
    # _COND_DESIGN_C is the DESIGN point only (worst-case summer wet bulb + design
    # approach). The loop's live base temperature is not this constant — it is
    # cooling_model.cond_supply_c(city, cells): wet bulb + the approach the running
    # cells achieve. So the published condenser water gets colder on a cold night,
    # colder again when the bank shares the load across more cells, and only rises
    # above the base when rejection is genuinely lost.
    _COND_DESIGN_C = 30.5    # design condenser supply temp (matches the point base)
    # The three protection thresholds are the TEMPERATE selection (base 30.5 →
    # 36 / 42 / 33). A machine bought for a humid site is selected for that site's
    # design condition, so each threshold also rides the live base by the same
    # spacing — otherwise a Singapore plant legitimately holding 34–35 °C water
    # would sit in permanent capacity limit, which is a modelling artifact, not a
    # fault. max() means the protection never gets TIGHTER on a cold night.
    _COND_LIMIT_C  = 36.0    # capacity-limit onset — machine starts unloading
    _COND_TRIP_C   = 42.0    # high-pressure safety trips here
    _COND_RESET_C  = 33.0    # condenser must fall this low before a reset will hold
    _COND_LIMIT_MARGIN_C = 5.5  # limit onset, as °C above the site's live base
    _COND_TRIP_MARGIN_C  = 11.5 # HP cutout, as °C above the site's live base
    _COND_RESET_MARGIN_C = 2.5  # reset must fall this close to the site's live base
    _COND_MAX_C    = 50.0    # ceiling once rejection is fully gone
    _COND_RANGE_C  = 5.0     # condenser-water range (return − supply) at design flow
    _COND_RISE     = 0.35    # °C/s the loop heats with rejection fully lost
    _COND_FALL     = 0.55    # °C/s it recovers once the towers are back
    _COND_TRIP_S   = 5.0     # seconds above trip temp before the safety latches
    _CHILLER_MIN_LOAD = 0.40 # capacity floor while limiting (40 % of nameplate)

    def _ensure_staging_primed(self) -> None:
        """Make sure a staging decision exists before anything consumes one.

        Staging is computed in _compute_power_flow, which runs LAST in the tick, so
        the cooling passes ahead of it read the previous tick's decision. At steady
        state that one-tick lag is immaterial. On the very FIRST tick there is no
        previous decision at all: the standby set is empty, so every chiller looks
        like it is running and gets published with a live condenser range and running
        head pressure, and every tower cell reports its fan turning. It corrected
        itself a second later, which is exactly what made it easy to miss — it showed
        up as plant briefly reading "all running" after each restart.

        Priming once is cheaper and safer than reordering the tick: moving power flow
        ahead of the cooling passes would just hand the staleness to the penalty and
        derate inputs it consumes, trading one transient for another. Idempotent —
        after the first pass this is a dict check.
        """
        if self._plant_stage_on:
            return
        try:
            self._compute_power_flow()
        except Exception:
            log.exception("[StateStore] staging prime failed")

    def _compute_cond_loop(self) -> None:
        """Per-tick: condenser-water temperature, then chiller head-pressure
        protection (unload → trip → latched lockout).

        Physical chain the towers sit in:
            IT heat → chilled water → chiller evaporator → chiller condenser
            → CONDENSER WATER → cooling tower → atmosphere

        Kill the towers and the last hop is gone: condenser water has nowhere to
        dump heat, so it climbs. Refrigerant condensing pressure tracks that
        temperature, and a centrifugal chiller responds in two stages, which is
        what real machines do:

          * above _COND_LIMIT_C it UNLOADS to hold pressure down (capacity limit
            — Trane/Carrier/York all do this before any safety acts), and
          * above _COND_TRIP_C the high-pressure cutout latches it OFF.

        The trip LATCHES: clearing the tower fault cools the loop but does not
        restart the machine, matching a manual-reset HP cutout. That is the whole
        point of modelling it — a lost-rejection event leaves the plant crippled
        until someone resets it, instead of silently self-healing.

        Emits synthetic BACnet points (keyed by device IP, merged with operator
        overrides at the BACnet tick) so the trip is visible on the plant plane,
        and a per-chiller derate that _compute_chw_penalty folds into cooling
        loss — so unloading actually costs cooling rather than being cosmetic.
        """
        from core.cooling_model import tower_cells_needed, cond_supply_c
        self._ensure_staging_primed()
        ctx = self._cooling_context()
        city_by_dc = ctx.get("city_by_dc", {})
        dt = self._dt
        auto: Dict[str, dict] = {}
        self._chiller_derate = {}

        for dc, kinds in ctx["plant_by_dc"].items():
            # EVERY cell counts — the bank is not staged with the trains, so there is
            # no "staged-off" cell to exclude. Cells beyond what the load needs are
            # genuine N+x redundancy on the condenser side.
            towers = list(kinds.get("cooling_tower") or [])
            chillers = [c for c in (kinds.get("chiller") or [])
                        if c not in self._plant_standby_names]
            if not chillers:
                continue

            # Rejection capability = cells still moving air ÷ cells the CURRENT load
            # needs, not ÷ the whole bank. An unpowered cell counts as lost the same
            # as a faulted one — either way no air crosses the fill — but losing a
            # SURPLUS cell costs no rejection, it only gives back the approach credit
            # (which _compute_power_flow already reprices). Sizing the denominator to
            # the full bank, as this used to, read a healthy N+2 plant at half load as
            # 33 % short the moment one cell tripped.
            if towers:
                # Cells are counted by AVAILABILITY, not by whether they happen to be
                # spinning: a cell the bank cycled off is idle capacity that can start
                # on demand. _is_faulted would read every cycled-off cell as lost
                # rejection and drive a false cooling-degraded state.
                ok = sum(1 for t in towers if self._tower_available(t))
                _on = self._plant_stage_on.get(dc, 0)
                _inst = self._plant_installed_mods.get(dc, 0)
                _duty = (_on / _inst) if _inst else 1.0
                needed = tower_cells_needed(_duty, len(towers))
                reject = min(1.0, ok / max(1, needed))
            else:
                reject = 1.0        # no modelled towers → assume rejection is fine
            self._tower_reject[dc] = reject

            # Heat rejection needs BOTH tower air AND condenser-water flow. A stopped,
            # faulted, or unpowered CW pump means the loop water is not carrying heat to
            # the tower, so head pressure climbs the same as a stalled fan. The weaker
            # link caps rejection. (Chilled-water pumps are the evaporator side — a
            # different loop — so only the condenser pumps count here.)
            _cw_names = {tr.get("cwp") for tr in ctx.get("trains_by_dc", {}).get(dc, [])
                         if tr.get("cwp")}
            cwps = [p for p in (kinds.get("pump") or [])
                    if p in _cw_names and p not in self._plant_standby_names]
            if cwps:
                cw_ok = sum(1 for p in cwps
                            if not self._is_faulted(p)
                            and p not in self._plant_unpowered_names)
                # Kept for _compute_chw_loop, which caps the published condenser
                # flow by it: rejection capability and the flow meter that ought to
                # show the same loss must not be derived independently.
                self._cw_pump_frac[dc] = round(cw_ok / len(cwps), 4)
                reject = min(reject, cw_ok / len(cwps))

            # The CONDENSER-WATER header valve sits in this same rejection path, so a
            # stuck or shut VCW throttles heat on its way to the tower exactly as a
            # dead condenser pump does. It used to be pooled with the chilled-water
            # valve into one evaporator-side multiplier, which put its effect on the
            # wrong loop: faulting VCW moved chilled water and left the condenser
            # untouched. Role comes from the leading name segment, as it does for the
            # header probes. Halved, matching the evaporator-side weighting — a
            # throttled header is a partial restriction, not a closed one.
            _vcw = [v for v in (kinds.get("valve") or [])
                    if v.upper().startswith("VCW")
                    and v not in self._plant_standby_names]
            if _vcw:
                # Weighted by how far the disc actually is from its command, not by
                # the bare fault bit — see _valve_lost_weight. An unpowered actuator
                # keeps the binary reading: its feedback died with its supply.
                _vcw_lost = sum(1.0 if v in self._plant_unpowered_names
                                else self._valve_lost_weight(v)
                                for v in _vcw) / len(_vcw)
                reject = min(reject, 1.0 - 0.5 * _vcw_lost)

            # BASE loop temperature — what the healthy bank can actually hold right
            # now: site wet bulb + the approach the running cells achieve. This is the
            # floor the loop settles back to, replacing the old fixed 30.5 °C. It moves
            # with the weather (a January night in Dublin makes ~15 °C water, a humid
            # Singapore afternoon ~35 °C) and with how many cells share the load, so
            # the published Cond_Supply_Temp corroborates the compressor saving that
            # _compute_power_flow books instead of contradicting it.
            _need_run = self._tower_cells.get(dc)
            if _need_run:
                _cn, _cr = _need_run
            else:
                _cn = _cr = max(1, len(towers))
            base = cond_supply_c(city_by_dc.get(dc), _cn, _cr)
            self._cond_base_c[dc] = round(base, 2)

            # Loop temperature: heats toward the ceiling in proportion to lost
            # rejection, cools back toward the base when it returns. Rates are
            # per-second so the behaviour does not change with tick interval.
            cur = self._cond_water_c.get(dc, base)
            if reject >= 1.0:
                cur += (base - cur) * min(1.0, self._COND_FALL * dt)
            else:
                loss = 1.0 - reject
                target = base + (self._COND_MAX_C - base) * loss
                if cur < target:
                    cur = min(target, cur + self._COND_RISE * loss * dt)
                else:
                    cur += (target - cur) * min(1.0, self._COND_FALL * dt)
            cur = max(base, min(self._COND_MAX_C, cur))
            self._cond_water_c[dc] = round(cur, 2)

            # Publish the live loop on the TOWER cells too. The cells are the machines
            # that set this temperature, so leaving them on their own synthetic curve
            # made the bank claim 30 °C water while the chillers it feeds reported 24 —
            # the plant page contradicting itself. Cold basin / tower outlet IS the
            # condenser supply; the hot inlet sits a design range above it (the CW
            # pumps are VFD and track load, so the range stays near design).
            _cells_on = self._tower_running_now.get(dc) or set(towers)
            for _tn in towers:
                _tip = _tn
                if _tip:
                    # A cell the bank has cycled OFF is valved out with its fan stopped:
                    # it rejects nothing, so its inlet, outlet and basin all sit at loop
                    # temperature with no range. Publishing the running range on it would
                    # claim heat rejection from a stopped cell — and the BACnet off-unit
                    # handling would then average the pair into a temperature that
                    # matches neither the loop nor the running cells.
                    # Live range, one tick behind: _compute_chw_loop derives it from
                    # the flow the CW pumps delivered, and runs after this pass.
                    _rng = (self._cond_range_c.get(dc, self._COND_RANGE_C)
                            if _tn in _cells_on else 0.0)
                    auto.setdefault(_tip, {}).update({
                        "Cond_Water_Out": round(cur, 1),
                        "Cond_Water_In": round(cur + _rng, 1),
                        "Basin_Temp": round(cur, 1)})

            # Condensing pressure for the published point, as a linear fit through
            # R-134a saturation. Running, the compressor adds lift on top of the
            # loop temperature; this puts the _COND_TRIP_C threshold at ~1200 kPa,
            # a textbook centrifugal HP cutout. A TRIPPED machine has no lift —
            # the compressor is off and the refrigerant equalises — so it reads
            # the loop's saturation pressure instead of continuing to climb.
            cond_kpa_run  = 900.0 + (cur - self._COND_DESIGN_C) * 26.0
            cond_kpa_idle = 700.0 + (cur - self._COND_DESIGN_C) * 18.0

            # STAGED-OFF chillers sit on the same headers. Their condenser barrel is
            # full of loop water — piped to the common condenser main, and even a shut
            # isolation valve conducts — so it reads the LOOP temperature, not a
            # synthetic curve of its own. Without this a standby machine claimed 33 °C
            # condenser water while the running one beside it reported 24.5: the same
            # self-contradiction the tower cells had, and one this model created by
            # moving the live loop off the old fixed 30.5 °C base.
            #
            # A stopped machine also has no compressor lift, so its refrigerant sits at
            # the loop's saturation pressure — the idle fit, not the running one. Only
            # these BASE values go out; the BACnet layer still collapses an off unit's
            # supply/return to one value (no flow → no ΔT) and zeroes run status, power
            # and compressor load. The evaporator side is deliberately left alone: a
            # staged-off chiller's CHW barrel is isolated by its own stopped pump and
            # genuinely drifts warm, which is what it already reports.
            for _cn2 in (kinds.get("chiller") or []):
                if _cn2 not in self._plant_standby_names:
                    continue
                _cip = _cn2
                if _cip:
                    auto.setdefault(_cip, {}).update({
                        "Cond_Supply_Temp": round(cur, 1),
                        "Cond_Return_Temp": round(cur, 1),
                        "Cond_Pressure": round(cond_kpa_idle, 1)})

            # Site-adjusted protection thresholds (see the constants above).
            lim_c  = max(self._COND_LIMIT_C, base + self._COND_LIMIT_MARGIN_C)
            trip_c = max(self._COND_TRIP_C,  base + self._COND_TRIP_MARGIN_C)
            over = cur >= trip_c
            for name in chillers:
                ip = name
                latched = name in self._chiller_hp_lockout

                if not latched and over:
                    held = self._cond_trip_s.get(name, 0.0) + dt
                    self._cond_trip_s[name] = held
                    if held >= self._COND_TRIP_S:
                        self._chiller_hp_lockout.add(name)
                        latched = True
                        _msg = (f"{name} tripped on HIGH HEAD PRESSURE "
                                f"(condenser water {cur:.1f} C) — latched, manual reset")
                        log.warning("[Plant] %s", _msg)
                        if self._log_cb:                # surface in the UI event log
                            try: self._log_cb(_msg, "warning")
                            except Exception: pass
                elif not over:
                    self._cond_trip_s.pop(name, None)

                if latched:
                    # Locked out: the machine is off and its capacity is gone.
                    self._chiller_derate[name] = 1.0
                    if ip:
                        # The CUTOUT. Alarm_HighPressure means exactly this from here
                        # on: latched out, compressor off, manual reset. The limit
                        # band below publishes Alarm_CondPressLimit instead, and is
                        # not asserted here — the machine is not unloading, it is off.
                        auto[ip] = {"Chiller_Running": 0.0, "Alarm_HighPressure": 1.0,
                                    "Alarm_CondPressLimit": 0.0,
                                    "Cond_Pressure": round(cond_kpa_idle, 1),
                                    "Cond_Supply_Temp": round(cur, 1),
                                    # Locked out: no compressor heat into the condenser,
                                    # so the barrel sits at loop temperature — no range.
                                    "Cond_Return_Temp": round(cur, 1),
                                    "Compressor_Load": 0.0}
                    continue

                if cur > lim_c:
                    # Capacity limit: unload linearly from full at the limit onset
                    # down to the floor at the trip point. This is the machine
                    # PROTECTING itself and still carrying load, so it annunciates on
                    # the limit point, not the cutout — see _CAPACITY_ALARMS. The
                    # distinction is what stops the BMS swapping lead machines over a
                    # condition that belongs to the condenser loop they share.
                    span = max(0.1, trip_c - lim_c)
                    frac = min(1.0, (cur - lim_c) / span)
                    avail = 1.0 - (1.0 - self._CHILLER_MIN_LOAD) * frac
                    self._chiller_derate[name] = round(1.0 - avail, 3)
                    if ip:
                        auto[ip] = {"Alarm_CondPressLimit": 1.0,
                                    "Cond_Pressure": round(cond_kpa_run, 1),
                                    "Cond_Supply_Temp": round(cur, 1),
                                    "Cond_Return_Temp": round(
                                        cur + self._cond_range_c.get(
                                            dc, self._COND_RANGE_C), 1),
                                    "Compressor_Load": round(avail * 100.0, 1)}
                elif ip:
                    # Supply and return move TOGETHER. Publishing only the supply left
                    # the return on its own 35.5 °C curve, so once the live loop dropped
                    # off the old fixed base the machine reported an ~11 °C condenser
                    # range against a 5 °C design — the running chiller contradicting
                    # its own tower. The CW pumps are VFD on a condenser-ΔT setpoint,
                    # so the range holds near design WHILE THEY CAN DELIVER; once
                    # they cannot the control loop is out of authority and the
                    # derived range widens, which is the whole point of publishing
                    # the live figure rather than the design constant.
                    auto[ip] = {"Cond_Pressure": round(cond_kpa_run, 1),
                                "Cond_Supply_Temp": round(cur, 1),
                                "Cond_Return_Temp": round(
                                    cur + self._cond_range_c.get(
                                        dc, self._COND_RANGE_C), 1)}

        self._plant_auto_points = auto

    def reset_chiller_trip(self, name: str) -> str:
        """Manual reset of a latched high-pressure trip.

        Refuses while condenser water is still hot — a real HP cutout will not
        hold in until the head pressure has actually come down, and letting it
        restart into the same condition would just trip it again.
        """
        if name not in self._chiller_hp_lockout:
            return "not tripped"
        dc = None
        for _dc, kinds in (self._cooling_context()["plant_by_dc"]).items():
            if name in (kinds.get("chiller") or []):
                dc = _dc
                break
        cond = self._cond_water_c.get(dc, self._COND_DESIGN_C) if dc else self._COND_DESIGN_C
        # The reset threshold rides on the site's live base temperature, not a fixed
        # 33 °C: a hot, humid site legitimately holds condenser water in the mid-30s,
        # and an absolute limit below that would make the trip permanently unresettable.
        # The margin is what proves head pressure has actually come down.
        limit = max(self._COND_RESET_C,
                    self._cond_base_c.get(dc, self._COND_DESIGN_C)
                    + self._COND_RESET_MARGIN_C)
        if cond > limit:
            return f"condenser water still {cond:.1f} C — must fall below {limit:.0f} C"
        self._chiller_hp_lockout.discard(name)
        self._cond_trip_s.pop(name, None)
        return "reset"

    def get_chiller_trips(self) -> list:
        """Names of chillers latched out on high head pressure."""
        return sorted(self._chiller_hp_lockout)

    # ── IT thermal protection ────────────────────────────────────────────────
    # Silicon does not sit at its limit and keep working. It throttles well before
    # it, and the platform shuts down at it — PROCHOT then THERMTRIP on Intel,
    # equivalently on AMD, surfaced by every BMC as a thermal-shutdown event.
    #
    # Without this the die simply clamped at 95 °C: a runaway had no end state, no
    # trap and no protective response, so a cooling failure that should cost
    # capacity cost nothing at all. Throttling is also what makes the failure
    # self-limiting, which is the behaviour an operator is trying to rehearse.
    _CPU_THROTTLE_C = 90.0     # shed load above this
    _CPU_SHUTDOWN_C = 95.0     # platform thermal trip
    _THROTTLE_FLOOR = 0.35     # deepest load reduction before shutdown takes over

    def _apply_thermal_protection(self, device: "Device") -> None:
        """Throttle a hot server, and shut it down at the thermal limit.

        Called after cpu_temp settles for the tick. Load is reduced in proportion
        to how far past the throttle point the die has gone, which feeds straight
        back into _server_live_watts and the heat the room model sees — so a
        throttling fleet genuinely draws less and heats less, exactly as a real one
        does under a cooling failure.
        """
        if getattr(device, "power_state", "On") == "Off":
            return
        temp = float(getattr(device, "cpu_temp", 0.0) or 0.0)
        if temp >= self._CPU_SHUTDOWN_C:
            device.power_state = "Off"
            if device.name not in self._thermal_shutdown:
                self._thermal_shutdown.add(device.name)
                log.warning("[Thermal] %s THERMAL SHUTDOWN at %.1f C — platform "
                            "tripped, needs a manual power-on once cool",
                            device.name, temp)
            return
        if temp <= self._CPU_THROTTLE_C:
            self._throttled.pop(device.name, None)
            return
        # Linear ramp from full speed at the throttle point down to the floor at the
        # shutdown point. Deliberately not a cliff: real throttling is progressive.
        span = max(1e-6, self._CPU_SHUTDOWN_C - self._CPU_THROTTLE_C)
        depth = min(1.0, (temp - self._CPU_THROTTLE_C) / span)
        factor = 1.0 - depth * (1.0 - self._THROTTLE_FLOOR)
        self._throttled[device.name] = round(factor, 3)
        device.cpu_usage = round(max(0.0, float(device.cpu_usage or 0.0) * factor), 1)

    def _tower_available(self, name: str) -> bool:
        """Can this cooling-tower cell reject heat if the bank asks it to?

        AVAILABILITY, not whether it happens to be spinning — the bank cycles
        surplus cells off and they start on demand, so `_is_faulted` would read
        every cycled-off cell as lost rejection.

        One predicate because this was judged in TWO places — `_compute_cond_loop`
        sizing `_tower_reject`, and `_compute_power_flow` sizing the running set —
        and only one of them was ever taught about a silent stop. Stopping every fan
        in the bank therefore changed nothing at all: rejection stayed 1.00, the
        condenser never moved, no high-pressure trip. The fixture test missed it
        because it ALARMS the cells; the live campaign stops them silently, which is
        the failure a real BMS has to catch on its own.
        """
        return (name not in self._plant_unpowered_names
                and not self._is_alarmed(name)
                and self._tower_silent_s.get(name, 0.0) < self._dt)

    # Cooling-plant health → one status string per machine, worst condition first.
    # Ordered, because a machine can hold several binaries at once and a trap needs
    # a single answer: a chiller that has latched out on head pressure is reported
    # as tripped even if it is also annunciating high leaving water.
    _PLANT_STATUS_ORDER = (
        ("Alarm_HighPressure",   "hp_trip"),
        ("Alarm_FlowLoss",       "flow_loss"),
        ("Alarm_ActuatorFault",  "actuator_fault"),
        ("Alarm_AirflowLoss",    "airflow_loss"),
        ("Alarm_Fault",          "unit_fault"),
        ("Alarm_HighVibration",  "vibration"),
        ("Alarm_LowBasin",       "low_basin"),
        ("Alarm_LowFlow",        "low_flow"),
        ("Alarm_HighTemp",       "high_temp"),
        ("Alarm_LowEvapTemp",    "low_evap_temp"),
        ("Filter_Dirty",         "filter_dirty"),
    )
    _PLANT_FACT_TYPES = {"chiller", "cooling_tower", "pump", "valve", "crah", "cdu"}

    def _plant_status(self, device: "Device") -> str:
        """Worst active condition on a cooling-plant machine, for the trap rules.

        "stopped" outranks everything below a latched trip: a machine that is not
        running is not doing its job whatever else it is annunciating. Capacity
        alarms are excluded for the same reason they are excluded from the cooling
        loss — they announce a shortfall the thermal model has already booked.

        A STAGED-OFF unit reads "ok", not "stopped". It is idle on purpose — that
        is the entire point of N+1 — and treating idle as stopped raised a
        PlantUnitStopped trap on every cycled-off tower cell of a perfectly healthy
        plant, four of them at baseline. Same distinction the store already draws
        between _is_faulted and _is_alarmed, for the same reason.

        ORDER MATTERS. Alarms are checked BEFORE the standby test, because a unit
        that alarms gets ranked unfit and staged off — so a standby-first check
        would swallow the very alarm that put it there, and the recovery trap could
        never fire when the operator cleared it. And a clean standby reads "ok"
        rather than a status of its own, so alarm → cleared always lands on "ok"
        whether or not the unit was promoted back.
        """
        if device.device_type.value not in self._PLANT_FACT_TYPES:
            return ""
        if device.name in self._chiller_hp_lockout:
            return "hp_trip"
        if device.name in self._chw_flow_interlock:
            return "flow_loss"
        pv = _plant_state_cache.get(device.name)
        if not pv:
            return "ok"
        for point, status in self._PLANT_STATUS_ORDER:
            if point in _CAPACITY_ALARMS:
                continue
            if float(pv.get(point, 0.0)) >= 0.5:
                return status
        if device.name in self._plant_standby_names:
            self._stopped_since.pop(device.name, None)
            return "ok"           # idle N+1 spare, not a fault
        if any(k in _RUNNING_POINTS and float(v) < 0.5 for k, v in pv.items()):
            # DEBOUNCED. A staging change moves units in and out of the standby set
            # a tick or two before the BACnet plane catches up, so a unit being
            # promoted is briefly "commanded on but reading stopped" through no
            # fault of its own. Annunciating that raised a PlantUnitStopped and a
            # matching Cleared for every member on every staging change — four
            # pairs of pure noise on a single chiller failover, which is exactly
            # how an operator learns to ignore an alarm.
            #
            # Wall-clock rather than tick-counted because _publish_facts does not
            # run on the same cadence as the physics tick.
            t0 = self._stopped_since.setdefault(device.name, time.monotonic())
            if time.monotonic() - t0 < self._RUN_ALARM_S:
                return "ok"
            return "stopped"
        self._stopped_since.pop(device.name, None)
        return "ok"

    def cooling_model_kw(self) -> float:
        """Staged-model cooling electrical, scaled by DELIVERED capacity (kW).

        The raw model answers "what would a healthy plant draw for this load" — a
        DEMAND figure. On its own it cannot see a plant that has stopped running,
        which is why PUE held flat through a total loss of chilled water while the
        branch meters showed the mechanical panel fall by a third. Scaling by the
        surviving fraction, per DC, makes it a statement about the plant.

        Deliberately still a model and not the meters: metered plant draw is capped
        by the small curated device nameplates and collapses PUE toward 1.0 as the
        fleet outgrows the plant. get_power_summary takes max(metered, this), which
        keeps a healthy plant on today's behaviour and lets the meters win once the
        plant is genuinely short.
        """
        if not self._cool_model_w_by_dc:
            return max(0.0, self._cool_model_w) / 1000.0
        return sum(
            max(0.0, w) * (1.0 - min(1.0, max(0.0, self._cool_loss_frac.get(dc, 0.0))))
            for dc, w in self._cool_model_w_by_dc.items()) / 1000.0

    def cooling_degraded(self, dc: str) -> bool:
        """True if this DC's cooling is genuinely SHORT right now — the plant could not
        fill its required run set with a HEALTHY train, so a running lead train still
        carries an unpowered / latched-out / actively-alarmed member. False when a
        standby covered a trip (N+1 held): only redundancy is lost, not cooling.

        Checks real faults only — NOT a unit's raw running-status bit — so a healthy
        standby that was just promoted (running=0 for one tick) never reads as short,
        and a fully-staged plant at high load is not flagged unless a member truly
        faults. A member that is commanded on and SILENT does count, after one tick
        of grace — capacity is lost the moment a running machine stops, even though
        the failover deliberately waits out a proof dwell before promoting a
        standby. Without that this predicate answered "healthy" through a total loss
        of chilled water whose only symptom was silence.

        Tower cells are not train members (the bank is unstaged), so the condenser
        side is checked once: short only when fewer cells are turning than the load
        needs — losing a surplus cell costs efficiency, not cooling."""
        if self._tower_reject.get(dc, 1.0) < 1.0:
            return True
        # THE THERMAL MODEL IS THE SOURCE OF TRUTH for whether a site is short, and
        # this predicate has to agree with it. _cool_loss_frac is the delivered-
        # capacity deficit _compute_chw_penalty just booked: 0 means full cooling,
        # anything above it means the plant is not covering its load — and it is
        # already computed from lost_weight, which carries the same _CAPACITY_ALARMS
        # exemption as the member checks below, so a healthy-but-outmatched machine
        # still does not count.
        #
        # Without this the answer depended on TIMER PHASE. The member scan below
        # reads _run_unproven, but the proof timers are accrued against the
        # PREVIOUS commanded set and the run set is recomputed after — so on the
        # tick a silent lead is demoted and a standby promoted, the newly commanded
        # chiller has no timer at all and the scan finds nothing. With every chiller
        # in a site silently stopped the lead rotates on the dwell, so the predicate
        # answered "healthy" on every changeover tick while the loss fraction sat at
        # 1.0 and the room ran away. The single-DC gate passed only because its hold
        # length happened to miss the flip.
        if self._cool_loss_frac.get(dc, 0.0) > 0.0:
            return True
        for tr in self._plant_trains_run.get(dc, []):
            for m in tr["members"]:
                if (m in self._plant_unpowered_names
                        or m in self._chiller_hp_lockout
                        or self._run_unproven(m)):
                    return True
                pv = _plant_state_cache.get(m)
                # CAPACITY alarms are excluded here for the same reason they are
                # excluded from _is_faulted and _is_alarmed: they announce a
                # shortfall the thermal model has ALREADY booked, so counting one
                # as a fault reports a healthy plant as short purely because it
                # said so. A chiller at full compressor with its leaving water off
                # setpoint is outmatched by the load, not broken — and the load is
                # already driving the penalty.
                #
                # This predicate was the last one still missing the guard. It went
                # unnoticed while the trips endpoint only consulted it when a
                # chiller had already latched out; fixing that (F14) is what
                # exposed it.
                if pv and any(k.startswith("Alarm_")
                              and k not in _CAPACITY_ALARMS
                              and float(pv.get(k, 0.0)) >= 0.5
                              for k in pv):
                    return True
        return False

    def _dc_of_chiller(self, name: str) -> "str | None":
        for _dc, kinds in (self._cooling_context().get("plant_by_dc", {})).items():
            if name in (kinds.get("chiller") or []):
                return _dc
        return None

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
        from core.cooling_model import PLANT_MODULE_KW
        for dc, kinds in ctx["plant_by_dc"].items():
            # Thermal-mass grace for a plant that has just lost power (see
            # _CHW_RIDE_S). While the loop still has stored cooling, an unpowered
            # unit costs nothing; past that the loop is exhausted and its full loss
            # phases in over another ride-through's worth of seconds. So a clean
            # transfer (plant fully back inside ~37 s) never warms the room, while a
            # genset that fails to start rides the grace out and then runs away.
            _dead = self._mech_dead_s.get(dc, 0.0)
            ride = max(0.0, min(1.0, (_dead - self._CHW_RIDE_S) / self._CHW_RIDE_S))

            def lost_weight(name: str) -> float:
                """How much of this unit's capacity is gone, 0..1.

                An UNPOWERED unit is not rejecting heat — but the chilled-water loop
                has thermal mass, so during the ride-through window its loss costs
                nothing and only phases in afterwards (see _CHW_RIDE_S). Counted
                directly rather than waiting for its BACnet run-status to read 0, so
                the penalty is right even with the BACnet server disabled. A FAULTED
                unit gets no grace: the rest of the plant has already been
                compensating for it.
                """
                if name in self._plant_unpowered_names:
                    return ride
                if self._is_faulted(name) or name in self._chw_flow_interlock:
                    return 1.0
                # A chiller riding its head-pressure limit is still running but
                # has shed capacity — a PARTIAL loss, not a binary one.
                return self._chiller_derate.get(name, 0.0)

            # ── Cooling loss, computed per TRAIN ─────────────────────────────
            # A train is a series chain in its own right: chiller → evaporator pump →
            # condenser pump → tower cell. Lose any member and that train delivers
            # nothing, so a train's loss is its WORST member, not the product of the
            # whole plant's per-device-type fractions. Only the trains the BMS has
            # actually staged ON count — a standby train is redundancy, not a loss.
            #
            # The old per-type product multiplied fractions across the plant, so a 2N
            # electrical split (half of every type on each side) read as ~87 % loss
            # when one side dropped, instead of the one train it truly cost.
            trains_on = self._plant_trains_run.get(dc, [])

            # ── Evaporator pumping, and the flow switch behind it ────────────
            # How much of the chilled-water pumping the running trains REQUIRE is
            # actually available: one pump per running train, with the header
            # standby able to cover one that has failed. This is what decides
            # whether water moves through the barrels, and _compute_chw_loop scales
            # the published loop flow by it — so the header flow meter, the
            # CHWFlowLoss rule reading that meter, and this interlock all agree.
            _req = max(1, len(trains_on))
            _lead_p = [tr.get("chwp") for tr in trains_on if tr.get("chwp")]
            _ok = sum(1 for n in _lead_p
                      if not self._is_faulted(n)
                      and n not in self._plant_unpowered_names)
            _ok += sum(1 for n in (ctx.get("spare_chwp", {}).get(dc) or [])
                       if not self._is_faulted(n)
                       and n not in self._plant_unpowered_names)
            _pump_frac = max(0.0, min(1.0, _ok / _req))
            self._chw_pump_frac[dc] = round(_pump_frac, 4)

            _lost = self._chw_flow_lost_s.get(dc, 0.0)
            _lost = (_lost + self._dt) if _pump_frac < self._FLOW_TRIP_FRAC else 0.0
            self._chw_flow_lost_s[dc] = _lost
            _running = {tr.get("chiller") for tr in trains_on if tr.get("chiller")}
            if _lost >= self._FLOW_PROOF_S:
                _new = _running - self._chw_flow_interlock
                self._chw_flow_interlock |= _running
                for _c in sorted(_new):
                    log.warning("[Plant] %s shed on LOSS OF EVAPORATOR FLOW "
                                "(%.0f%% of required pumping) — flow-switch interlock",
                                _c, _pump_frac * 100.0)
            elif _lost == 0.0:
                # Flow proved again: the interlock permits a restart (see
                # _FLOW_PROOF_S). Only release this DC's machines.
                self._chw_flow_interlock -= _running

            if trains_on:
                deficit = 0.0
                for tr in trains_on:
                    worst = max((lost_weight(m) for m in tr["members"]), default=0.0)
                    if not tr["complete"]:
                        worst = 1.0     # a chiller with no pump moves no water
                    deficit += worst
                loss = deficit / len(trains_on)
            else:
                loss = 0.0

            # Header equipment sits OUTSIDE the trains and is common to all of them:
            # lose the chilled-water or condenser-water isolation valve and every
            # train downstream of it is throttled. Applied as a multiplier on the
            # surviving capacity, as before.
            def frac(kind: str, prefix: str = "") -> float:
                names = [n for n in (kinds.get(kind) or [])
                         if n not in self._plant_standby_names
                         and (not prefix or n.upper().startswith(prefix))]
                if not names:
                    return 0.0
                # A control valve is scored on WHERE its disc froze rather than on
                # the bare fault bit (see _valve_lost_weight); everything else is
                # binary. The unpowered ride-through still applies to both — that is
                # a property of the loop's thermal mass, not of the machine.
                _w = self._valve_lost_weight if kind == "valve" else lost_weight
                return sum(ride if n in self._plant_unpowered_names else _w(n)
                           for n in names) / len(names)

            # The TOWER BANK is header equipment too, now that cells no longer stage
            # with their train: rejection is one per-DC capability (cells turning ÷
            # cells the load needs, from _compute_cond_loop) applied to every train at
            # once. Counting a cell inside its train would both understate a bank-wide
            # loss and overstate the loss of one surplus cell.
            # A header valve throttles the loop it SITS IN, and the two sit in
            # different ones. Pooling both into a single evaporator-side multiplier
            # meant faulting the condenser-water valve warmed the chilled water and
            # left the condenser loop untouched — the wrong loop entirely. The role
            # is read off the leading name segment (VCHW / VCW), the same idiom the
            # header probes use. VCW's effect is applied in _compute_cond_loop, with
            # the tower cells and condenser pumps it shares the rejection path with;
            # only VCHW belongs here.
            avail = ((1.0 - loss) * (1.0 - 0.5 * frac("valve", "VCHW"))
                     * max(0.0, min(1.0, self._tower_reject.get(dc, 1.0))))
            loss = max(0.0, 1.0 - avail)                  # 0 = full cooling, 1 = none
            # PLANT OVERLOAD: live IT beyond the FULL installed plant is heat nothing
            # can reject (every module already on) — treat the excess as a cooling
            # loss so the room heats and PUE degrades, instead of a silent fake-good
            # reading. Expressed as a fraction of installed capacity.
            _ovl = self._plant_overload_kw.get(dc, 0.0)
            if _ovl > 0.0:
                _inst_kw = self._plant_installed_mods.get(dc, 1) * PLANT_MODULE_KW
                loss = min(1.0, loss + _ovl / max(1.0, _inst_kw))
            # Kept for the evaporator-side model: how short the plant is decides
            # whether it can still hold its chilled-water setpoint.
            self._cool_loss_frac[dc] = round(loss, 4)
            # Both branches integrate against MEASURED ELAPSED TIME, exactly as the
            # condenser loop does. See _COOL_RUN / _COOL_TAU_S for why.
            dt = self._dt
            cur = self._chw_pen.get(dc, 0.0)
            deficit = loss - self._COOL_TOL
            # Exponential ease toward a target, with the loop's thermal lag. A time
            # constant rather than a per-tick fraction, so a long tick eases further
            # in one step instead of taking the same small bite.
            def _ease(to: float) -> float:
                return cur + (to - cur) * (1.0 - math.exp(-dt / self._COOL_TAU_S))

            if deficit <= 0.0:
                # Bounded: redundancy absorbs it and the plant settles at a standing
                # offset proportional to the shortfall (≤ ~6 °C at the knee).
                new = _ease(loss * 18.0)
            else:
                # Runaway: the IT heat the plant can no longer take accumulates, so
                # integrate it upward per second of real time — but never BELOW the
                # offset the plant already carries AT the knee.
                #
                # That floor is what makes severity monotonic, and its absence made
                # the knee a cliff in the wrong direction. Just under _COOL_TOL a
                # fault eased straight to its standing offset and settled there;
                # just over it the integration started from wherever the penalty
                # happened to be — normally zero — and needed tens of seconds to
                # climb back to the value the MILDER fault was already sitting at.
                # Measured on three staged trains: one chiller lost (L 0.340) read
                # 6.089 K at 90 s, two lost (L 0.673) read 6.055 K. A worse failure
                # must never read milder, or an operator triaging by inlet
                # temperature works the bigger outage second.
                #
                # The floor is the KNEE offset, not this shortfall's own loss × 18.
                # That expression is calibrated for the bounded regime and only
                # meets the runaway branch at the knee; extrapolating it to a total
                # loss floors the penalty at 18 K and roughly doubles the measured
                # runaway rate (11.9 → 23.2 K at 90 s), which is a recalibration of
                # the thermal model wearing a monotonicity fix as a disguise. Past
                # the knee the plant has no steady state at all — that is what
                # runaway means — so the standing part stops growing and the
                # integral carries the severity from there.
                new = max(_ease(self._COOL_TOL * 18.0),
                          cur + self._COOL_RUN * deficit * dt)
            self._chw_pen[dc] = round(min(new, self._COOL_MAX), 3)

    # ── Chilled-water loop thresholds ─────────────────────────────────────────
    # A BMS alarms the CHILLED-WATER SUPPLY, not the return: supply is the
    # controlled variable, so a supply temperature off setpoint is by definition
    # the plant failing to do its job. Deadbands are the usual vendor defaults
    # (Trane Tracer / JCI Metasys chilled-water plant sequences): a couple of
    # degrees of tolerance, held for a few minutes so a stage change or a valve
    # step does not annunciate.
    _CHW_HIGH_DB_C   = 2.0     # K above setpoint before the plant is "off setpoint"
    _CHW_HIGH_S      = 180.0   # dwell before Alarm_HighCHWSupply latches
    _CHW_CLEAR_DB_C  = 1.0     # hysteresis — must come back inside this to clear
    # Return-air limit for a CRAH. ASHRAE A1 allowable tops out at 32 °C INLET; a
    # return sensor sits in the hot aisle, so the alarm point is well above that.
    _CRAH_RETURN_ALARM_C = 42.0
    # CHW valve authority: percent of travel per K of chilled-water shortfall. At 20
    # a coil starved by ~1.5 K is already wide open, which is the behaviour a real
    # discharge-temperature loop shows — it does not modulate gently through a
    # failure, it saturates.
    _CRAH_VALVE_GAIN = 20.0
    # CDU: how much of the facility loop's drift reaches the technology-cooling
    # supply. A liquid-cooling CDU is a heat exchanger between the two loops, so
    # warmer facility water means warmer coolant, attenuated by the approach the
    # exchanger holds. Not 1.0 — the secondary loop has its own pump work and
    # thermal mass, and a CDU with any control authority left will spend it here.
    _CDU_FOLLOW_FRAC = 0.8
    # Warm-water cooling: the whole point of a cold-plate loop is that it rejects at
    # ~32 °C, well above the CRAH coils, which is what lets the chillers run at a
    # higher COP. Matches the generator's design values for the CDU class.
    _CDU_TCS_SETPOINT_C = 32.0
    _CDU_TCS_RANGE_C = 13.0
    # Pump design points, matching the PLANT_SPEC bases in bacnet_plant_generator.
    _PUMP_SUCTION_KPA   = 130.0
    _PUMP_DIFF_KPA      = 300.0
    # Air-side mixing: a hot aisle is never pure exhaust — some cold-aisle air
    # bypasses the racks and dilutes what reaches the CRAH return.
    _RETURN_MIX_FRAC = 0.85

    def _compute_chw_loop(self) -> None:
        """Per-tick: the EVAPORATOR side of the plant — what water the chillers are
        actually making, how much of it is moving, and how wide the loop has opened.

        The condenser side (_compute_cond_loop) models heat leaving the building.
        This models heat arriving: IT load → CRAH/CDU coils → chilled-water return
        → chiller evaporator. In a primary-variable plant that chain behaves as:

            supply  = setpoint, until the plant runs out of capacity and drifts off it
            ΔT      = design, until flow bottoms out on the minimum-flow bypass
            flow    = Q / (cp·ΔT)  — so it tracks load, and pins at the bypass minimum

        Published onto the running plant's BACnet points through the same per-IP
        auto-point channel _compute_cond_loop uses, so CHW supply/return/flow, pump
        flow and head, tower makeup and CRAH return air all move with the fleet
        instead of walking on a clock. The load-driven plant alarms (CHW supply off
        setpoint, CRAH return air over limit, pumps at full speed and still short)
        are raised here too — they are the annunciation of the same physics.

        Runs AFTER _compute_power_flow so it sees this tick's staging, duty and
        per-unit draws, and MERGES into _plant_auto_points rather than replacing it.
        """
        from core.cooling_model import (
            CHW_SETPOINT_C, CHW_DESIGN_DT_C, CHW_MAX_DT_C, COND_DESIGN_RANGE_C,
            COND_MAX_RANGE_C, CP_WATER_KJ_KGK, PLANT_MODULE_KW,
            chw_supply_c, CHILLER_COP_RATED,
            chw_flow_frac, water_flow_lps, makeup_flow_lpm, pump_head_frac,
            affinity_speed_frac, vfd_speed_frac, PUMP_MIN_SPEED)
        ctx = self._cooling_context()
        auto = self._plant_auto_points
        dt = self._dt
        # Drop the capacity alarms this pass owns before re-deriving them. In the
        # normal tick order _compute_cond_loop has just replaced the whole map, so
        # nothing is stale — but relying on that would mean a cleared alarm latches
        # forever the moment anything reorders the chain. Owning our own keys keeps
        # this pass correct on its own.
        for _pts in auto.values():
            for _k in _CHW_OWNED_ALARMS:
                _pts.pop(_k, None)

        for dc, kinds in ctx["plant_by_dc"].items():
            itl_kw = self._it_live_by_dc.get(dc, 0.0) / 1000.0
            duty = self._plant_duty.get(dc, 0.0)
            # How far the plant has been pushed off its setpoint. The thermal model
            # already integrates this (_chw_pen drives room supply temperature), so
            # reusing it keeps the water the plant makes and the air the room gets
            # telling one story instead of two.
            pen = self._chw_pen.get(dc, 0.0)
            supply = chw_supply_c(pen, CHW_SETPOINT_C)
            # ── PUMPS SET FLOW; LOAD SETS ΔT ─────────────────────────────────
            # The causal order, and it was backwards. Flow used to be the DEMAND
            # figure Q/(cp·ΔT) with ΔT read off a curve, then capped by surviving
            # pumping — so the loop answered "how much water would carry this heat"
            # rather than "how much water is moving". A demand figure cannot lose
            # flow, which is why faulting every pump once made the published header
            # go UP (7.8 → 14.9 l/s in the campaign): it only shrank the divisor the
            # flow was split across.
            #
            # This is a variable-primary plant — VFD pumps on a differential-
            # pressure setpoint, two-way coil valves, and an evaporator low-flow
            # interlock behind them. The pumps ride the ΔP loop down as the valves
            # close, floored at the minimum-flow bypass (CHW_MIN_FLOW_FRAC), and
            # whatever they deliver is the flow. ΔT is then a RESULT: Q = ṁ·cp·ΔT.
            #
            # Deliberately arithmetically identical to the old curve while the plant
            # is healthy — at duty d ≥ the bypass floor, flow = design × d cancels to
            # exactly CHW_DESIGN_DT_C, and below it to design × d/floor, which is
            # what chw_delta_t_c returned. Nothing is recalibrated; only the FAILURE
            # behaviour changes, because now a loop with no pumps has no flow and
            # therefore a collapsing, not an inflating, story.
            #
            # THERMAL duty here, not _plant_duty — that one is cooling-electrical
            # over running nameplate, and it is the heat in the water that strokes
            # the coil valves. Using the electrical figure breaks the cancellation
            # above and quietly moves every ΔT in the model.
            _enabled_kw = max(1e-6, self._plant_stage_on.get(dc, 1) * PLANT_MODULE_KW)
            _duty_th = itl_kw / _enabled_kw
            flow = (water_flow_lps(_enabled_kw, CHW_DESIGN_DT_C)
                    * chw_flow_frac(_duty_th)
                    * self._chw_pump_frac.get(dc, 1.0))
            d_t = (min(itl_kw / (CP_WATER_KJ_KGK * flow), CHW_MAX_DT_C)
                   if flow > 1e-6 else CHW_MAX_DT_C)
            ret = supply + d_t
            self._chw_supply_c[dc] = round(supply, 2)
            self._chw_return_c[dc] = round(ret, 2)
            self._chw_dt_c[dc] = round(d_t, 2)
            self._chw_flow_lps[dc] = round(flow, 2)

            # Condenser side carries the IT heat PLUS the compressor work that moved
            # it — that is why a condenser pump is always sized above its evaporator
            # counterpart. Chiller draw comes from this tick's power flow.
            chillers_all = list(kinds.get("chiller") or [])
            comp_kw = sum(self._plant_power_by_name.get(c, 0.0) for c in chillers_all)
            reject_kw = itl_kw + comp_kw
            # Heat only reaches the condenser loop through a chiller that is actually
            # transferring it. Kept SEPARATE from reject_kw, which still sizes the
            # condenser flow: the condenser pumps keep turning when the chillers
            # stop, so the loop still moves water — what collapses is the heat in
            # it, and therefore the evaporation. Without this the tower went on
            # consuming makeup for heat that never arrived: 5.28 → 5.24 l/min
            # through a cascade with every chiller stopped. Evaporation is the
            # plant's only consumable, so a figure that ignores whether the plant is
            # running is not modelling it at all.
            _run_ch = [c for c in chillers_all if c not in self._plant_standby_names]
            _moving = [c for c in _run_ch
                       if not self._is_faulted(c)
                       and c not in self._chiller_hp_lockout
                       and c not in self._chw_flow_interlock
                       and c not in self._plant_unpowered_names]
            transferred_kw = reject_kw * ((len(_moving) / len(_run_ch)) if _run_ch else 1.0)
            # The condenser range narrows at part load for the same reason the
            # chilled-water ΔT does: its pumps are VFD too and ride their own
            # minimum-flow floor, so below that point the loop recirculates and the
            # measured range collapses. Shaping both loops with one curve is not a
            # convenience — hold the condenser range fixed while the CHW ΔT narrows
            # and a lightly loaded plant reports LESS condenser flow than evaporator
            # flow, which is thermodynamically impossible (the condenser carries the
            # IT heat plus the compressor work that moved it).
            # The two loops run DIFFERENT control strategies, and the difference is
            # real rather than an inconsistency. The evaporator pumps ride a
            # DIFFERENTIAL-PRESSURE setpoint, so their flow floors at the bypass and
            # the measured ΔT collapses at light load. The condenser pumps ride a
            # condenser-ΔT setpoint — the BMS trims flow to hold the range while
            # optimising it against tower-fan energy — so here flow tracks the heat
            # being rejected and the RANGE is what holds near design.
            #
            # Which is why flow keeps its demand shape and is merely capped by
            # surviving pumping: with the pumps healthy the control loop genuinely
            # does deliver whatever the reject needs.
            # Flow tracks the heat at the DESIGN range, floored at the pumps' own
            # turndown. It used to size the flow with chw_delta_t_c() — the
            # EVAPORATOR loop's minimum-flow bypass curve, which collapses ΔT below
            # 35 % duty. Borrowing it here made the condenser RANGE the thing that
            # moved and the flow the thing that held, which is the opposite of the
            # control strategy described above, and since flow = Q/(cp·ΔT) a
            # collapsing range INFLATES flow. The duty it collapsed against is
            # _plant_duty — cooling-electrical over running nameplate — so the
            # published flow came out as reject_kw/duty, and because both terms rise
            # with load they cancelled: condenser flow went nearly load-independent
            # and inverted. Live, the DC rejecting 153.9 kW moved 12.6 l/s while the
            # one rejecting 111.4 kW moved 14.2 — ratio 0.887 against a reject ratio
            # of 1.382, and reject/duty predicts 0.880.
            #
            # The narrowing was guarding something real: hold the range fixed with no
            # floor and a lightly loaded plant reports LESS condenser than evaporator
            # flow, which is thermodynamically impossible — the condenser carries the
            # IT heat plus the compressor work that moved it. But the cause of that
            # was CHW flow being floored at its bypass while CW flow was free to fall
            # to nothing, so the fix belongs on the floor, not on the range. CW pumps
            # are VFD too and bottom out at the same turndown; sized off the STAGED
            # capacity because a staged-off train's pump is stopped, not slow.
            _cw_design_kw = _enabled_kw * (1.0 + 1.0 / CHILLER_COP_RATED)
            _cw_design_lps = water_flow_lps(_cw_design_kw, COND_DESIGN_RANGE_C)
            _cw_min_lps = _cw_design_lps * PUMP_MIN_SPEED
            cw_flow = max(water_flow_lps(reject_kw, COND_DESIGN_RANGE_C),
                          _cw_min_lps) * self._cw_pump_frac.get(dc, 1.0)
            # RANGE IS DERIVED FROM THE FLOW ACTUALLY MOVING. A ΔT-controlled loop
            # holds its setpoint only while the pumps can still deliver; once they
            # cannot, the control loop is out of authority and the range widens —
            # that widening IS the diagnostic. It used to be pinned at design
            # everywhere it was published (tower cell Cond_Water_In, both chiller
            # Cond_Return_Temp branches, the CWR header probe), so a condenser loop
            # with zero flow still advertised a healthy 5 K range on the wire while
            # the chillers behind it were latching out on head pressure.
            self._cond_range_c[dc] = round(
                min(reject_kw / (CP_WATER_KJ_KGK * cw_flow), COND_MAX_RANGE_C)
                if cw_flow > 1e-6 else COND_MAX_RANGE_C, 2)

            # ── Chillers: supply/return/setpoint/flow on the RUNNING machines ──
            # A staged-off machine's evaporator is isolated by its own stopped pump
            # and genuinely drifts — _compute_cond_loop deliberately leaves its
            # evaporator alone, and so do we.
            running_ch = [c for c in chillers_all
                          if c not in self._plant_standby_names
                          and c not in self._chiller_hp_lockout
                          and c not in self._plant_unpowered_names]
            if running_ch:
                per_ch = flow / len(running_ch)
                for name in running_ch:
                    ip = name
                    if not ip:
                        continue
                    pts = auto.setdefault(ip, {})
                    pts.update({"CHW_Supply_Temp": round(supply, 1),
                                "CHW_Return_Temp": round(ret, 1),
                                "CHW_Setpoint": round(CHW_SETPOINT_C, 1),
                                "CHW_Flow": round(per_ch, 1)})
                    # Off-setpoint annunciation, with a dwell so a stage change or a
                    # valve step cannot annunciate, and hysteresis so a plant sitting
                    # on the deadband cannot chatter.
                    #
                    # Only ever PUBLISHED as 1.0, never as 0.0: these auto points are
                    # merged over the operator's forced-alarm map, so writing an
                    # explicit zero would stamp out a fault the operator injected from
                    # the Limits tab. Dropping the key instead lets the engine's own
                    # 0.0 show through, which is the clear.
                    over = supply > (CHW_SETPOINT_C + self._CHW_HIGH_DB_C)
                    held = (self._chw_high_s.get(name, 0.0) + dt) if over else 0.0
                    self._chw_high_s[name] = held
                    if held >= self._CHW_HIGH_S:
                        self._chw_high_alarm.add(name)
                    elif supply <= (CHW_SETPOINT_C + self._CHW_CLEAR_DB_C):
                        self._chw_high_alarm.discard(name)
                    if name in self._chw_high_alarm:
                        pts["Alarm_HighCHWSupply"] = 1.0
            for name in chillers_all:
                if name not in running_ch:
                    self._chw_high_s.pop(name, None)
                    self._chw_high_alarm.discard(name)

            # ── Pumps: real flow, and head from the affinity law ──────────────
            trains = self._plant_trains_run.get(dc, [])
            chwps = [tr["chwp"] for tr in trains if tr.get("chwp")]
            cwps  = [tr["cwp"] for tr in trains if tr.get("cwp")]
            # EVERY pump in the DC, addressed by the loop its name leads with — not
            # just the ones sitting in a running train. A header spare is a train
            # member in nobody's train, so iterating the trains left it with no
            # published Flow at all and the BACnet engine's own base curve (22 l/s,
            # decayed by whatever alarm coupling applied) went out on the wire. A
            # faulted spare advertised 1.62 l/s while the header it feeds read 0.
            _pumps_all = [n for n in (kinds.get("pump") or [])]
            # DESIGN FLOW PER PUMP IS A HARDWARE CONSTANT — the installed plant's
            # design flow divided by how many trains were built, NOT by how many are
            # staged. Staging does not resize an impeller. It has to be the same
            # basis the power flow derives speed from (_duty_inst × trains_all /
            # trains_running), or the published speed and the published operating
            # point describe different machines: sized off STAGED capacity instead,
            # a pump at 50.9 % came out passing 0.68 of design, and the head read off
            # a curve position the drive was never at.
            #
            # Divided by ALL trains, not the turning ones, for the same reason: a
            # failed pump does not make its neighbours bigger machines, it pushes
            # them further out along the curve they already have. That is what makes
            # head droop diagnostic — once the survivors' speed caps at 100 %, extra
            # flow can only come from sliding down the curve.
            _n_trains_all = max(1, len(ctx["trains_by_dc"].get(dc, ())))
            _inst_kw = max(1e-6, self._plant_installed_mods.get(dc, 1)
                           * PLANT_MODULE_KW)
            _chw_pump_design = water_flow_lps(_inst_kw,
                                              CHW_DESIGN_DT_C) / _n_trains_all
            _cw_pump_design = water_flow_lps(
                _inst_kw * (1.0 + 1.0 / CHILLER_COP_RATED),
                COND_DESIGN_RANGE_C) / _n_trains_all
            for group, total, _pump_design_lps, _label, _pref in (
                    (chwps, flow, _chw_pump_design, "chw", "CHWP"),
                    (cwps, cw_flow, _cw_pump_design, "cw", "CWP")):
                _members = [n for n in _pumps_all if n.upper().startswith(_pref)]
                # TURNING, not merely present. A faulted or silent pump is not
                # moving water, so it must not take a share of the header flow —
                # and the survivors genuinely carry the whole of it. Leaving the
                # faulted ones in the split published an impeller at 0 % speed
                # passing 5.5 l/s, which is the gauge set contradicting itself.
                turning = [p for p in group
                           if p not in self._plant_standby_names
                           and p not in self._plant_unpowered_names
                           and not self._is_faulted(p)
                           and not self._run_unproven(p)]
                per_pump = (total / len(turning)) if turning else 0.0
                for name in _members:
                    ip = name
                    if not ip:
                        continue
                    if name not in turning:
                        # Stopped: no flow, no speed, and no differential across a
                        # still impeller. Suction is static head and survives.
                        auto.setdefault(ip, {}).update({
                            "Flow": 0.0, "Speed": 0.0, "VFD_Frequency": 0.0,
                            "Diff_Pressure": 0.0,
                            "Suction_Pressure": round(self._PUMP_SUCTION_KPA, 1),
                            "Discharge_Pressure": round(self._PUMP_SUCTION_KPA, 1),
                        })
                        continue
                    # SPEED IS THE DRIVE'S OWN COMMANDED FRACTION, not a figure
                    # back-derived from the draw. It was the latter, and the draw is
                    # the wrong thing to invert: _plant_power_by_name is NORMALISED
                    # so each DC's plant sums to cooling_electrical_w(), so inverting
                    # it answered "what share of the plant's electrical bill is this
                    # pump" rather than "how fast is it turning". Those coincide only
                    # while the staged unit count holds still. Stage a second cooling
                    # train and the same total splits two ways: both pumps' apparent
                    # speed fell while their flow did not, and a pump moving 8.0 l/s
                    # published 24.6 % against an idle plant's 29.1 % at 1.8 l/s —
                    # below the 35 % turndown floor a running pump cannot be under.
                    #
                    # Flow, head and kW still cannot disagree: head is derived from
                    # this same speed two lines down, and the draw the power flow
                    # gave the pump came from affinity_power_kw() on this very
                    # fraction before normalisation rescaled its magnitude.
                    npw = ctx["np_kw_by_name"].get(name, 0.0)
                    spd = self._plant_speed_by_name.get(name)
                    if spd is None:
                        # No cooling model for this unit (bare topology): fall back to
                        # the old derivation rather than publishing nothing.
                        kw = self._plant_power_by_name.get(name, 0.0)
                        spd = (affinity_speed_frac(kw, npw) if npw > 0
                               else vfd_speed_frac(duty, PUMP_MIN_SPEED))
                    # Round ONCE, then build the gauge set from the rounded figure.
                    # Publishing round(diff) beside round(suction + diff) double-rounds
                    # and the two disagree by 0.1 whenever diff lands near a .x5
                    # boundary — discharge − suction ≠ the published differential, on a
                    # gauge set whose whole job is to add up. Latent until a speed
                    # change happened to park diff on the boundary.
                    # Head from the pump's operating POINT — speed and flow — not
                    # from speed alone. A pump pinned at the turndown floor still
                    # rides its curve, so one passing 1.8 l/s and one passing
                    # 15.9 l/s develop different differential pressure; the
                    # affinity-only form published the same number for both.
                    _q_frac = ((per_pump / _pump_design_lps)
                               if _pump_design_lps > 1e-6 else None)
                    diff = round(self._PUMP_DIFF_KPA
                                 * pump_head_frac(spd, _q_frac), 1)
                    pts = auto.setdefault(ip, {})
                    pts.update({
                        "Flow": round(per_pump, 1),
                        "Diff_Pressure": diff,
                        "Suction_Pressure": round(self._PUMP_SUCTION_KPA, 1),
                        "Discharge_Pressure": round(self._PUMP_SUCTION_KPA + diff, 1),
                        # Publish SPEED from the same number that produced the head,
                        # rather than leaving the BACnet engine to derive its own from
                        # an EMA-smoothed copy of the draw. Both were "from power", but
                        # one was smoothed and one was not, so during a load transient
                        # the gauge set stopped adding up — head implying one speed
                        # while the Speed point read another, and at one capture head
                        # even fell while speed rose. One source of truth removes that.
                        "Speed": round(spd * 100.0, 1),
                        "VFD_Frequency": round(spd * 50.0, 1),
                    })
                    # Deliberately NO capacity alarm on the pump. It is tempting to
                    # raise Alarm_LowFlow when a pump is pinned at 100 % and the
                    # plant is still short, but that point already means something
                    # else and something worse: a real low-flow alarm is a blocked
                    # strainer, a shut isolation valve, a failed impeller — a fault
                    # that costs cooling. Overloading it with "healthy pump, too much
                    # load" would make the store stop scoring genuine pump faults as
                    # lost capacity. The high-load condition is already annunciated
                    # where it belongs (CHW supply off setpoint, above), and the
                    # pump's own Speed point shows it pinned at full.

            # ── Tower cells: makeup water tracks evaporation ──────────────────
            cells = [t for t in (kinds.get("cooling_tower") or [])
                     if t in (self._tower_running_now.get(dc) or set())]
            if cells:
                per_cell = makeup_flow_lpm(transferred_kw / len(cells))
                for name in cells:
                    ip = name
                    if ip:
                        auto.setdefault(ip, {})["Makeup_Flow"] = round(per_cell, 2)

        # ── CRAH discharge and return air ─────────────────────────────────────
        # DISCHARGE AIR is published here, as setpoint plus the chilled-water
        # shortfall, and _room_supply_temp READS IT BACK instead of adding the
        # penalty again on the way out. One source of truth.
        #
        # It used to be the other way round: the penalty lived only inside the room
        # model, so the discharge point never moved and the plant plane reported a
        # healthy hall through a total failure — 22.0 °C discharge against a 43.3 °C
        # return and 44.3 °C inlets, a 21 K air-side rise across a coil fed 19 °C
        # water. Discharge air is the unit's controlled variable and the first point
        # a DCIM trends; losing the coil's water is exactly what should raise it.
        #
        # The publish and the deletion downstream are ONE change. Doing either alone
        # is a bug: publish without deleting and the room is charged twice.
        for (dc, room), crahs in ctx["crah_by_room"].items():
            rk = (dc, room)
            inlet = self._room_inlet_c.get(rk)
            outlet = self._room_outlet_c.get(rk)
            # No measured exhaust warmer than the intake means the room's servers
            # have not reported one yet (cold start, or a hall with no live load).
            # There is no RETURN-AIR temperature to publish, and inventing one from
            # the intake alone would just restate the cold aisle. The valve below is
            # not gated on it: a coil's control loop runs on the water it is being
            # fed, whether or not the room has reported an exhaust yet.
            ret_air = (inlet + self._RETURN_MIX_FRAC * (outlet - inlet)
                       if inlet is not None and outlet is not None and outlet > inlet
                       else None)
            # Discharge = setpoint + the shortfall the plant is carrying. A healthy
            # plant holds setpoint, which is what makes any drift off it diagnostic.
            _sa = _SUPPLY_SETPOINT_C + self._chw_pen.get(dc, 0.0)
            self._supply_air_c[dc] = round(_sa, 2)
            for name in crahs:
                if (name in self._plant_standby_names
                        or name in self._plant_unpowered_names):
                    continue
                ip = name
                if not ip:
                    continue
                pts = auto.setdefault(ip, {})
                pts["Supply_Air_Temp"] = round(_sa, 1)
                if ret_air is not None:
                    pts["Return_Air_Temp"] = round(ret_air, 1)
                # CHILLED-WATER VALVE. A CRAH holds discharge setpoint by modulating
                # this valve, so its position is the output of a temperature control
                # loop — not, as it used to be, a restatement of the plant's demand
                # fraction. That version had the valve drifting 40 → 46 % while its
                # coil was fed 19 °C water and the hall ran to 42.7 °C; a real unit
                # drives to 100 % and stays there, and a valve pinned open with the
                # room still hot is the diagnostic that says the fault is upstream.
                #
                # Proportional on the shortfall the plant is carrying: on setpoint it
                # trims for load, and it saturates once the water is a couple of
                # degrees off. _chw_pen IS that shortfall, already integrated.
                _load_pos = 30.0 + 50.0 * max(0.0, min(1.0, self._plant_duty.get(dc, 0.0)))
                _starve = self._chw_pen.get(dc, 0.0) * self._CRAH_VALVE_GAIN
                pts["CHW_Valve"] = round(min(100.0, _load_pos + _starve), 1)
                # Return-air high, NOT discharge high: the unit is holding its
                # setpoint on the CHW valve, the hot aisle feeding it is too hot.
                # Raised only (never zeroed) so an operator-forced alarm survives.
                if ret_air is not None and ret_air >= self._CRAH_RETURN_ALARM_C:
                    pts["Alarm_HighReturnAir"] = 1.0

        # ── CDU: the technology-cooling loop follows the facility loop ────────
        # A CDU is a heat exchanger, not an independent chiller: it rejects cold-plate
        # heat into the SAME chilled water the CRAHs use. So when that water goes warm
        # the coolant must follow it. Left on its own random walk the point held
        # 32.0 °C through every cascade — including one with all three chillers latched
        # out and chilled water at 15–19 °C — while the die temperature on the servers
        # of that very loop DID take the penalty. The CDU and the servers it feeds were
        # telling different stories on the same wire.
        for dc, _all_cdus in (ctx.get("cdu_by_dc") or {}).items():
            _cdus = [c for c in _all_cdus if c not in self._plant_unpowered_names]
            if not _cdus:
                continue
            # Drift of the facility loop above its design temperature, attenuated by
            # the exchanger's approach (see _CDU_FOLLOW_FRAC).
            _drift = max(0.0, self._chw_supply_c.get(dc, CHW_SETPOINT_C) - CHW_SETPOINT_C)
            _tcs = self._CDU_TCS_SETPOINT_C + _drift * self._CDU_FOLLOW_FRAC
            for name in _cdus:
                ip = name
                if not ip:
                    continue
                pts = auto.setdefault(ip, {})
                pts["TCS_Supply_Temp"] = round(_tcs, 1)
                # Return sits a design range above supply; the secondary pumps are
                # VFD and track loop heat, so the range holds near design.
                pts["TCS_Return_Temp"] = round(_tcs + self._CDU_TCS_RANGE_C, 1)
                pts["TCS_Setpoint"] = round(self._CDU_TCS_SETPOINT_C, 1)
                # Facility-side valve opens as the CDU loses approach, same control
                # story as the CRAH coil valve above.
                pts["Facility_CHW_Valve"] = round(
                    min(100.0, 40.0 + _drift * self._CRAH_VALVE_GAIN), 1)

        # ── Plant header instruments ─────────────────────────────────────────
        # Every one of these points is already computed above or by the condenser
        # model; the probes just READ them. Publishing here (rather than letting
        # _step_device walk them like a rack air probe) is the whole point: a
        # thermowell on the CHW supply header reports the water the plant is
        # making, and the flow meter on the CHW main reports the water it is
        # moving, both of which move the moment the fleet does.
        readings: Dict[str, tuple] = {}
        for dc, probes in (ctx.get("probes_by_dc") or {}).items():
            cond = self._cond_water_c.get(dc, self._COND_DESIGN_C)
            src = {
                "chw_supply": self._chw_supply_c.get(dc),
                "chw_return": self._chw_return_c.get(dc),
                "cw_supply":  cond,
                "cw_return":  cond + self._cond_range_c.get(dc, self._COND_RANGE_C),
                # The basin IS the tower's cold well — the same water the condenser
                # supply header carries, which is why _compute_cond_loop publishes
                # Basin_Temp and Cond_Water_Out as one value on the cells.
                "ct_basin":   cond,
                "chw_flow":   self._chw_flow_lps.get(dc),
            }
            for name, role in probes:
                val = src.get(role)
                if val is not None:
                    readings[name] = (role, round(float(val), 2))
        self._probe_reading = readings

    def _room_supply_temp(self, device: "Device") -> float:
        """Cold-aisle supply temperature for a device's room.

        Healthy CRAHs set the room supply via their live Supply_Air_Temp (so a
        HighTemp fault, which warms that air, propagates to inlets). A CRAH that
        is OFF (Unit_Running=0) or has lost airflow delivers no cold air, so it is
        dropped from the average AND counts as lost cooling capacity — the room
        warms in proportion to the fraction of CRAHs down.

        The upstream CHW penalty arrives THROUGH that discharge reading, which
        _compute_chw_loop publishes as setpoint + penalty. It is deliberately not
        added again here.

        Capacity loss is a FRACTION per unit, not a headcount, because a fault
        can be partial: a clogged filter still blows cold air, just ~20% less of
        it. Sensible cooling is mass-flow × ΔT, so losing flow loses capacity and
        the room heats even though that CRAH's discharge temperature is fine —
        which is exactly why a filter alarm has to act here rather than by
        faking a Supply_Air_Temp rise (a real CRAH holds discharge setpoint on
        its CHW valve; what collapses is delivered kW, not supply temp)."""
        ctx = self._cooling_context()
        crahs = ctx["crah_by_room"].get((device.datacenter, device.room))
        if not crahs:
            return self._rack_supply_temp(device) + self._chw_pen.get(device.datacenter, 0.0)
        supplies, deficit = [], 0.0
        for n in crahs:
            pv = _plant_state_cache.get(n) or {}
            off = float(pv.get("Unit_Running", 1.0)) < 0.5
            noair = float(pv.get("Alarm_AirflowLoss", 0.0)) >= 0.5
            if off or noair:               # not delivering cold air
                deficit += 1.0
                continue
            # Partial derate. Gated on the alarm flag rather than read back from
            # the Airflow point: that point is a % of design carrying ±12 of
            # tick noise, so a healthy unit dipping to 68% would otherwise be
            # scored as a permanent 15% capacity loss.
            if float(pv.get("Filter_Dirty", 0.0)) >= 0.5:
                deficit += _CRAH_FILTER_DERATE
            sa = pv.get("Supply_Air_Temp")
            if sa is not None:
                supplies.append(float(sa))
        if supplies:
            base = sum(supplies) / len(supplies)
        else:
            # No live telemetry (BACnet stopped, or a unit that has not published
            # yet). Fall back to the same figure _compute_chw_loop publishes, so the
            # room reads identically with the telemetry plane up or down.
            base = self._supply_air_c.get(
                device.datacenter,
                _SUPPLY_SETPOINT_C + self._chw_pen.get(device.datacenter, 0.0))
        base += (deficit / len(crahs)) * 12.0    # lost capacity → room heats (all down → +12)
        # The CHW penalty is NOT added here. It is already inside the discharge air
        # above — published by _compute_chw_loop and read back — and adding it again
        # would charge the room twice for one shortfall. See the discharge-air block
        # in _compute_chw_loop; these two are one change.
        return base

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
        # Condenser loop BEFORE the CHW penalty: a chiller that trips or unloads
        # on head pressure has to be reflected in this same tick's cooling loss.
        self._compute_cond_loop()
        self._compute_chw_penalty()       # roll per-DC CHW penalty from upstream faults
        self._step_transfer()             # utility/genset transfer → who is energized
        # Which loads have lost every cord, BEFORE the flow sums them: a load whose
        # outlets are all open contributes 0 W to its PDU this tick, not next one.
        self._compute_unpowered_loads()
        self._compute_power_flow()        # live watts up the power graph (server→PDU→UPS→EV2)
        # Evaporator side LAST: it reads this tick's staging, duty and per-unit
        # draws, and merges its points into the same auto-point map the condenser
        # model filled, so both halves of the plant publish together.
        self._compute_chw_loop()
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
                # Autonomous plant protection (chiller head-pressure limit/trip)
                # rides the same per-IP override channel, but is merged into a
                # COPY: it must not leak into plant_alarm_overrides, which is the
                # operator's own forced-point map behind the Simulate Fault
                # menu's ACTIVE markers and the faulted-node canvas styling.
                _plant_ovr = self.plant_alarm_overrides
                # What the TICKER sees in the operator's forced-point map, logged on
                # change. The API accepts a force, stores it, and reports success
                # from its own thread — none of which proves the tick loop ever read
                # it. This line is the proof, and its ABSENCE is the useful signal:
                # if a fault was injected and nothing appears here, this loop is not
                # running. That is what a stalled ticker looks like from the outside,
                # and it presents as "fault injection randomly does nothing" — the
                # plant simply freezes, so the override is never read and every
                # published value holds its last reading. See
                # RuleEngine.get_rules_table_stats for the stall this caught.
                _ovr_now = {nm: sorted(pts) for nm, pts in _plant_ovr.items() if pts}
                if _ovr_now != getattr(self, "_ovr_seen_by_tick", None):
                    self._ovr_seen_by_tick = _ovr_now
                    log.warning("[StateStore] ticker sees operator overrides: %s",
                                _ovr_now or "{}")
                if self._plant_auto_points:
                    _plant_ovr = {ip: dict(pts) for ip, pts in _plant_ovr.items()}
                    for _nm, _pts in self._plant_auto_points.items():
                        _plant_ovr.setdefault(_nm, {}).update(_pts)
                self._bacnet_ctrl.tick(self._dt, self.metric_flags, self.metric_limits,
                                       _plant_ovr, live_kw_by_ip=self._ev2_live_kw,
                                       circuit_kw_by_ip=self._ev2_circuit_kw,
                                       plant_power_by_name=self._plant_power_by_name,
                                       plant_cop_by_name=self._plant_cop_by_name,
                                       plant_loadfrac_by_name=self._plant_loadfrac_by_name,
                                       plant_speed_by_name=self._plant_speed_by_name,
                                       plant_heat_by_name=self._cdu_loop_heat_kw,
                                       plant_standby_names=self._plant_standby_names,
                                       plant_unpowered_names=self._plant_unpowered_names)
                self._publish_plant_state()
            except Exception:
                log.exception("[StateStore] BACnet tick error")

        # Modbus register refresh. Runs AFTER the electrical/plant models above
        # have written this tick's ext state, so a master polling at 1 Hz reads
        # the same numbers the SNMP plane serves rather than last tick's.
        #
        # Only the PLANT unpowered set is passed, and that is deliberate. The v1
        # Modbus devices are electrical gear whose trip units and comm cards run
        # on control power, not on the bus they measure — a dead switchgear still
        # answers, reporting Bus_Energized = 0. Killing its comms on a bus outage
        # would hide the very reading an operator polls it for. Field devices on
        # an RTU trunk are the ones that genuinely go silent, and they answer
        # through the gateway as exception 0x0B.
        if self._modbus_ctrl:
            try:
                self._modbus_ctrl.tick(self._dt,
                                       unpowered_names=self._plant_unpowered_names)
            except Exception:
                log.exception("[StateStore] Modbus tick error")

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
        if topo is None:
            return broke
        for peer_id, edges in topo.get_adjacency(device.id):
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
            "ups_output_kw": 0.0,
            "ups_battery_status": "normal",
            "ups_input_voltage": random.uniform(396.0, 404.0),
            "ups_input_frequency": random.uniform(49.9, 50.1),
            "ups_fan_status": "ok",
            "ups_charger_status": "ok",
            "ups_rectifier_status": "ok",
            "ups_phase_status": "ok",
            "ups_bypass_status": "off",
            "ups_battery_health": random.uniform(92.0, 100.0),
            "ups_energy_kwh": 0.0,
            "ups_runtime_min": 8.0,
            "ups_on_battery_s": 0.0,
            "ups_autonomy_s": 0.0,
            "ups_battery_exhausted": False,
            "gen_fuel_pct": random.uniform(75.0, 95.0),
            "gen_run_hours": 0.0,
            "gen_status": "standby",
            "gen_load_pct": 0.0,
            "gen_kw": 0.0,
            "gen_runtime_min": 0.0,
            "gen_start_attempts": 0,
            "gen_was_running": False,
            "util_status": "normal",
            "util_voltage": 400.0,
            "util_current": 0.0,
            "util_frequency": 50.0,
            "util_kw": 0.0,
            "util_power_factor": 0.97,
            "util_energy_kwh": 0.0,
            "util_xfmr_loss_kw": 0.0,
            "util_va": 230.0, "util_vb": 230.0, "util_vc": 230.0,
            "util_ia": 0.0, "util_ib": 0.0, "util_ic": 0.0,
            "util_phase_imbalance": 0.0,
            "util_thd_v": 0.0, "util_thd_i": 0.0,
            "util_kvar": 0.0, "util_kva": 0.0, "util_peak_kw": 0.0,
            "swgr_source": "utility",
            "swgr_bus_status": "energized",
            "swgr_voltage": 400.0,
            "swgr_current": 0.0,
            "swgr_kw": 0.0,
            "swgr_load_pct": 0.0,
            "swgr_breaker_status": "closed",
            "swgr_va": 230.0, "swgr_vb": 230.0, "swgr_vc": 230.0,
            "swgr_ia": 0.0, "swgr_ib": 0.0, "swgr_ic": 0.0,
            "swgr_phase_imbalance": 0.0, "swgr_frequency": 50.0,
            "swgr_kvar": 0.0, "swgr_kva": 0.0, "swgr_power_factor": 0.97,
            "swgr_energy_kwh": 0.0,
            "ats_position": "normal",
            "ats_state": "utility",
            "ats_normal_available": "yes",
            "ats_emergency_available": "no",
            "ats_normal_voltage": 400.0,
            "ats_emergency_voltage": 0.0,
            "ats_normal_frequency": 50.0,
            "ats_emergency_frequency": 0.0,
            "ats_frequency": 50.0,
            "ats_transfer_count": 0,
            "ats_time_on_emergency": 0.0,
            "mcc_status": "energized",
            "mcc_tie": "open",
            "mcc_source": "normal",
            "mcc_voltage": 400.0,
            "mcc_current": 0.0,
            "mcc_kw": 0.0,
            "mcc_load_pct": 0.0,
            "mcc_va": 230.0, "mcc_vb": 230.0, "mcc_vc": 230.0,
            "mcc_ia": 0.0, "mcc_ib": 0.0, "mcc_ic": 0.0,
            "mcc_phase_imbalance": 0.0, "mcc_frequency": 50.0,
            "mcc_kvar": 0.0, "mcc_kva": 0.0, "mcc_power_factor": 0.88,
            "mcc_energy_kwh": 0.0,
            "mpp_status": "energized",
            "mpp_voltage": 400.0,
            "mpp_current": 0.0,
            "mpp_kw": 0.0,
            "mpp_load_pct": 0.0,
            "mpp_energy_kwh": 0.0,
            "mpp_va": 230.0, "mpp_vb": 230.0, "mpp_vc": 230.0,
            "mpp_ia": 0.0, "mpp_ib": 0.0, "mpp_ic": 0.0,
            "mpp_phase_imbalance": 0.0, "mpp_frequency": 50.0,
            "mpp_kvar": 0.0, "mpp_kva": 0.0, "mpp_power_factor": 0.92,
            "pdu_load": random.uniform(30.0, 60.0),
            "pdu_voltage": random.uniform(228.0, 232.0),
            "pdu_power_factor": random.uniform(0.92, 0.98),
            "pdu_phase_imbalance": random.uniform(0.0, 5.0),
            "pdu_outlet_status": "on",
            # Outlet indices switched OFF individually, 1-based to match the number
            # silk-screened on the strip. The strip-level pdu_outlet_status above
            # stays as the "whole PDU dead" case; this is the per-receptacle relay a
            # switched SKU actually gives you.
            "pdu_outlets_off": [],
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

        # ── Total power loss ────────────────────────────────────────────────────
        # An IT load whose every power feed has died — utility AND genset both failed
        # and the UPS exhausted — goes hard-dark: no OS/NOS, all uplinks down, until a
        # feed returns. A dead box cannot send a trap, so the loss shows only as its
        # neighbours' linkDown; on recovery it cold-boots (COLD_START). This is the
        # power-graph blackout reaching the IT layer. Distinct from a Redfish chassis
        # power-off (operator action, handled just below).
        if device.device_type in self._POWER_DEAD_TYPES:
            _admin_off = (device.device_type == DeviceType.SERVER
                          and getattr(device, "power_state", "On") == "Off")
            if not self._energized.get(device.id, True):
                if not ext.get("pwr_dead"):
                    ext["pwr_dead"] = True
                    # admin-off already broke this box's links; don't double-break.
                    ext["pwr_dead_links"] = (
                        [] if _admin_off else self._break_server_links(device))
                device.cpu_usage = 0
                device.memory_used = 0
                device.sys_uptime = 0
                if mf["cpu_temp"]:
                    device.cpu_temp = round(
                        max(0.0, device.cpu_temp - random.uniform(1.5, 3.5)), 1)
                for iface in device.interfaces:
                    iface.oper_status = 2
                return
            if ext.pop("pwr_dead", False):
                # A feed came back. If the box is still admin-off, leave it dark for
                # the Redfish block below; otherwise bring it back and cold-boot it.
                _links = ext.pop("pwr_dead_links", [])
                if not _admin_off:
                    for peer_id in _links:
                        self._topology.restore_link(device.id, peer_id, "production")
                        if self._link_cb:
                            try:
                                self._link_cb(device.id, peer_id, False)
                            except Exception:
                                pass
                    for iface in device.interfaces:
                        iface.oper_status = 1
                    device.sys_uptime = 0
                    if self._coldstart_cb:
                        try:
                            self._coldstart_cb(device)
                        except Exception:
                            pass

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
            device.sys_uptime += int(self._dt * 100)

        # CPU/ASIC temperature — IT gear only (real die/ASIC sensor). Power and
        # cooling devices (RPP/PDU/UPS/CRAH/chiller/pump) have no CPU, so they must
        # not carry a synthetic cpu_temp: it would surface over SNMP/Redfish and
        # previously drove a false HighTemperature trap (see trap_rules.HighTemperature).
        if mf["cpu_temp"] and device.device_type in self._CPU_BEARING_TYPES:
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
            # First-order thermal lag: the die relaxes toward its target rather than
            # snapping to it, so a short intake excursion (a transfer's dead-bus gap)
            # does not instantly spike cpu_temp and false-alarm, while a sustained
            # cooling loss still heats it to target and trips the trap. Symmetric, so
            # recovery lags too — the die cools over a minute, as real hardware does.
            _target = max(20.0, min(95.0, _cpu_t))
            _prev = getattr(device, "cpu_temp", None)
            if _prev is None:
                _prev = _target
            _alpha = min(1.0, self._dt / self._CPU_THERMAL_TAU_S)
            # Kept to 3 dp, NOT 1. At a 1 s tick _alpha is 1/150, so the step is
            # gap × 0.0067 — under 0.05 once the gap is below 7.5 K, and rounding
            # that to one decimal quantised it to ZERO. The die then stalled
            # permanently 7.5 K short of its target: a full cold-plate leak drove a
            # 100 °C target and cpu_temp parked at 87.5 forever, so the 90 °C
            # throttle point was unreachable by any fault and _apply_thermal_
            # protection could not fire outside a unit test that set cpu_temp by
            # hand. Two fault campaigns never reached it and blamed the inlet clamp.
            # The stall width also scales with the tick — halving dt doubles it —
            # which is the same tick-rate dependence _COOL_TAU_S exists to avoid.
            device.cpu_temp = round(_prev + (_target - _prev) * _alpha, 3)
            device.cpu_temp = self._num_limit("cpu_temp", device.cpu_temp)
            # A pinned (injected/overridden) cpu_temp wins, so the fan below ramps
            # to cool the hot die — the realistic response to a thermal fault.
            if _cputemp_pin is not None:
                device.cpu_temp = round(max(20.0, min(95.0, _cputemp_pin)), 1)
            # Protective response, once the die has settled for this tick. Without
            # it the temperature simply pinned at the ceiling and the runaway had no
            # end state.
            self._apply_thermal_protection(device)

        # Chassis fan speed — servers only. Single source of truth: Redfish _fans()
        # and the BMC SNMP dataset both read this, so the two agents agree.
        #
        # An AIR-cooled server's fans remove the whole load, and its control loop
        # chases the die: speed tracks cpu_temp, which already carries both load and
        # intake temperature.
        #
        # A DIRECT-TO-CHIP server's fans do not. The cold plate takes the CPU/GPU
        # heat into the coolant loop, leaving the fans only the ~30 % air fraction
        # (_DTC_AIR_FRACTION) that VRs, DIMMs, NICs and drives put into the air —
        # the same split the exhaust ΔT above already uses. Two consequences, and
        # both matter:
        #   * the RAMP is scaled by that fraction, so a loaded DLC box sits far
        #     below an air box at the same utilisation, which is the observable
        #     signature of DLC on the floor (and a chunk of its fan-power saving);
        #   * the DRIVER is load + INTAKE AIR, not cpu_temp. A cold-plate die is
        #     thermally decoupled from the fans — it barely moves with load, and
        #     spinning up for it would chase heat the fans cannot remove anyway.
        #     Intake is what actually threatens the air-cooled components, so a
        #     DLC chassis in a warming hall still ramps hard to save its DIMMs.
        if mf["fan_rpm"] and device.device_type == DeviceType.SERVER:
            # Absolute speeds come from the chassis: a 1U's 40 mm fans run 7-20 krpm,
            # a 4U's 92 mm fans 3-9 krpm for the same duty (fan_rpm_range). The curve
            # below computes a DUTY FRACTION and maps it onto that range, so the same
            # thermal logic gives a 1U and a 4U their own realistic speeds.
            _fan_pin = self._pin_value(device, "fan_rpm")
            if _fan_pin is not None:
                # An operator-forced speed IS the fault: a stalled rotor or a failed
                # fan board holds the chassis below its floor no matter what the
                # thermal loop asks for. Pinned last so the curve cannot overwrite it.
                _fan = max(0.0, _fan_pin)
            else:
                _lo, _hi = fan_rpm_range(getattr(device, "model_name", "") or "")
                if device.name in self._liquid_cooled_servers():
                    # Duty tracks load + intake, scaled by the air fraction. Min duty
                    # is lower than an air box's: with the CPU heat in the loop there
                    # is far less for the fans to hold at idle.
                    _duty = (device.cpu_usage * 0.45
                             + max(0.0, (device.inlet_temp or 22.0) - 22.0) * 3.0) / 45.0
                    _duty = max(0.0, min(1.0, _duty)) * _DTC_AIR_FRACTION
                    _lo *= _DTC_IDLE_FACTOR
                else:
                    # Duty tracks the die: min duty at 40 °C, full duty by 85 °C — the
                    # span between a warm idle and the Warning threshold.
                    _duty = max(0.0, min(1.0, (device.cpu_temp - 40.0) / 45.0))
                _fan = _lo + (_hi - _lo) * _duty + random.uniform(-90, 90)
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
            # PLANT HEADER INSTRUMENT (CHWS/CHWR/CWS/CWR/CTB/FLOW): reads the live
            # water loop, not room air. A thermowell in a chilled-water header has
            # no cold-aisle setpoint to revert to and no humidity to report, so the
            # air-probe walk below is skipped entirely and the RH/dew-point channels
            # are left at zero — there is no sensor behind them. Instrument noise is
            # the transmitter's own accuracy band (±0.1 K RTD, ±0.5 % mag meter),
            # not a random walk that would drift off the loop it is measuring.
            _probe = self._probe_reading.get(device.name)
        else:
            _probe = None

        if device.device_type == DeviceType.SENSOR and _probe is not None:
            _role, _val = _probe
            if _role == "chw_flow":
                device.airflow = round(max(0.0, _val * (1.0 + random.uniform(-0.005, 0.005))), 2)
                device.inlet_temp = 0.0
            else:
                device.inlet_temp = round(_val + random.uniform(-0.1, 0.1), 1)
                device.airflow = 0.0
            device.humidity = 0.0
            device.dewpoint = 0.0
            device.mid_temp = 0.0
            device.outlet_temp = 0.0

        elif device.device_type == DeviceType.SENSOR:
            # Ambient temperature: a rack environmental probe reads the SAME
            # cold-aisle air as the servers beside it, so it is driven from the room
            # supply plus its own rack-height gradient — the identical formula the
            # server inlet model uses a few lines down.
            #
            # It used to mean-revert to the supply SETPOINT and clamp at 30 °C, which
            # meant it could not observe a cooling failure at all: through every
            # cascade in the campaign the probes read 21.7–22.2 °C while co-located
            # servers read 36–44 °C, and the rack-probe average moved −0.03 °C during
            # a total loss of chilled water. On a real floor these are the FIRST
            # alarm on a CRAH failure, and SensorAmbientTempHigh (>32 °C) and
            # Critical (>38 °C) were unreachable by physics.
            if mf["sensor_ambient_temp"]:
                _base = self._room_supply_temp(device)
                _grad = min(max(device.rack_unit, 0), 42) / 42.0 * 3.0
                device.inlet_temp = round(max(15.0, min(45.0,
                    _base + _grad + random.uniform(-0.3, 0.3))), 1)
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
            elif metric == "fan_rpm":
                device.fan_rpm = int(max(0.0, min(25000.0, v)))
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
        "ups_input_voltage":   ("ext", "ups_input_voltage",   0.0, 500.0),
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

    def get_all_faulted(self) -> dict:
        """{device_id: [metric, …]} for every device with an injected ramp.

        Fleet-wide so the topology canvas can mark faulted nodes from ONE poll —
        per-device get_faults() would be a request per node (~1000 of them).

        Ramps that are CLEARING are included on purpose. A ramp is only the
        injection, not the alarm: the caller gates on the rule engine's in_alert
        so the node lights when the trap actually fires and goes out when the
        recovery fires. During a clear the metric is still above the recovery
        threshold for several ticks, and the alarm is genuinely still up then.
        """
        return {dev_id: sorted(ramps) for dev_id, ramps in self._fault_ramps.items() if ramps}

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
            # ups_status is scrubbed only when the rectifier actually has a source.
            # A UPS on battery because its ATS is mid-transfer (or has failed) is a
            # MODELLED condition, not a random-walk artefact, so it must survive the
            # scrub — otherwise the utility-outage sequence is invisible on SNMP.
            if self._ups_source_ok(device):
                setv("ups_status", "normal")
            setv("ups_battery_status", "normal")
            for c in ("ups_fan_status", "ups_charger_status",
                      "ups_rectifier_status", "ups_phase_status"):
                setv(c, "ok")
            setv("ups_bypass_status", "off")
        if dt in (DeviceType.PDU, DeviceType.FLOOR_PDU):
            setv("pdu_outlet_status", "on")
            # An OPERATOR-tripped breaker is a MODELLED condition, not a random-walk
            # artefact, so it survives the scrub — the same exemption ups_status gets
            # above, and for the same reason. Without it the strip went dead in
            # _compute_energized while this reset the point to "ok" every tick: a
            # DCIM saw an unpowered PDU reporting a healthy breaker, and the
            # ok->tripped rule never fired because the state never changed. The
            # override-backed PDU conditions do not need this — _apply_ext_overrides
            # re-pins them after the scrub — but a pducond has no such re-application.
            if "breaker_trip" not in self._pdu_conditions.get(device.id, ()):
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

        # Plant header instruments are exempt. This scrub exists to tame a random
        # WALK, and their readings are not walked — they are the live water loop,
        # computed in _compute_chw_loop. Clamping them would not suppress a phantom
        # alarm, it would FALSIFY a measurement: 35 °C condenser return water is a
        # healthy design condition, and squeezing it to 31.9 (the cold-aisle
        # ceiling) makes the plant page report a temperature the loop never had.
        # Forcing 30.1 % RH onto a thermowell is worse — an instrument that does
        # not exist reporting a value it cannot measure.
        if dt == DeviceType.SENSOR and _probe_role(device) is None:
            device.inlet_temp  = min(device.inlet_temp, 31.9)        # ambient > 32
            device.humidity    = min(max(device.humidity, 30.1), 69.9)  # <30 / >70
            device.dewpoint    = min(device.dewpoint, 20.9)          # > 21
            device.airflow     = min(max(device.airflow, 0.31), 3.49)   # <0.3 / >3.5
            device.mid_temp    = min(device.mid_temp, 37.9)          # > 38
            device.outlet_temp = min(device.outlet_temp, 44.9)       # > 45
        # Facility electrical loading (swgr_/mcc_/mpp_/gen_load_pct) is likewise NOT
        # scrubbed, for the same reason: it is computed from the live power graph,
        # not walked, so there is no spurious excursion to suppress. A board at 97 %
        # is at 97 % because the fleet put it there, and hiding that would suppress
        # exactly the behaviour this quiet mode is supposed to leave visible.

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
            clamp("ups_input_voltage", 360.1, 439.9, 400.0)        # >440 / <360
            clamp("ups_input_frequency", 49.1, 50.9, 50.0)         # OOR <49 / >51
            if st.get("ups_battery_health", 100.0) < 50.1:         # low health < 50
                st["ups_battery_health"] = 50.1; changed = True
        if dt in (DeviceType.PDU, DeviceType.FLOOR_PDU):
            if st.get("pdu_load", 0.0) > 79.9:                     # load high > 80
                st["pdu_load"] = 79.9; changed = True
            clamp("pdu_voltage", 200.1, 239.9, 230.0)              # >240 / <200
            if st.get("pdu_power_factor", 1.0) < 0.701:            # PF low < 0.70
                st["pdu_power_factor"] = 0.701; changed = True
            if st.get("pdu_phase_imbalance", 0.0) > 19.9:          # imbalance > 20
                st["pdu_phase_imbalance"] = 19.9; changed = True
            if st.get("pdu_outlet_current", 0.0) > 31.9:           # current > 32A breaker
                st["pdu_outlet_current"] = 31.9; changed = True
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
            "ups_output_kw": 0.0,
            "ups_battery_status": "normal",
            "ups_input_voltage": random.uniform(396.0, 404.0),
            "ups_input_frequency": random.uniform(49.9, 50.1),
            "ups_fan_status": "ok",
            "ups_charger_status": "ok",
            "ups_rectifier_status": "ok",
            "ups_phase_status": "ok",
            "ups_bypass_status": "off",
            "ups_battery_health": random.uniform(92.0, 100.0),
            "ups_energy_kwh": 0.0,
            "ups_runtime_min": 8.0,
            "ups_on_battery_s": 0.0,
            "ups_autonomy_s": 0.0,
            "ups_battery_exhausted": False,
            "gen_fuel_pct": random.uniform(75.0, 95.0),
            "gen_run_hours": 0.0,
            "gen_status": "standby",
            "gen_load_pct": 0.0,
            "gen_kw": 0.0,
            "gen_runtime_min": 0.0,
            "gen_start_attempts": 0,
            "gen_was_running": False,
            "util_status": "normal",
            "util_voltage": 400.0,
            "util_current": 0.0,
            "util_frequency": 50.0,
            "util_kw": 0.0,
            "util_power_factor": 0.97,
            "util_energy_kwh": 0.0,
            "util_xfmr_loss_kw": 0.0,
            "util_va": 230.0, "util_vb": 230.0, "util_vc": 230.0,
            "util_ia": 0.0, "util_ib": 0.0, "util_ic": 0.0,
            "util_phase_imbalance": 0.0,
            "util_thd_v": 0.0, "util_thd_i": 0.0,
            "util_kvar": 0.0, "util_kva": 0.0, "util_peak_kw": 0.0,
            "swgr_source": "utility",
            "swgr_bus_status": "energized",
            "swgr_voltage": 400.0,
            "swgr_current": 0.0,
            "swgr_kw": 0.0,
            "swgr_load_pct": 0.0,
            "swgr_breaker_status": "closed",
            "swgr_va": 230.0, "swgr_vb": 230.0, "swgr_vc": 230.0,
            "swgr_ia": 0.0, "swgr_ib": 0.0, "swgr_ic": 0.0,
            "swgr_phase_imbalance": 0.0, "swgr_frequency": 50.0,
            "swgr_kvar": 0.0, "swgr_kva": 0.0, "swgr_power_factor": 0.97,
            "swgr_energy_kwh": 0.0,
            "ats_position": "normal",
            "ats_state": "utility",
            "ats_normal_available": "yes",
            "ats_emergency_available": "no",
            "ats_normal_voltage": 400.0,
            "ats_emergency_voltage": 0.0,
            "ats_normal_frequency": 50.0,
            "ats_emergency_frequency": 0.0,
            "ats_frequency": 50.0,
            "ats_transfer_count": 0,
            "ats_time_on_emergency": 0.0,
            "mcc_status": "energized",
            "mcc_tie": "open",
            "mcc_source": "normal",
            "mcc_voltage": 400.0,
            "mcc_current": 0.0,
            "mcc_kw": 0.0,
            "mcc_load_pct": 0.0,
            "mcc_va": 230.0, "mcc_vb": 230.0, "mcc_vc": 230.0,
            "mcc_ia": 0.0, "mcc_ib": 0.0, "mcc_ic": 0.0,
            "mcc_phase_imbalance": 0.0, "mcc_frequency": 50.0,
            "mcc_kvar": 0.0, "mcc_kva": 0.0, "mcc_power_factor": 0.88,
            "mcc_energy_kwh": 0.0,
            "mpp_status": "energized",
            "mpp_voltage": 400.0,
            "mpp_current": 0.0,
            "mpp_kw": 0.0,
            "mpp_load_pct": 0.0,
            "mpp_energy_kwh": 0.0,
            "mpp_va": 230.0, "mpp_vb": 230.0, "mpp_vc": 230.0,
            "mpp_ia": 0.0, "mpp_ib": 0.0, "mpp_ic": 0.0,
            "mpp_phase_imbalance": 0.0, "mpp_frequency": 50.0,
            "mpp_kvar": 0.0, "mpp_kva": 0.0, "mpp_power_factor": 0.92,
            "pdu_load": random.uniform(30.0, 60.0),
            "pdu_voltage": random.uniform(228.0, 232.0),
            "pdu_power_factor": random.uniform(0.92, 0.98),
            "pdu_phase_imbalance": random.uniform(0.0, 5.0),
            "pdu_outlet_status": "on",
            # Outlet indices switched OFF individually, 1-based to match the number
            # silk-screened on the strip. The strip-level pdu_outlet_status above
            # stays as the "whole PDU dead" case; this is the per-receptacle relay a
            # switched SKU actually gives you.
            "pdu_outlets_off": [],
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
            # upsOutputPower is its own measured register in UPS-MIB, not something the
            # device derives from the percent-load it displays. Publish the real watts
            # flowing through the UPS, so the PUE denominator reads a clean instrument
            # value instead of inheriting the percentage's ±0.5 % jitter and 0.1 %
            # quantisation (which on a 1200 kW frame is ±6 kW of noise per unit).
            # Written outside the ups_output_load metric flag: switching off a display
            # metric must not freeze the power reading a DCIM polls.
            _ups_rated = self._power_context()["rated_w"].get(device.id, 0.0)
            _ups_thr   = self._through_live.get(device.id, 0.0)
            st["ups_output_kw"] = round(_ups_thr / 1000.0, 2) if _ups_rated > 0 else 0.0

            if mf["ups_status"]:
                # A UPS does not decide to go on battery — it drops there because its
                # input source died. That is the ATS's business, so the status is
                # slaved to the transfer sequence rather than random-walked. Battery
                # autonomy is finite: after _UPS_DESIGN_MIN at load the string is
                # into its low-battery alarm and the site is minutes from dropping.
                if self._ups_source_ok(device):
                    st["ups_on_battery_s"] = 0.0
                    st["ups_battery_exhausted"] = False
                    st["ups_status"] = "normal"
                else:
                    on_batt = st.get("ups_on_battery_s", 0.0) + self._dt
                    if on_batt <= self._dt:
                        # Freeze the autonomy at the instant of drop-out, from the
                        # runtime estimate for the load it is carrying right now. A
                        # lightly-loaded string lasts far longer than its full-load
                        # rating, which is why a 2N site can ride a long outage.
                        st["ups_autonomy_s"] = max(60.0, float(
                            st.get("ups_runtime_min", self._UPS_DESIGN_MIN)) * 60.0)
                    st["ups_on_battery_s"] = on_batt
                    autonomy = st.get("ups_autonomy_s", self._UPS_DESIGN_MIN * 60.0)
                    # Past autonomy the string is flat and the inverter drops the
                    # load. Downstream, that cord goes dead — on a dual-corded 2N
                    # feed the other side then carries everything.
                    st["ups_battery_exhausted"] = on_batt >= autonomy
                    st["ups_status"] = ("low_battery"
                                        if on_batt >= autonomy * self._UPS_LOW_BATT_FRAC
                                        else "on_battery")
            if mf["ups_status"]:
                st["ups_status"] = self._state_lock("ups_status", st["ups_status"])

            if mf["ups_output_load"]:
                _rated, _thr = _ups_rated, _ups_thr
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
                # UPS input is fed from its ATS off the 400 V (L-L) LV bus, like the
                # rest of the plant — mean-revert to 400 V, with the odd transient
                # sag/swell for the over/under-voltage alarm.
                v = st.get("ups_input_voltage", 400.0)
                v += (400.0 - v) * 0.05 + random.uniform(-1.5, 1.5)
                v = max(388.0, min(412.0, v))
                if random.random() < 0.003:
                    v = random.choice([random.uniform(436.0, 450.0),
                                       random.uniform(350.0, 364.0)])
                st["ups_input_voltage"] = round(self._num_limit("ups_input_voltage", v), 1)

            if mf["ups_input_frequency"]:
                # Input frequency is the SOURCE frequency, not a free walk: the shared
                # grid on utility, the genset governor on emergency, and 0 with no live
                # source (the UPS is on battery, inverter-isolated). Matches the ATS /
                # switchgear frequency for the same DC.
                _dc = getattr(device, "datacenter", None) or "?"
                if not self._ups_source_ok(device):
                    f = 0.0
                elif self._transfer.status(_dc).source == "emergency":
                    f = round(random.uniform(49.8, 50.2), 2)   # genset governor
                else:
                    f = round(self._grid_frequency(
                        getattr(device, "datacenter_city", None) or _dc), 2)
                st["ups_input_frequency"] = (
                    0.0 if f == 0.0 else round(self._num_limit("ups_input_frequency", f), 2))

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

            # Operating mode is DERIVED from the two states above, and it is
            # published into ext rather than computed at fact time so every plane
            # reads one value. It used to exist only inside _publish_facts, which
            # left the Modbus Operating_Mode register keyed on an ext entry that
            # was never written — so it served "online" forever, including on a
            # UPS that was on battery. A point that reports a plausible constant
            # is worse than one that reports nothing.
            st["ups_operating_mode"] = (
                "bypass" if st.get("ups_bypass_status", "off") == "on"
                else "battery" if st.get("ups_status", "normal") in ("on_battery", "low_battery")
                else "online")

            # Battery health (state-of-health %): slow monotonic decay, faster on fault
            if mf["ups_battery_health"]:
                hp = st.get("ups_battery_health", 100.0)
                decay = 0.05 if st.get("ups_battery_status", "normal") != "normal" else 0.002
                hp = max(0.0, hp - random.uniform(0.0, decay))
                st["ups_battery_health"] = round(self._num_limit("ups_battery_health", hp), 1)

            # Output energy accumulator (kWh): integrate ~3 kW frame at current % load,
            # assuming a ~1-minute tick interval. Flag gates accumulation (freeze counter).
            if mf["ups_energy_kwh"]:
                # Integrate the REAL watts through the UPS (kW/60 per ~1-min tick),
                # same convention as the util/MCC/MPP energy — not a 3 kW frame.
                kw_now = st.get("ups_output_kw", 0.0)
                st["ups_energy_kwh"] = round(st.get("ups_energy_kwh", 0.0)
                                             + kw_now / 60.0, 3)

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

        # ── Electrical upstream (utility feed / switchgear / ATS / MCC) ───
        if device.device_type in (DeviceType.UTILITY_FEED, DeviceType.SWITCHGEAR,
                                  DeviceType.ATS, DeviceType.MCC, DeviceType.MPP):
            self._step_electrical(device, st)

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
                # Mean-revert to 230 V nominal (matches the EV2 clamp's nominal) so
                # the rack bus doesn't drift; occasional transient sag/swell still
                # passes through for the over/under-voltage alarms.
                pv = st.get("pdu_voltage", 230.0)
                pv += (230.0 - pv) * 0.05 + random.uniform(-0.8, 0.8)
                pv = max(223.0, min(237.0, pv))
                if random.random() < 0.003:
                    pv = random.choice([random.uniform(241.0, 250.0),
                                        random.uniform(190.0, 199.0)])
                st["pdu_voltage"] = round(self._num_limit("pdu_voltage", pv), 1)

            if mf["pdu_power_factor"]:
                if _pdu_rated > 0:
                    # Active-PFC IT load: PF tracks load (poor light, ~unity full),
                    # the same curve the EV2 branch uses, so the PDU and its EV2
                    # clamp report a consistent power factor.
                    from core.bacnet_telemetry import _pf_from_load
                    _lf = _pdu_thr / _pdu_rated
                    pf = _pf_from_load(_lf) + random.uniform(-0.004, 0.004)
                    if random.random() < 0.003:
                        pf = random.uniform(0.50, 0.69)   # occasional bad-PSU event
                    pf = max(0.50, min(0.99, pf))
                else:
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
            # An OPERATOR-tripped breaker wins over both the spontaneous walk above
            # and the type-wide lock, and it does not clear itself — the strip is
            # de-energized in _compute_energized off this same state, so the point
            # and the power have to agree. A real branch breaker stays open until
            # somebody resets it.
            if "breaker_trip" in self._pdu_conditions.get(device.id, ()):
                st["pdu_breaker_status"] = "tripped"

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
                    # Live: single-phase equivalent I = P / (V·PF), matching the EV2
                    # branch CT model (one CT per rack PDU) so the PDU's own current
                    # and the EV2 clamp agree, and V·I·PF reconciles to the real draw.
                    _v = st.get("pdu_voltage", 230.0)
                    _pf = st.get("pdu_power_factor", 0.95)
                    oc = max(0.0, _pdu_thr / max(1.0, _v * _pf)
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
                    volt_now = st.get("pdu_voltage", 230.0)
                    cur_now  = st.get("pdu_outlet_current", 10.0)
                    pf_now   = st.get("pdu_power_factor", 0.95)
                    real_kw  = (volt_now * cur_now * pf_now) / 1000.0
                st["pdu_energy_kwh"] = round(st.get("pdu_energy_kwh", 0.0)
                                             + real_kw * self._dt / 3600.0, 3)

        # ── Rack environmental probe — publish its air readings by NAME ──────
        # A DPX2 plugs into a PDU's sensor port and is served from the PDU's
        # agent, which only knows its children by name. These readings live on
        # the Device object, so without publishing them here the PDU would have
        # nothing to render its own external-sensor table from.
        if device.device_type == DeviceType.SENSOR and not _probe_role(device):
            st["probe_inlet_c"] = round(float(device.inlet_temp), 1)
            st["probe_mid_c"] = round(float(device.mid_temp), 1)
            st["probe_outlet_c"] = round(float(device.outlet_temp), 1)
            st["probe_humidity_pct"] = round(float(device.humidity), 1)
            st["probe_dewpoint_c"] = round(float(device.dewpoint), 1)
            # Identity travels with the reading: the PDU renders its children by
            # name and needs to know which model each is (how many slots it
            # occupies and of what type) and where its chain position starts.
            st["probe_model"] = device.model_name
            st["probe_slot"] = int(getattr(device, "sensor_slot", 0) or 0)

        # Update module-level cache so snmprec_generator can read UPS/PDU states
        _ext_state_cache[name] = dict(st)

        # ── Plant header instrument — publish the loop reading it represents ──
        # Kept in ext_state (not just on the device's air-probe fields) so the
        # DCIM/UI and the rule engine can read a CHW supply thermowell as WATER
        # temperature rather than mistaking it for room ambient.
        _probe = self._probe_reading.get(name)
        if device.device_type == DeviceType.SENSOR and _probe is not None:
            _role, _ = _probe
            st["probe_role"] = _role
            if _role == "chw_flow":
                st["water_flow_lps"] = float(device.airflow)
                st.pop("water_temp", None)
            else:
                st["water_temp"] = float(device.inlet_temp)
                st.pop("water_flow_lps", None)
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

    # Facility electrical gear → (ext_state key prefix for load %, health key).
    # The ATS and the utility meter are deliberately absent: an ATS is a switch and
    # does not meter load, and a revenue meter has no rating to load against — both
    # already annunciate through their own dedicated paths (transfer traps / meter
    # points), and inventing a load % for them would be fiction.
    _ELEC_FACT_KEYS = {
        "switchgear": ("swgr_", "swgr_bus_status"),
        "mcc":        ("mcc_",  "mcc_status"),
        "mpp":        ("mpp_",  "mpp_status"),
        "generator":  ("gen_",  "gen_status"),
    }

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
                # A load that has lost all power is dark — no agent, publish nothing.
                if (device.device_type in self._POWER_DEAD_TYPES
                        and not self._energized.get(device.id, True)):
                    continue
                ext = self._ext_states.get(device.name, {})
                mem_pct = (device.memory_used / max(1, device.memory_total)) * 100.0
                disk_pct = (device.disk_used / max(1, device.disk_total)) * 100.0
                # Facility electrical gear reports its bus loading and health under a
                # per-type key prefix; normalise both onto one pair of metrics so the
                # rules read the same way for every board.
                _elec_pfx, _elec_st = self._ELEC_FACT_KEYS.get(
                    device.device_type.value, (None, None))
                _elec_load = float(ext.get(f"{_elec_pfx}load_pct", 0.0)) if _elec_pfx else 0.0
                _elec_state = str(ext.get(_elec_st, "")) if _elec_st else ""

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
                    fan_speed_pct=self._fan_speed_pct(device),
                    ups_status=ext.get("ups_status", "normal"),
                    ups_output_load=float(ext.get("ups_output_load", 0.0)),
                    ups_battery_status=ext.get("ups_battery_status", "normal"),
                    ups_input_voltage=float(ext.get("ups_input_voltage", 400.0)),
                    ups_input_frequency=float(ext.get("ups_input_frequency", 50.0)),
                    ups_fan_status=ext.get("ups_fan_status", "ok"),
                    ups_charger_status=ext.get("ups_charger_status", "ok"),
                    ups_rectifier_status=ext.get("ups_rectifier_status", "ok"),
                    ups_phase_status=ext.get("ups_phase_status", "ok"),
                    # Read the published value rather than re-deriving it: two
                    # copies of one rule drift, and the Modbus plane reads the
                    # published one.
                    ups_operating_mode=ext.get("ups_operating_mode", "online"),
                    ups_bypass_status=ext.get("ups_bypass_status", "off"),
                    ups_battery_health=float(ext.get("ups_battery_health", 100.0)),
                    ups_output_apparent_power=float(ext.get("ups_output_load", 0.0)) * 30.0,
                    ups_energy_kwh=float(ext.get("ups_energy_kwh", 0.0)),
                    pdu_load=float(ext.get("pdu_load", 0.0)),
                    pdu_voltage=float(ext.get("pdu_voltage", 230.0)),
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
                    water_temp=float(ext.get("water_temp", 0.0)),
                    water_flow_lps=float(ext.get("water_flow_lps", 0.0)),
                    elec_load_pct=_elec_load,
                    elec_status=_elec_state,
                    plant_status=self._plant_status(device),
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