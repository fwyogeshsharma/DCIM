"""Pydantic request/response schemas for the REST API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── Generic ──────────────────────────────────────────────────────────────────

class OkResponse(BaseModel):
    ok: bool = True
    message: str = ""


class JobResponse(BaseModel):
    job_id: str
    operation: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    operation: str
    status: str
    progress_done: int
    progress_total: int
    message: str
    error: str
    result: Optional[Any] = None
    started_at: str
    finished_at: str


# ── Topology ──────────────────────────────────────────────────────────────────

class OpenTopologyRequest(BaseModel):
    path: str = Field(..., description="Absolute or relative path to .json topology file")


class TopologyInfoResponse(BaseModel):
    device_count: int
    link_count: int
    current_path: str
    devices: List[str]


class LinkActionRequest(BaseModel):
    src_id: str
    dst_id: str
    layer: str = "production"


# ── Binding ───────────────────────────────────────────────────────────────────

class AdapterSelectRequest(BaseModel):
    adapter: str = Field(..., description="Network adapter name, e.g. 'Ethernet'")


class SubnetMaskRequest(BaseModel):
    mask: str = Field("255.255.255.0", description="IPv4 subnet mask")


class BindingStatusResponse(BaseModel):
    selected_adapter: str
    subnet_mask: str
    bound_count: int
    bound_ips: List[str]
    gnmi_bound_count: int
    gnmi_bound_ips: List[str]
    active_job_id: Optional[str] = None


class AdaptersResponse(BaseModel):
    adapters: List[str]                          # adapter names — use these with POST /binding/adapter
    adapter_labels: Dict[str, str] = Field(default_factory=dict)  # name → display label


# ── SNMP Simulator ────────────────────────────────────────────────────────────

class TrapReceiverRequest(BaseModel):
    ip: str = Field("127.0.0.1")
    port: int = Field(162, ge=1, le=65535)


class SnmpStartRequest(BaseModel):
    port: int = Field(161, ge=1, le=65535, description="UDP port for snmpsim to listen on")


class SnmpStatusResponse(BaseModel):
    running: bool
    ready: bool
    pid: Optional[int] = None
    active_endpoints: List[str]
    datasets_generated: bool
    dataset_count: int
    trap_receiver_ip: str
    trap_receiver_port: int
    rule_engine_enabled: bool
    active_job_id: Optional[str] = None


# ── gNMI Simulator ────────────────────────────────────────────────────────────

class GnmiStartRequest(BaseModel):
    port: int = Field(50051, ge=1, le=65535, description="gRPC port for the gNMI server/proxy")


class GnmiStatusResponse(BaseModel):
    running: bool
    ready: bool
    proxy_running: bool
    port: Optional[int] = None
    active_targets: List[str]
    datasets_generated: bool
    dataset_count: int
    active_job_id: Optional[str] = None
    clients: List[Dict[str, Any]] = []


# ── Rules ─────────────────────────────────────────────────────────────────────

class RuleResponse(BaseModel):
    name: str
    enabled: bool
    description: str
    total_fired: int
    last_fired: str
    conditions: List[Dict[str, Any]]
    actions: List[str]
    severity: Optional[str] = None


class RulesTableResponse(BaseModel):
    rule_engine_enabled: bool
    total_fired_grand: int
    rules: List[RuleResponse]


# ── Traps ─────────────────────────────────────────────────────────────────────

class TrapRecord(BaseModel):
    timestamp: str
    device_id: Optional[str]
    device_name: Optional[str]
    device_ip: Optional[str]
    trap_type: Optional[str]
    display_name: Optional[str] = None
    severity: Optional[str] = None
    details: str
    rule_name: str
    iface_index: Optional[int]


class TrapsResponse(BaseModel):
    total: int
    traps: List[TrapRecord]


class SendTrapRequest(BaseModel):
    device_id: str
    trap_type: str = Field(..., description="TrapType enum name, e.g. LINK_DOWN, CPU_HIGH")
    peer_id: Optional[str] = Field(None, description="For LINK_DOWN/LINK_UP: peer device ID — also breaks/restores the topology link")
    kwargs: Dict[str, Any] = Field(default_factory=dict, description="Extra varbind kwargs")


# ── Devices ───────────────────────────────────────────────────────────────────

class IfaceStats(BaseModel):
    index: int
    name: str
    oper_status: int
    speed: int
    in_octets: int
    out_octets: int
    in_errors: int
    out_errors: int
    in_discards: int
    out_discards: int
    in_unicast_pkts: int
    out_unicast_pkts: int


class DeviceInfo(BaseModel):
    id: str
    name: str
    device_type: str
    vendor: str
    ip_address: str
    mgmt_ip: Optional[str] = None
    snmp_port: int
    gnmi_port: int
    interface_count: int
    cpu_usage: float
    memory_used: float
    memory_total: float = 0.0
    disk_used: float
    disk_total: float = 0.0
    sys_location: str
    sys_contact: str
    uptime: int
    model_name: Optional[str] = None
    os_name: Optional[str] = None
    os_version: Optional[str] = None
    # All-device environmental
    cpu_temp: Optional[float] = None
    inlet_temp: Optional[float] = None
    # Sensor-specific
    humidity: Optional[float] = None
    dewpoint: Optional[float] = None
    airflow: Optional[float] = None
    # UPS-specific
    ups_status: Optional[str] = None
    ups_output_load: Optional[float] = None
    ups_battery_status: Optional[str] = None
    ups_input_voltage: Optional[float] = None
    ups_input_frequency: Optional[float] = None
    ups_fan_status: Optional[str] = None
    ups_charger_status: Optional[str] = None
    ups_rectifier_status: Optional[str] = None
    ups_phase_status: Optional[str] = None
    # PDU-specific
    pdu_load: Optional[float] = None
    pdu_voltage: Optional[float] = None
    pdu_power_factor: Optional[float] = None
    pdu_phase_imbalance: Optional[float] = None
    pdu_outlet_status: Optional[str] = None
    pdu_breaker_status: Optional[str] = None
    pdu_outlet_failure: Optional[str] = None
    pdu_smoke: Optional[str] = None
    pdu_outlet_current: Optional[float] = None
    pdu_ground_fault: Optional[str] = None
    # Router/Firewall
    bgp_sessions_up: Optional[int] = None
    bgp_sessions_total: Optional[int] = None
    # Aggregated interface stats (network devices)
    total_rx_bytes: Optional[int] = None
    total_tx_bytes: Optional[int] = None
    total_errors: Optional[int] = None
    total_discards: Optional[int] = None
    flapping_count: Optional[int] = None
    interfaces_up: Optional[int] = None
    interfaces_total: Optional[int] = None
    iface_stats: List[IfaceStats] = []


class DevicesResponse(BaseModel):
    total: int
    devices: List[DeviceInfo]


class AddDeviceRequest(BaseModel):
    name: str
    device_type: str = Field(..., description="router | switch | server | firewall | load_balancer | ups | pdu | floor_pdu | oob_switch | sensor")
    vendor: str = Field("Cisco Systems", description="Full vendor name e.g. 'Cisco Systems', 'Juniper Networks'")
    ip_address: str
    model_name: str = ""
    mgmt_ip: str = ""
    snmp_port: int = 161
    gnmi_port: int = 57400
    interface_count: int = 4
    sys_contact: str = ""
    metrics_enabled: bool = True
    # Physical location
    country: str = ""
    datacenter_city: str = ""
    datacenter: str = ""
    room: str = ""
    rack_row: int = 0
    rack_num: int = 0
    rack_unit: int = 0


class EditDeviceRequest(BaseModel):
    name: Optional[str] = None
    vendor: Optional[str] = None
    snmp_port: Optional[int] = None
    gnmi_port: Optional[int] = None
    sys_location: Optional[str] = None
    sys_contact: Optional[str] = None