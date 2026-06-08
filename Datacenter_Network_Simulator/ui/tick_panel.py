"""Metrics Tick Panel — sidebar panel for controlling the DeviceStateStore ticker."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGroupBox, QCheckBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QFrame, QStackedWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

if TYPE_CHECKING:
    from core.device_state_store import DeviceStateStore

# ── metric / limit data ──────────────────────────────────────────────────────

_METRIC_GROUPS = [
    ("All Devices", [
        ("cpu_usage",      "CPU Usage %",
         "Random walk ±4 pp; 1% spike chance to >90%"),
        ("memory_used",    "Memory Used %",
         "Random walk; 0.5% spike chance to >85%"),
        ("disk_used",      "Disk Used %",
         "Growth-biased walk; capped 5–90% of disk total"),
        ("sys_uptime",     "System Uptime",
         "Advances by tick_interval each tick (centiseconds)"),
        ("cpu_temp",       "CPU Temperature",
         "20 + 0.42×cpu ± 1 °C, clamped 20–95 °C"),
        ("inlet_temp",     "Chassis Inlet Temp",
         "18 + 0.12×cpu ± 0.5 °C, clamped 15–55 °C (servers only)"),
        ("iface_octets",   "Interface Byte Counters",
         "in/out_octets +5K–150K per tick on every UP interface"),
        ("iface_errors",   "Interface Error Counters",
         "in_errors +1 (10%) / out_errors +1 (5%) per UP interface"),
        ("iface_discards", "Interface Discard Counters",
         "Scale with congestion (cpu>70: +0–10; cpu>50: 30% +0–3)"),
        ("interface_flap", "Interface Flapping",
         "0.2% per connected interface → DOWN; auto-recovers in 5 s"),
    ]),
    ("Sensor Devices Only", [
        ("sensor_ambient_temp", "Inlet / Ambient Temp",      "±0.3 °C walk, clamped 15–35 °C (independent of CPU load)"),
        ("humidity",            "Ambient Humidity",          "±1.5 %RH walk, clamped 10–90 %"),
        ("dewpoint",            "Dew Point  ⓘ",              "Derived from inlet/ambient temp + humidity"),
        ("airflow",             "Airflow Speed",             "±0.15 m/s walk, clamped 0.2–4.0 m/s (NetBotz only)"),
        ("mid_temp",            "Mid-Rack Temp",             "Raritan DPX2-T3H1 only — tracks ambient +3–7 °C"),
        ("outlet_temp",         "Exhaust Temp",              "Raritan DPX2-T3H1 only — tracks ambient +8–14 °C"),
        ("water_detection",     "Water Detection",           "Raritan DPX2-CC2 only — 0.05% wet; clears 20%"),
    ]),
    ("UPS Devices Only", [
        ("ups_status",           "UPS Power Status",        "normal → on_battery (0.1%) → low_battery"),
        ("ups_output_load",      "Output Load %",           "±3% walk; 0.5% spike >90%"),
        ("ups_battery_status",   "Battery Hardware Status", "normal / failure (0.05%) / disconnected"),
        ("ups_input_voltage",    "Input Voltage",           "±2 V walk; 0.3% spike outside 200–240 V"),
        ("ups_input_frequency",  "Input Frequency",         "±0.05 Hz walk; 0.2% spike outside 49.5–50.5 Hz"),
        ("ups_fan_status",       "Cooling Fan Status",      "ok / failure — 0.1% chance; recovers 15%"),
        ("ups_charger_status",   "Battery Charger Status",  "ok / failure — 0.1% chance; recovers 15%"),
        ("ups_rectifier_status", "Rectifier Status",        "ok / failure — 0.1% chance; recovers 15%"),
        ("ups_phase_status",     "Input Phase Status",      "ok / failure — 0.1% chance; recovers 15%"),
        ("ups_bypass_status",    "Bypass Path Status",      "off / on — 0.08% to bypass; recovers 12%"),
        ("ups_battery_health",   "Battery Health %",        "% state-of-health; slow decay (faster if battery faulted)"),
        ("ups_energy_kwh",       "Energy Output (kWh)  ⓘ",  "Derived: cumulative ∫(output_load% × 3kW) dt per tick"),
    ]),
    ("PDU / Floor PDU Devices Only", [
        ("pdu_load",            "PDU Load %",              "±3% walk; 0.4% spike >80%"),
        ("pdu_voltage",         "Input Voltage",           "±2 V walk; 0.3% spike outside 205–235 V"),
        ("pdu_power_factor",    "Power Factor",            "±0.02 walk; 0.3% dip <0.70"),
        ("pdu_phase_imbalance", "Phase Imbalance %",       "±1% walk; 0.3% spike >20%"),
        ("pdu_outlet_status",   "Outlet Power Status",     "on/off — 0.1% flip; recovers 30%"),
        ("pdu_breaker_status",  "Circuit Breaker Status",  "ok/tripped — 0.1% trip; recovers 25%"),
        ("pdu_outlet_failure",  "Outlet Hardware Failure", "ok/failed — 0.1%; recovers 25%"),
        ("pdu_smoke",           "Smoke Detection",         "no/yes — 0.01% chance; clears 5%"),
        ("pdu_outlet_current",  "Outlet Current",          "±1 A walk; 0.3% spike >20 A"),
        ("pdu_ground_fault",    "Ground Fault Status",     "no/yes — 0.05% chance; clears 20%"),
        ("pdu_frequency",       "Input Frequency",         "±0.05 Hz walk; 0.2% spike outside 49.5–50.5 Hz"),
        ("pdu_temperature",     "Ambient Temperature",     "±0.3 °C walk, clamped 15–45 °C"),
        ("pdu_humidity",        "Ambient Humidity",        "±1 %RH walk, clamped 10–90 %"),
        ("pdu_energy_kwh",      "Energy Output (kWh)  ⓘ",  "Derived: cumulative ∫(Real Power kW) dt per tick"),
    ]),
    ("Router / Firewall Devices Only", [
        ("bgp_sessions", "BGP Session State", "established → idle (0.5%) → established (15%)"),
    ]),
]

_LIMIT_GROUPS = [
    ("All Devices", [
        ("cpu_usage",  "CPU Usage %",             "num",  0,   100,  1,   0, " %",    0,   100),
        ("memory_pct", "Memory Used %",           "num",  0,   100,  1,   0, " %",    0,   100),
        ("disk_pct",   "Disk Used %",             "num",  0,   100,  1,   0, " %",    0,   100),
        ("cpu_temp",   "CPU Temperature",         "num", 20,    95,  0.5, 1, " °C",  20,    95),
        ("inlet_temp", "Chassis Inlet Temp (servers)", "num", 15, 55, 0.5, 1, " °C", 15, 55),
    ]),
    ("Sensor Devices", [
        ("sensor_ambient_temp", "Inlet / Ambient Temp",      "num", 15, 35, 0.5, 1, " °C", 15, 35),
        ("humidity",            "Ambient Humidity",          "num", 10,  90, 1.0, 1, " %",   10,  90),
        ("airflow",             "Airflow Speed (NetBotz)",   "num",  0,   5, 0.1, 2, " m/s", 0.2, 4.0),
        ("mid_temp",            "Mid-Rack Temperature",      "num", 15,  55, 0.5, 1, " °C",  15,  55),
        ("outlet_temp",         "Exhaust Temperature",       "num", 15,  65, 0.5, 1, " °C",  15,  65),
        ("water_detection",     "Water Detection (CC2)",     "state", ["dry", "wet"]),
    ]),
    ("UPS Devices", [
        ("ups_output_load",     "Output Load %",           "num",   0,   100, 1.0, 1, " %",    0,   100),
        ("ups_input_voltage",   "Input Voltage",           "num", 200,   260, 1.0, 1, " V",  200,   240),
        ("ups_input_frequency", "Input Frequency",         "num",  47,    53, 0.1, 2, " Hz", 49.5, 50.5),
        ("ups_status",          "UPS Power Status",        "state", ["normal", "on_battery", "low_battery"]),
        ("ups_battery_status",  "Battery Hardware Status", "state", ["normal", "failure", "disconnected"]),
        ("ups_fan_status",      "Cooling Fan Status",      "state", ["ok", "failure"]),
        ("ups_charger_status",  "Battery Charger Status",  "state", ["ok", "failure"]),
        ("ups_rectifier_status","Rectifier Status",        "state", ["ok", "failure"]),
        ("ups_phase_status",    "Input Phase Status",      "state", ["ok", "failure"]),
        ("ups_battery_health",  "Battery Health %",        "num",   0,   100, 1.0, 1, " %", 0, 100),
        ("ups_bypass_status",   "Bypass Path Status",      "state", ["off", "on"]),
    ]),
    ("PDU Devices", [
        ("pdu_load",           "PDU Load %",              "num",   0, 100, 1.0, 1, " %",   0, 100),
        ("pdu_voltage",        "Input Voltage",           "num", 190, 250, 1.0, 1, " V", 205, 235),
        ("pdu_outlet_current", "Outlet Current",          "num",   0,  30, 1.0, 1, " A",   0,  20),
        ("pdu_outlet_status",  "Outlet Power Status",     "state", ["on", "off"]),
        ("pdu_breaker_status", "Circuit Breaker Status",  "state", ["ok", "tripped"]),
        ("pdu_outlet_failure", "Outlet Hardware Failure", "state", ["ok", "failed"]),
        ("pdu_smoke",          "Smoke Detection",         "state", ["no", "yes"]),
        ("pdu_ground_fault",   "Ground Fault Status",     "state", ["no", "yes"]),
        ("pdu_frequency",      "Input Frequency",         "num",  47,   53, 0.1, 2, " Hz", 49.5, 50.5),
        ("pdu_temperature",    "Ambient Temperature",     "num",  15,   45, 0.5, 1, " °C",   15,   45),
        ("pdu_humidity",       "Ambient Humidity",        "num",  10,   90, 1.0, 1, " %",    10,   90),
    ]),
    ("Router / Firewall", [
        ("bgp_sessions", "BGP Session State", "state", ["established", "idle"]),
    ]),
]


# ── Verdigris EV2 + chiller-plant metrics / limits (mirror web TickPanel) ─────
# Flag/limit keys MUST match those registered in core/device_state_store.py.
# Plant keys are "<device_type>:<PointName>"; EV2 metrics are grouped.

# Metric flag groups: EV2 grouped, plant per-point.
_EV2_FLAG_ROWS = [
    ("ev2_power",         "Power (kW / V / A)",              "Panel total kW, phase voltages & currents"),
    ("ev2_energy",        "Energy (kWh)",                    "Panel + per-circuit kWh accumulators"),
    ("ev2_power_quality", "Power Quality (THD / Harmonics)", "Voltage/current THD + harmonics 3/5/7/9"),
    ("ev2_freq_pf",       "Frequency & Power Factor",        "Mains frequency + panel power factor"),
    ("ev2_alarms",        "Panel Alarms",                    "Overcurrent, imbalance, high THD, phase loss, sensor fault"),
    ("ev2_circuits",      "Per-Circuit Metrics",             "Per-circuit current, kW, PF, THD"),
]

# (gid, title, [(point, label)])
_PLANT_FLAG_GROUPS = [
    ("crah", "CRAH", [
        ("Supply_Air_Temp", "Supply Air Temp"), ("Return_Air_Temp", "Return Air Temp"),
        ("Setpoint", "Setpoint"), ("Fan_Speed", "Fan Speed"), ("CHW_Valve", "CHW Valve"),
        ("Cooling_Capacity", "Cooling Capacity"), ("Supply_Humidity", "Supply Humidity"),
        ("Airflow", "Airflow"), ("Fan_Power", "Fan Power"), ("Run_Hours", "Run Hours"),
        ("Unit_Running", "Unit Running"), ("Alarm_HighTemp", "Alarm: High Temp"),
        ("Alarm_AirflowLoss", "Alarm: Airflow Loss"), ("Filter_Dirty", "Filter Dirty"),
    ]),
    ("chiller", "Chiller", [
        ("CHW_Supply_Temp", "CHW Supply Temp"), ("CHW_Return_Temp", "CHW Return Temp"),
        ("CHW_Setpoint", "CHW Setpoint"), ("CHW_Flow", "CHW Flow"),
        ("Cond_Supply_Temp", "Cond Supply Temp"), ("Cond_Return_Temp", "Cond Return Temp"),
        ("Compressor_Load", "Compressor Load"), ("Active_Power", "Active Power"),
        ("Cooling_Capacity", "Cooling Capacity"), ("COP", "COP"),
        ("Evap_Pressure", "Evap Pressure"), ("Cond_Pressure", "Cond Pressure"),
        ("Run_Hours", "Run Hours"), ("Chiller_Running", "Chiller Running"),
        ("Alarm_HighPressure", "Alarm: High Pressure"), ("Alarm_LowEvapTemp", "Alarm: Low Evap Temp"),
        ("Alarm_FlowLoss", "Alarm: Flow Loss"),
    ]),
    ("pump", "Pump", [
        ("Speed", "Speed"), ("Flow", "Flow"), ("Discharge_Pressure", "Discharge Pressure"),
        ("Suction_Pressure", "Suction Pressure"), ("Diff_Pressure", "Diff Pressure"),
        ("Motor_Power", "Motor Power"), ("Motor_Temp", "Motor Temp"),
        ("VFD_Frequency", "VFD Frequency"), ("Run_Hours", "Run Hours"),
        ("Run_Status", "Run Status"), ("Alarm_Fault", "Alarm: Fault"), ("Alarm_LowFlow", "Alarm: Low Flow"),
    ]),
    ("cooling_tower", "Cooling Tower", [
        ("Fan_Speed", "Fan Speed"), ("Basin_Temp", "Basin Temp"),
        ("Cond_Water_In", "Cond Water In"), ("Cond_Water_Out", "Cond Water Out"),
        ("Fan_Power", "Fan Power"), ("Basin_Level", "Basin Level"),
        ("Makeup_Flow", "Makeup Flow"), ("Vibration", "Vibration"), ("Run_Hours", "Run Hours"),
        ("Fan_Status", "Fan Status"), ("Alarm_HighVibration", "Alarm: High Vibration"),
        ("Alarm_LowBasin", "Alarm: Low Basin"),
    ]),
    ("valve", "Valve", [
        ("Position", "Position"), ("Commanded_Position", "Commanded Position"),
        ("Actuator_Temp", "Actuator Temp"), ("Status_Modulating", "Status Modulating"),
        ("Alarm_ActuatorFault", "Alarm: Actuator Fault"),
    ]),
]

_METRIC_GROUPS.append(("Verdigris EV2", _EV2_FLAG_ROWS))
for _gid, _title, _points in _PLANT_FLAG_GROUPS:
    _METRIC_GROUPS.append((_title, [
        (f"{_gid}:{_p}", _lbl, f"{_lbl} — present-value updated each tick")
        for _p, _lbl in _points
    ]))

# Limit specs: numeric clamp + binary off/on force.
# (gid, title, num=[(point,label,suffix,absMin,absMax,step,dec)], bin=[(point,label)])
_PLANT_LIMIT_SPEC = [
    ("ev2", "Verdigris EV2", [
        ("Panel_Total_kW", "Panel Total kW", " kW", 0, 200, 1, 0),
        ("Voltage_PhA", "Voltage Ph A", " V", 200, 260, 1, 1),
        ("Voltage_PhB", "Voltage Ph B", " V", 200, 260, 1, 1),
        ("Voltage_PhC", "Voltage Ph C", " V", 200, 260, 1, 1),
        ("Current_PhA", "Current Ph A", " A", 0, 200, 1, 1),
        ("Current_PhB", "Current Ph B", " A", 0, 200, 1, 1),
        ("Current_PhC", "Current Ph C", " A", 0, 200, 1, 1),
        ("Line_Frequency", "Line Frequency", " Hz", 45, 65, 0.05, 2),
        ("Panel_PF", "Power Factor", "", 0, 1, 0.01, 2),
        ("Voltage_THD", "Voltage THD", " %", 0, 50, 0.5, 1),
        ("Current_THD", "Current THD", " %", 0, 50, 0.5, 1),
    ], [
        ("Alarm_Overcurrent", "Alarm: Overcurrent"), ("Alarm_VoltageImbalance", "Alarm: V Imbalance"),
        ("Alarm_HighTHD", "Alarm: High THD"), ("Alarm_PhaseLoss", "Alarm: Phase Loss"),
        ("Alarm_SensorFault", "Alarm: Sensor Fault"),
    ]),
    ("crah", "CRAH", [
        ("Supply_Air_Temp", "Supply Air Temp", " °C", 0, 40, 0.5, 1),
        ("Return_Air_Temp", "Return Air Temp", " °C", 0, 50, 0.5, 1),
        ("Setpoint", "Setpoint", " °C", 10, 30, 0.5, 1),
        ("Fan_Speed", "Fan Speed", " %", 0, 100, 1, 0),
        ("CHW_Valve", "CHW Valve", " %", 0, 100, 1, 0),
        ("Cooling_Capacity", "Cooling Capacity", " %", 0, 100, 1, 0),
        ("Supply_Humidity", "Supply Humidity", " %", 0, 100, 1, 0),
        ("Airflow", "Airflow", " %", 0, 100, 1, 0),
        ("Fan_Power", "Fan Power", " kW", 0, 50, 0.1, 2),
        ("Run_Hours", "Run Hours", " h", 0, 100000, 100, 0),
    ], [
        ("Unit_Running", "Unit Running"), ("Alarm_HighTemp", "Alarm: High Temp"),
        ("Alarm_AirflowLoss", "Alarm: Airflow Loss"), ("Filter_Dirty", "Filter Dirty"),
    ]),
    ("chiller", "Chiller", [
        ("CHW_Supply_Temp", "CHW Supply Temp", " °C", 0, 20, 0.5, 1),
        ("CHW_Return_Temp", "CHW Return Temp", " °C", 0, 25, 0.5, 1),
        ("CHW_Setpoint", "CHW Setpoint", " °C", 4, 12, 0.5, 1),
        ("CHW_Flow", "CHW Flow", " L/s", 0, 50, 0.5, 1),
        ("Cond_Supply_Temp", "Cond Supply Temp", " °C", 0, 50, 0.5, 1),
        ("Cond_Return_Temp", "Cond Return Temp", " °C", 0, 50, 0.5, 1),
        ("Compressor_Load", "Compressor Load", " %", 0, 100, 1, 0),
        ("Active_Power", "Active Power", " kW", 0, 600, 1, 0),
        ("Cooling_Capacity", "Cooling Capacity", " kW", 0, 1000, 1, 0),
        ("COP", "COP", "", 0, 10, 0.1, 2),
        ("Evap_Pressure", "Evap Pressure", " kPa", 0, 800, 1, 0),
        ("Cond_Pressure", "Cond Pressure", " kPa", 0, 1500, 1, 0),
        ("Run_Hours", "Run Hours", " h", 0, 100000, 100, 0),
    ], [
        ("Chiller_Running", "Chiller Running"), ("Alarm_HighPressure", "Alarm: High Pressure"),
        ("Alarm_LowEvapTemp", "Alarm: Low Evap Temp"), ("Alarm_FlowLoss", "Alarm: Flow Loss"),
    ]),
    ("pump", "Pump", [
        ("Speed", "Speed", " %", 0, 100, 1, 0),
        ("Flow", "Flow", " L/s", 0, 50, 0.5, 1),
        ("Discharge_Pressure", "Discharge Pressure", " kPa", 0, 800, 1, 0),
        ("Suction_Pressure", "Suction Pressure", " kPa", 0, 400, 1, 0),
        ("Diff_Pressure", "Diff Pressure", " kPa", 0, 600, 1, 0),
        ("Motor_Power", "Motor Power", " kW", 0, 100, 0.1, 2),
        ("Motor_Temp", "Motor Temp", " °C", 0, 120, 0.5, 1),
        ("VFD_Frequency", "VFD Frequency", " Hz", 0, 60, 0.5, 1),
        ("Run_Hours", "Run Hours", " h", 0, 100000, 100, 0),
    ], [
        ("Run_Status", "Run Status"), ("Alarm_Fault", "Alarm: Fault"), ("Alarm_LowFlow", "Alarm: Low Flow"),
    ]),
    ("cooling_tower", "Cooling Tower", [
        ("Fan_Speed", "Fan Speed", " %", 0, 100, 1, 0),
        ("Basin_Temp", "Basin Temp", " °C", 0, 50, 0.5, 1),
        ("Cond_Water_In", "Cond Water In", " °C", 0, 50, 0.5, 1),
        ("Cond_Water_Out", "Cond Water Out", " °C", 0, 50, 0.5, 1),
        ("Fan_Power", "Fan Power", " kW", 0, 50, 0.1, 2),
        ("Basin_Level", "Basin Level", " %", 0, 100, 1, 0),
        ("Makeup_Flow", "Makeup Flow", " L/min", 0, 20, 0.5, 1),
        ("Vibration", "Vibration", " mm/s", 0, 10, 0.1, 2),
        ("Run_Hours", "Run Hours", " h", 0, 100000, 100, 0),
    ], [
        ("Fan_Status", "Fan Status"), ("Alarm_HighVibration", "Alarm: High Vibration"),
        ("Alarm_LowBasin", "Alarm: Low Basin"),
    ]),
    ("valve", "Valve", [
        ("Position", "Position", " %", 0, 100, 1, 0),
        ("Commanded_Position", "Commanded Position", " %", 0, 100, 1, 0),
        ("Actuator_Temp", "Actuator Temp", " °C", 0, 100, 0.5, 1),
    ], [
        ("Status_Modulating", "Status Modulating"), ("Alarm_ActuatorFault", "Alarm: Actuator Fault"),
    ]),
]

for _gid, _title, _num, _binr in _PLANT_LIMIT_SPEC:
    _rows = []
    for _p, _lbl, _suf, _amn, _amx, _stp, _dec in _num:
        _rows.append((f"{_gid}:{_p}", _lbl, "num", _amn, _amx, _stp, _dec, _suf, _amn, _amx))
    for _p, _lbl in _binr:
        _rows.append((f"{_gid}:{_p}", _lbl, "state", ["off", "on"]))
    _LIMIT_GROUPS.append((_title, _rows))


# ── style helpers (match other desktop panels) ────────────────────────────────

def _group_style() -> str:
    return (
        "QGroupBox { color: #8b949e; font-size: 7pt; "
        "border: 1px solid #30363d; border-radius: 4px; "
        "margin-top: 8px; padding-top: 4px; } "
        "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
    )

def _spinbox_style() -> str:
    return (
        "QSpinBox, QDoubleSpinBox { background: #21262d; color: #e6edf3; "
        "border: 1px solid #30363d; border-radius: 4px; padding: 2px 4px; } "
        "QSpinBox:focus, QDoubleSpinBox:focus { border-color: #58a6ff; } "
        "QSpinBox:disabled, QDoubleSpinBox:disabled { color: #6e7681; } "
        "QSpinBox::up-button, QSpinBox::down-button, "
        "QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width:0; height:0; border:none; }"
    )

def _combo_style() -> str:
    return (
        "QComboBox { background: #21262d; color: #e6edf3; "
        "border: 1px solid #30363d; border-radius: 4px; padding: 2px 6px; } "
        "QComboBox:focus { border-color: #58a6ff; } "
        "QComboBox:disabled { color: #6e7681; } "
        "QComboBox::drop-down { border: none; width: 16px; } "
        "QComboBox QAbstractItemView { background: #21262d; color: #e6edf3; "
        "border: 1px solid #30363d; selection-background-color: #30363d; outline: none; }"
    )

def _btn_secondary() -> str:
    return (
        "QPushButton { background: #21262d; color: #e6edf3; "
        "border: 1px solid #30363d; border-radius: 6px; padding: 4px 10px; } "
        "QPushButton:hover { background: #30363d; } "
        "QPushButton:pressed { background: #0d1117; } "
        "QPushButton:disabled { color: #6e7681; }"
    )

def _btn_primary() -> str:
    return (
        "QPushButton { background: #238636; color: #ffffff; "
        "border: 1px solid #2ea043; border-radius: 6px; padding: 5px 16px; font-weight: bold; } "
        "QPushButton:hover { background: #2ea043; } "
        "QPushButton:pressed { background: #1a6128; } "
        "QPushButton:disabled { background: #21262d; color: #6e7681; border-color: #30363d; }"
    )


class TickPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store: "DeviceStateStore | None" = None
        self._metric_checks: dict[str, QCheckBox]      = {}
        self._limit_checks:  dict[str, QCheckBox]      = {}
        self._limit_min:     dict[str, QDoubleSpinBox] = {}
        self._limit_max:     dict[str, QDoubleSpinBox] = {}
        self._limit_combo:   dict[str, QComboBox]      = {}
        self._build_ui()

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_state_store(self, store: "DeviceStateStore"):
        self._store = store
        self._load()

    def set_available(self, available: bool):
        self._chk_enabled.setEnabled(available)
        if not available:
            self._chk_enabled.setChecked(False)

    # ── Build UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(26)
        title_bar.setStyleSheet("background: #21262d; border-bottom: 1px solid #30363d;")
        tb_row = QHBoxLayout(title_bar)
        tb_row.setContentsMargins(8, 0, 8, 0)
        title_lbl = QLabel("Metrics Tick")
        title_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        title_lbl.setStyleSheet("color: #e6edf3; background: transparent; border: none;")
        tb_row.addWidget(title_lbl)
        tb_row.addStretch()
        self._status_lbl = QLabel("● —")
        self._status_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        self._status_lbl.setStyleSheet("color: #8b949e; background: transparent; border: none;")
        tb_row.addWidget(self._status_lbl)
        root.addWidget(title_bar)

        # ── Tab bar ───────────────────────────────────────────────────────────
        tab_bar = QWidget()
        tab_bar.setFixedHeight(28)
        tab_bar.setStyleSheet("background: #161b22; border-bottom: 1px solid #30363d;")
        tbl = QHBoxLayout(tab_bar)
        tbl.setContentsMargins(6, 0, 6, 0)
        tbl.setSpacing(2)
        self._tab_metrics_btn = QPushButton("Metrics")
        self._tab_limits_btn  = QPushButton("Limits")
        for btn in (self._tab_metrics_btn, self._tab_limits_btn):
            btn.setFont(QFont("Arial", 8))
            btn.setFixedHeight(26)
            btn.setStyleSheet(self._tab_inactive_style())
        self._tab_metrics_btn.setStyleSheet(self._tab_active_style())
        self._tab_metrics_btn.clicked.connect(lambda: self._switch_tab(0))
        self._tab_limits_btn.clicked.connect(lambda:  self._switch_tab(1))
        tbl.addWidget(self._tab_metrics_btn)
        tbl.addWidget(self._tab_limits_btn)
        tbl.addStretch()
        root.addWidget(tab_bar)

        # ── Stacked pages ─────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        metrics_page = QWidget()
        self._build_metrics_page(metrics_page)
        self._stack.addWidget(metrics_page)

        limits_page = QWidget()
        self._build_limits_page(limits_page)
        self._stack.addWidget(limits_page)

        root.addWidget(self._stack, stretch=1)

        # ── Apply bar ─────────────────────────────────────────────────────────
        apply_bar = QWidget()
        apply_bar.setFixedHeight(40)
        apply_bar.setStyleSheet("background: #21262d; border-top: 1px solid #30363d;")
        al = QHBoxLayout(apply_bar)
        al.setContentsMargins(6, 6, 6, 6)
        al.setSpacing(6)

        self._btn_apply = QPushButton("Apply")
        self._btn_apply.setEnabled(False)
        self._btn_apply.setStyleSheet(_btn_primary())
        self._btn_apply.clicked.connect(self._apply)
        al.addWidget(self._btn_apply)

        root.addWidget(apply_bar)

    def _tab_active_style(self) -> str:
        return (
            "QPushButton { background: transparent; color: #e6edf3; border: none; "
            "border-bottom: 2px solid #58a6ff; padding: 2px 10px; border-radius: 0; } "
            "QPushButton:hover { background: #30363d; }"
        )

    def _tab_inactive_style(self) -> str:
        return (
            "QPushButton { background: transparent; color: #8b949e; border: none; "
            "border-bottom: 2px solid transparent; padding: 2px 10px; border-radius: 0; } "
            "QPushButton:hover { background: #21262d; color: #e6edf3; }"
        )

    def _switch_tab(self, idx: int):
        self._stack.setCurrentIndex(idx)
        self._tab_metrics_btn.setStyleSheet(self._tab_active_style()   if idx == 0 else self._tab_inactive_style())
        self._tab_limits_btn.setStyleSheet( self._tab_active_style()   if idx == 1 else self._tab_inactive_style())

    # ── Metrics page ──────────────────────────────────────────────────────────

    def _build_metrics_page(self, parent: QWidget):
        outer = QVBoxLayout(parent)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(4)

        # Ticker control group
        ctrl = QGroupBox("Ticker Control")
        ctrl.setStyleSheet(_group_style())
        ctl = QVBoxLayout(ctrl)
        ctl.setSpacing(6)
        ctl.setContentsMargins(8, 6, 8, 8)

        self._chk_enabled = QCheckBox("Ticker enabled")
        self._chk_enabled.setEnabled(False)
        self._chk_enabled.setStyleSheet("color: #e6edf3; font-size: 9pt;")
        ctl.addWidget(self._chk_enabled)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background: #30363d; max-height: 1px; border: none;")
        ctl.addWidget(sep)

        iv_row = QHBoxLayout()
        iv_row.setSpacing(6)
        lbl_iv = QLabel("Interval")
        lbl_iv.setStyleSheet("color: #8b949e; font-size: 9pt;")
        lbl_iv.setFixedWidth(52)
        self._spin_interval = QSpinBox()
        self._spin_interval.setRange(1, 3600)
        self._spin_interval.setSuffix(" s")
        self._spin_interval.setFixedWidth(76)
        self._spin_interval.setStyleSheet(_spinbox_style())
        hint = QLabel("1–3600 s")
        hint.setStyleSheet("color: #484f58; font-size: 8pt; font-family: Consolas, monospace;")
        iv_row.addWidget(lbl_iv)
        iv_row.addWidget(self._spin_interval)
        iv_row.addWidget(hint)
        iv_row.addStretch()
        ctl.addLayout(iv_row)
        cl.addWidget(ctrl)

        # Metric flag groups
        for group_title, rows in _METRIC_GROUPS:
            grp = QGroupBox(group_title)
            grp.setStyleSheet(_group_style())
            gl = QVBoxLayout(grp)
            gl.setSpacing(1)
            gl.setContentsMargins(8, 4, 8, 6)
            for key, label, tip in rows:
                chk = QCheckBox(label)
                chk.setToolTip(tip)
                chk.setStyleSheet("QCheckBox { color: #e6edf3; font-size: 9pt; spacing: 6px; }")
                gl.addWidget(chk)
                self._metric_checks[key] = chk
            cl.addWidget(grp)

        cl.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        # All / None row
        sel_bar = QWidget()
        sel_bar.setFixedHeight(30)
        sel_bar.setStyleSheet("background: #161b22; border-top: 1px solid #30363d;")
        sl = QHBoxLayout(sel_bar)
        sl.setContentsMargins(6, 4, 6, 4)
        sl.setSpacing(4)
        lbl_sel = QLabel("Toggle all:")
        lbl_sel.setStyleSheet("color: #8b949e; font-size: 8pt;")
        btn_all  = QPushButton("All")
        btn_none = QPushButton("None")
        for b in (btn_all, btn_none):
            b.setFixedHeight(22)
            b.setStyleSheet(
                "QPushButton { background: #21262d; color: #e6edf3; "
                "border: 1px solid #30363d; border-radius: 4px; padding: 2px 8px; font-size: 8pt; } "
                "QPushButton:hover { background: #30363d; } "
                "QPushButton:pressed { background: #0d1117; }"
            )
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none.clicked.connect(lambda: self._set_all(False))
        sl.addWidget(lbl_sel)
        sl.addStretch()
        sl.addWidget(btn_all)
        sl.addWidget(btn_none)
        outer.addWidget(sel_bar)

    # ── Limits page ───────────────────────────────────────────────────────────

    def _build_limits_page(self, parent: QWidget):
        outer = QVBoxLayout(parent)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(4)

        # Hint bar
        hint_w = QWidget()
        hint_w.setStyleSheet(
            "background: #21262d; border: 1px solid #30363d; border-radius: 4px;"
        )
        hint_l = QHBoxLayout(hint_w)
        hint_l.setContentsMargins(8, 5, 8, 5)
        hint_l.setSpacing(6)
        ico = QLabel("ⓘ")
        ico.setStyleSheet("color: #58a6ff; font-size: 10pt; background: transparent; border: none;")
        txt = QLabel(
            "Check a row to constrain it.  "
            "Numeric: clamps walk output.  State: forces a fixed value."
        )
        txt.setStyleSheet("color: #8b949e; font-size: 8pt; background: transparent; border: none;")
        txt.setWordWrap(True)
        hint_l.addWidget(ico)
        hint_l.addWidget(txt, stretch=1)
        cl.addWidget(hint_w)

        for group_title, rows in _LIMIT_GROUPS:
            grp = QGroupBox(group_title)
            grp.setStyleSheet(_group_style())
            gl = QVBoxLayout(grp)
            gl.setSpacing(2)
            gl.setContentsMargins(8, 4, 8, 6)

            for row in rows:
                key, label, kind = row[0], row[1], row[2]
                chk = QCheckBox()
                chk.setFixedSize(16, 16)
                self._limit_checks[key] = chk

                if kind == "num":
                    _, _, _, abs_min, abs_max, step, decimals, suffix, def_min, def_max = row
                    row_w = self._make_num_row(
                        chk, label, abs_min, abs_max, step, decimals, suffix, def_min, def_max
                    )
                    spin_min = row_w.property("spin_min")
                    spin_max = row_w.property("spin_max")
                    self._limit_min[key] = spin_min
                    self._limit_max[key] = spin_max
                    chk.toggled.connect(
                        lambda v, sm=spin_min, sx=spin_max: (sm.setEnabled(v), sx.setEnabled(v))
                    )
                    spin_min.setEnabled(False)
                    spin_max.setEnabled(False)
                else:
                    options = row[3]
                    row_w = self._make_state_row(chk, label, options)
                    combo = row_w.property("combo")
                    self._limit_combo[key] = combo
                    chk.toggled.connect(lambda v, c=combo: c.setEnabled(v))
                    combo.setEnabled(False)
                gl.addWidget(row_w)

            cl.addWidget(grp)

        cl.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

    def _make_num_row(
        self, chk, label, abs_min, abs_max, step, decimals, suffix, def_min, def_max
    ) -> QWidget:
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 1, 0, 1)
        hl.setSpacing(4)
        hl.addWidget(chk)

        lbl = QLabel(label)
        lbl.setMinimumWidth(70)
        lbl.setMaximumWidth(100)
        lbl.setStyleSheet("color: #e6edf3; font-size: 8.5pt;")
        hl.addWidget(lbl, stretch=1)

        spin_min = QDoubleSpinBox()
        spin_min.setRange(abs_min, abs_max)
        spin_min.setSingleStep(step)
        spin_min.setDecimals(decimals)
        spin_min.setValue(def_min)
        spin_min.setSuffix(suffix)
        spin_min.setMinimumWidth(54)
        spin_min.setStyleSheet(_spinbox_style())

        arrow = QLabel("→")
        arrow.setStyleSheet("color: #484f58;")
        arrow.setFixedWidth(12)
        arrow.setAlignment(Qt.AlignCenter)

        spin_max = QDoubleSpinBox()
        spin_max.setRange(abs_min, abs_max)
        spin_max.setSingleStep(step)
        spin_max.setDecimals(decimals)
        spin_max.setValue(def_max)
        spin_max.setSuffix(suffix)
        spin_max.setMinimumWidth(54)
        spin_max.setStyleSheet(_spinbox_style())

        hl.addWidget(spin_min, stretch=2)
        hl.addWidget(arrow)
        hl.addWidget(spin_max, stretch=2)

        w.setProperty("spin_min", spin_min)
        w.setProperty("spin_max", spin_max)
        return w

    def _make_state_row(self, chk, label, options) -> QWidget:
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 1, 0, 1)
        hl.setSpacing(4)
        hl.addWidget(chk)

        lbl = QLabel(label)
        lbl.setMinimumWidth(70)
        lbl.setMaximumWidth(100)
        lbl.setStyleSheet("color: #e6edf3; font-size: 8.5pt;")
        hl.addWidget(lbl, stretch=1)

        lbl_f = QLabel("force →")
        lbl_f.setStyleSheet("color: #484f58; font-size: 8pt; font-family: Consolas, monospace;")
        combo = QComboBox()
        combo.addItems(options)
        combo.setMinimumWidth(80)
        combo.setFixedHeight(22)
        combo.setStyleSheet(_combo_style())

        hl.addWidget(lbl_f)
        hl.addWidget(combo, stretch=3)

        w.setProperty("combo", combo)
        return w

    # ── Load / Apply / Status ─────────────────────────────────────────────────

    def _load(self):
        if self._store is None:
            return
        store = self._store
        self._chk_enabled.setChecked(store.is_running() and not store.is_paused())
        self._spin_interval.setValue(int(store._tick_interval))

        for key, chk in self._metric_checks.items():
            chk.setChecked(store.metric_flags.get(key, True))

        for key, lim in store.metric_limits.items():
            if key in self._limit_checks:
                enabled = lim.get("enabled", False)
                self._limit_checks[key].setChecked(enabled)
            if key in self._limit_min:
                _mn, _mx = lim.get("min"), lim.get("max")
                # None → keep the spin's default (def_min/def_max); plant limits
                # start unset so the configured range stays put until edited.
                if _mn is not None:
                    self._limit_min[key].setValue(_mn)
                if _mx is not None:
                    self._limit_max[key].setValue(_mx)
                self._limit_min[key].setEnabled(lim.get("enabled", False))
                self._limit_max[key].setEnabled(lim.get("enabled", False))
            if key in self._limit_combo:
                lock = lim.get("lock", "")
                idx = self._limit_combo[key].findText(lock)
                if idx >= 0:
                    self._limit_combo[key].setCurrentIndex(idx)
                self._limit_combo[key].setEnabled(lim.get("enabled", False))

        self._btn_apply.setEnabled(True)
        self._update_status()

    def _apply(self):
        if self._store is None:
            return
        store = self._store
        store.set_tick_interval(self._spin_interval.value())
        if store.is_running():
            store.set_paused(not self._chk_enabled.isChecked())
        for key, chk in self._metric_checks.items():
            store.metric_flags[key] = chk.isChecked()
        for key, chk in self._limit_checks.items():
            lim = store.metric_limits.get(key)
            if lim is None:
                continue
            lim["enabled"] = chk.isChecked()
            if key in self._limit_min:
                lim["min"] = self._limit_min[key].value()
                lim["max"] = self._limit_max[key].value()
            if key in self._limit_combo:
                lim["lock"] = self._limit_combo[key].currentText()
        self._update_status()

    def _update_status(self):
        if self._store is None:
            self._status_lbl.setText("● —")
            self._status_lbl.setStyleSheet("color: #8b949e; background: transparent; border: none;")
            return
        store = self._store
        if not store.is_running():
            txt, color = "● Idle",    "#8b949e"
        elif store.is_paused():
            txt, color = "● Paused",  "#d29922"
        else:
            ivl = int(store._tick_interval)
            txt, color = f"● {ivl} s", "#3fb950"
        self._status_lbl.setText(txt)
        self._status_lbl.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )

    def _set_all(self, state: bool):
        for chk in self._metric_checks.values():
            chk.setChecked(state)
