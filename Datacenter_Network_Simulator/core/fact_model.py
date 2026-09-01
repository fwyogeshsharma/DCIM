"""
Fact Model — structured per-device telemetry snapshots fed into the rule engine.

Each DeviceFact is a point-in-time view of a single device's state.
The rule engine ingests these facts and evaluates rules against them.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


@dataclass
class InterfaceFact:
    index: int
    name: str
    oper_status: int        # 1=up, 2=down
    bandwidth_util: float = 0.0   # 0-100 %
    error_rate: float = 0.0       # errors/s


@dataclass
class BGPSessionFact:
    peer_addr: str
    state: str   # established | idle | active | connect | open_sent | open_confirm


@dataclass
class DeviceFact:
    """
    Complete telemetry snapshot for one device at one point in time.

    Built by DeviceStateStore on every tick and pushed into the rule engine.
    """
    device_id: str       # device.name (unique within topology)
    device_type: str     # router | switch | server | firewall | load_balancer
    model_name: str      # device model name e.g. "Raritan DPX2-T3H1"
    ip_address: str
    timestamp: float     # Unix epoch (seconds)

    # Resource metrics (0-100 %)
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    disk_usage: float = 0.0

    # Interfaces
    interfaces: List[InterfaceFact] = field(default_factory=list)

    # Environmental
    temperature: float = 0.0       # °C (CPU/ASIC temperature)
    ambient_temp: float = 0.0      # °C (datacenter ambient/inlet temp; used by sensors)
    humidity: float = 0.0          # % relative humidity
    dewpoint: float = 0.0          # °C dew-point (Vertiv Geist only)
    airflow: float = 0.0           # m/s (APC NetBotz only)
    mid_temp: float = 0.0          # °C mid-rack temp (Raritan DPX2-T3H1 probe 2)
    outlet_temp: float = 0.0       # °C exhaust temp (Raritan DPX2-T3H1 probe 3)
    # Chassis fan speed as a % of THIS chassis's minimum duty, not raw RPM. A raw
    # threshold cannot work fleet-wide: 3000 RPM is a healthy 4U at idle and a
    # stalled 1U, and a direct-to-chip server's minimum is lower again. Normalising
    # against the device's own expected floor makes one rule correct for every
    # chassis height and both cooling types. 100 = at minimum duty, <100 = below it,
    # ~0 = stopped. Servers only; everything else reports 100 (never alarms).
    fan_speed_pct: float = 100.0

    # Power / UPS
    ups_status: str = "normal"  # normal | on_battery | low_battery

    # UPS extended metrics (populated only for DeviceType.UPS)
    ups_output_load: float = 0.0          # % output load
    ups_battery_status: str = "normal"    # normal | failure | disconnected
    ups_input_voltage: float = 400.0      # V L-L (nominal 400V, 3-phase LV bus)
    ups_input_frequency: float = 50.0     # Hz (nominal 50Hz)
    ups_fan_status: str = "ok"            # ok | failure
    ups_charger_status: str = "ok"        # ok | failure
    ups_rectifier_status: str = "ok"      # ok | failure
    ups_phase_status: str = "ok"          # ok | failure
    ups_operating_mode: str = "online"    # online | battery | bypass | eco | standby
    ups_bypass_status: str = "off"        # off | on
    ups_battery_health: float = 100.0     # % state-of-health
    ups_output_apparent_power: float = 0.0  # VA
    ups_energy_kwh: float = 0.0           # cumulative output energy, kWh

    # PDU extended metrics (populated only for DeviceType.PDU / FLOOR_PDU)
    pdu_load: float = 0.0                 # % load
    pdu_voltage: float = 220.0            # V
    pdu_power_factor: float = 0.95        # 0-1
    pdu_phase_imbalance: float = 0.0      # %
    pdu_outlet_status: str = "on"         # on | off
    pdu_breaker_status: str = "ok"        # ok | tripped
    pdu_outlet_failure: str = "ok"        # ok | failed
    pdu_smoke: str = "no"                 # no | yes
    pdu_outlet_current: float = 0.0       # A, PER PHASE
    # Per-phase input breaker rating (A) from the SKU catalog; 0 = unknown.
    # Carried on the fact so an overload rule can measure a strip against its
    # OWN nameplate instead of a fleet-wide constant.
    pdu_breaker_rating_a: float = 0.0     # A
    # The receptacle a per-outlet condition names ("Outlet 12"), empty when the
    # condition belongs to the strip. Travels to the trap so the notification
    # can say which outlet, the way a real metered-by-outlet PDU does.
    pdu_outlet_instance: str = ""
    pdu_ground_fault: str = "no"          # no | yes
    pdu_frequency: float = 50.0           # Hz
    pdu_temperature: float = 0.0          # °C ambient inside PDU
    pdu_humidity: float = 0.0             # % RH inside PDU
    pdu_energy_kwh: float = 0.0           # cumulative energy, kWh

    # Plant header instruments (DeviceType.SENSOR whose model is a "Plant …" point).
    # A thermowell on a water header has nothing to say about room air, so these are
    # separate metrics — the cold-aisle rules are explicitly kept off these devices.
    water_temp: float = 0.0        # °C — CHW/CW supply/return, tower basin
    water_flow_lps: float = 0.0    # l/s — chilled-water main flow meter

    # Facility electrical gear (switchgear / MCC / MPP / generator). One pair of
    # metrics across all of them so one rule per device type covers that board,
    # with each type carrying its own thresholds.
    elec_load_pct: float = 0.0     # % of the board's / set's rating
    elec_status: str = ""          # energized | dead | fault | running | standby …

    # Cooling-plant machines (chiller / tower / pump / valve / CRAH / CDU). These
    # carry their health on BACnet binaries, which the SNMP plane could not see —
    # the trap rule set had 35 rules for sensors and ZERO for any plant device, so
    # a cascade that latched three chillers out produced no chiller trap. One
    # priority-ordered status string per machine (its WORST active condition, the
    # way a real plant MIB reports one), so a dozen rules cover the whole plant.
    plant_status: str = ""         # ok | hp_trip | flow_loss | vibration | …

    # Routing protocol sessions
    bgp_sessions: List[BGPSessionFact] = field(default_factory=list)

    # Physical location (used for rack-correlation rules)
    rack_id: str = ""       # "{datacenter}:R{row}:RACK{num}" or ""
    datacenter: str = ""