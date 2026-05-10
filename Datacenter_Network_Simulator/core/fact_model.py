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

    # Power / UPS
    ups_status: str = "normal"  # normal | on_battery | low_battery

    # Routing protocol sessions
    bgp_sessions: List[BGPSessionFact] = field(default_factory=list)

    # Physical location (used for rack-correlation rules)
    rack_id: str = ""       # "{datacenter}:R{row}:RACK{num}" or ""
    datacenter: str = ""