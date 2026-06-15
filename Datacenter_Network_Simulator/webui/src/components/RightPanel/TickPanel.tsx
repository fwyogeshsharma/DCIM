import { useState, useEffect, useCallback } from 'react'
import { api } from '../../api/client'

// ── Static metadata ──────────────────────────────────────────────────────────

type MetricRow   = { key: string; label: string; tip: string; derived?: boolean }
type MetricGroup = { gid: string; title: string; rows: MetricRow[] }

// ── Verdigris EV2 + chiller-plant BACnet metric toggles ───────────────────────
// Flag keys MUST match those registered in core/device_state_store.py.
// Plant keys are "<device_type>:<PointName>"; EV2 metrics are grouped.

const EV2_GROUP: MetricGroup = { gid: 'ev2', title: 'Verdigris EV2', rows: [
  { key: 'ev2_power',         label: 'Power (kW / V / A)',              tip: 'Panel total kW, phase voltages & currents' },
  { key: 'ev2_energy',        label: 'Energy (kWh)',                    tip: 'Panel + per-circuit kWh accumulators', derived: true },
  { key: 'ev2_power_quality', label: 'Power Quality (THD / Harmonics)', tip: 'Voltage/current THD + harmonics 3/5/7/9' },
  { key: 'ev2_freq_pf',       label: 'Frequency & Power Factor',        tip: 'Mains frequency + panel power factor' },
  { key: 'ev2_alarms',        label: 'Panel Alarms',                    tip: 'Overcurrent, imbalance, high THD, phase loss, sensor fault' },
  { key: 'ev2_circuits',      label: 'Per-Circuit Metrics',             tip: 'Per-circuit current, kW, PF, THD' },
]}

const PLANT_FLAG_GROUPS: { gid: string; title: string; points: [string, string][] }[] = [
  { gid: 'crah', title: 'CRAH', points: [
    ['Supply_Air_Temp', 'Supply Air Temp'], ['Return_Air_Temp', 'Return Air Temp'],
    ['Setpoint', 'Setpoint'], ['Fan_Speed', 'Fan Speed'], ['CHW_Valve', 'CHW Valve'],
    ['Cooling_Capacity', 'Cooling Capacity'], ['Supply_Humidity', 'Supply Humidity'],
    ['Airflow', 'Airflow'], ['Fan_Power', 'Fan Power'], ['Run_Hours', 'Run Hours'],
    ['Unit_Running', 'Unit Running'], ['Alarm_HighTemp', 'Alarm: High Temp'],
    ['Alarm_AirflowLoss', 'Alarm: Airflow Loss'], ['Filter_Dirty', 'Filter Dirty'],
  ]},
  { gid: 'chiller', title: 'Chiller', points: [
    ['CHW_Supply_Temp', 'CHW Supply Temp'], ['CHW_Return_Temp', 'CHW Return Temp'],
    ['CHW_Setpoint', 'CHW Setpoint'], ['CHW_Flow', 'CHW Flow'],
    ['Cond_Supply_Temp', 'Cond Supply Temp'], ['Cond_Return_Temp', 'Cond Return Temp'],
    ['Compressor_Load', 'Compressor Load'], ['Active_Power', 'Active Power'],
    ['Cooling_Capacity', 'Cooling Capacity'], ['COP', 'COP'],
    ['Evap_Pressure', 'Evap Pressure'], ['Cond_Pressure', 'Cond Pressure'],
    ['Run_Hours', 'Run Hours'], ['Chiller_Running', 'Chiller Running'],
    ['Alarm_HighPressure', 'Alarm: High Pressure'], ['Alarm_LowEvapTemp', 'Alarm: Low Evap Temp'],
    ['Alarm_FlowLoss', 'Alarm: Flow Loss'],
  ]},
  { gid: 'pump', title: 'Pump', points: [
    ['Speed', 'Speed'], ['Flow', 'Flow'], ['Discharge_Pressure', 'Discharge Pressure'],
    ['Suction_Pressure', 'Suction Pressure'], ['Diff_Pressure', 'Diff Pressure'],
    ['Motor_Power', 'Motor Power'], ['Motor_Temp', 'Motor Temp'],
    ['VFD_Frequency', 'VFD Frequency'], ['Run_Hours', 'Run Hours'],
    ['Run_Status', 'Run Status'], ['Alarm_Fault', 'Alarm: Fault'],
    ['Alarm_LowFlow', 'Alarm: Low Flow'],
  ]},
  { gid: 'cooling_tower', title: 'Cooling Tower', points: [
    ['Fan_Speed', 'Fan Speed'], ['Basin_Temp', 'Basin Temp'],
    ['Cond_Water_In', 'Cond Water In'], ['Cond_Water_Out', 'Cond Water Out'],
    ['Fan_Power', 'Fan Power'], ['Basin_Level', 'Basin Level'],
    ['Makeup_Flow', 'Makeup Flow'], ['Vibration', 'Vibration'],
    ['Run_Hours', 'Run Hours'], ['Fan_Status', 'Fan Status'],
    ['Alarm_HighVibration', 'Alarm: High Vibration'], ['Alarm_LowBasin', 'Alarm: Low Basin'],
  ]},
  { gid: 'valve', title: 'Valve', points: [
    ['Position', 'Position'], ['Commanded_Position', 'Commanded Position'],
    ['Actuator_Temp', 'Actuator Temp'], ['Status_Modulating', 'Status Modulating'],
    ['Alarm_ActuatorFault', 'Alarm: Actuator Fault'],
  ]},
  { gid: 'cdu', title: 'CDU (Direct-to-Chip)', points: [
    ['TCS_Supply_Temp', 'TCS Supply Temp'], ['TCS_Return_Temp', 'TCS Return Temp'],
    ['TCS_Setpoint', 'TCS Setpoint'], ['TCS_Flow', 'TCS Flow'],
    ['Facility_CHW_Valve', 'Facility CHW Valve'], ['Facility_CHW_Flow', 'Facility CHW Flow'],
    ['TCS_Loop_Pressure', 'TCS Loop Pressure'],
    ['Heat_Load', 'Heat Load'], ['Pump_Power', 'Pump Power'], ['Pump_Speed', 'Pump Speed'],
    ['Approach_Temp', 'Approach Temp'], ['Filter_DP', 'Filter ΔP'], ['Run_Hours', 'Run Hours'],
    ['Unit_Running', 'Unit Running'], ['Alarm_Leak', 'Alarm: Leak'],
    ['Alarm_HighSupplyTemp', 'Alarm: High Supply Temp'], ['Alarm_PumpFault', 'Alarm: Pump Fault'],
    ['Alarm_LowFlow', 'Alarm: Low Flow'],
  ]},
]

const BACNET_METRIC_GROUPS: MetricGroup[] = [
  EV2_GROUP,
  ...PLANT_FLAG_GROUPS.map(g => ({
    gid: g.gid,
    title: g.title,
    rows: g.points.map(([k, label]) => ({
      key: `${g.gid}:${k}`, label, tip: `${label} — present-value updated each tick`,
    })),
  })),
]

const METRIC_GROUPS: MetricGroup[] = [
  { gid: 'all', title: 'All Devices', rows: [
    { key: 'cpu_usage',      label: 'CPU Usage %',              tip: 'Random walk ±4 pp; 1% spike chance to >90%' },
    { key: 'memory_used',    label: 'Memory Used %',            tip: 'Random walk; 0.5% spike chance to >85%' },
    { key: 'disk_used',      label: 'Disk Used %',              tip: 'Growth-biased walk; capped 5–90%' },
    { key: 'sys_uptime',     label: 'System Uptime',            tip: '+tick_interval centiseconds per tick' },
    { key: 'cpu_temp',       label: 'CPU Temperature',          tip: '20 + 0.42×cpu ± 1 °C, clamped 20–95 °C' },
    { key: 'inlet_temp',     label: 'Chassis Inlet Temp',       tip: '18 + 0.12×cpu ± 0.5 °C, clamped 15–55 °C (servers only)' },
    { key: 'iface_octets',   label: 'Interface Byte Counters',  tip: '+5K–150K per tick on every UP interface' },
    { key: 'iface_errors',   label: 'Interface Error Counters', tip: 'in_errors 10% / out_errors 5% per UP interface' },
    { key: 'iface_discards', label: 'Interface Discard Counters', tip: 'Scale with CPU congestion (>70: heavy, >50: light)' },
    { key: 'interface_flap', label: 'Interface Flapping',       tip: '0.2% per connected interface → DOWN; auto-recovers in 5s' },
  ]},
  { gid: 'server', title: 'Server Devices', rows: [
    { key: 'fan_rpm',        label: 'Chassis Fan Speed',        tip: '3000 + 95×(cpu_temp−40) ± 60 RPM' },
  ]},
  { gid: 'sensor', title: 'Sensor Devices', rows: [
    { key: 'sensor_ambient_temp', label: 'Inlet / Ambient Temp', tip: '±0.3 °C walk, clamped 15–35 °C (independent of CPU load)' },
    { key: 'humidity',            label: 'Ambient Humidity',     tip: '±1.5 %RH walk, clamped 10–90 %' },
    { key: 'dewpoint',            label: 'Dew Point',            tip: 'Derived from inlet/ambient temp + humidity each tick', derived: true },
    { key: 'airflow',             label: 'Airflow Speed',        tip: '±0.15 m/s walk, clamped 0.2–4.0 m/s (NetBotz only)' },
    { key: 'mid_temp',            label: 'Mid-Rack Temp',        tip: 'Raritan DPX2-T3H1 only — tracks ambient +3–7 °C' },
    { key: 'outlet_temp',         label: 'Exhaust Temp',         tip: 'Raritan DPX2-T3H1 only — tracks ambient +8–14 °C' },
    { key: 'water_detection',     label: 'Water Detection',      tip: 'Raritan DPX2-CC2 only — 0.05% wet; clears 20%' },
  ]},
  { gid: 'ups', title: 'UPS Devices', rows: [
    { key: 'ups_status',           label: 'UPS Power Status',        tip: 'normal → on_battery (0.1%) → low_battery state machine' },
    { key: 'ups_output_load',      label: 'Output Load %',           tip: '±3% walk; 0.5% spike >90%' },
    { key: 'ups_battery_status',   label: 'Battery Hardware Status', tip: 'normal / failure (0.05%) / disconnected' },
    { key: 'ups_input_voltage',    label: 'Input Voltage',           tip: '±2V walk; 0.3% spike outside 200–240V' },
    { key: 'ups_input_frequency',  label: 'Input Frequency',         tip: '±0.05Hz walk; 0.2% spike outside 49.5–50.5Hz' },
    { key: 'ups_fan_status',       label: 'Cooling Fan Status',      tip: 'ok / failure — 0.1% chance; recovers 15%' },
    { key: 'ups_charger_status',   label: 'Battery Charger Status',  tip: 'ok / failure — 0.1% chance; recovers 15%' },
    { key: 'ups_rectifier_status', label: 'Rectifier Status',        tip: 'ok / failure — 0.1% chance; recovers 15%' },
    { key: 'ups_phase_status',     label: 'Input Phase Status',      tip: 'ok / failure — 0.1% chance; recovers 15%' },
    { key: 'ups_bypass_status',    label: 'Bypass Path Status',      tip: 'off / on — 0.08% to bypass; recovers 12%' },
    { key: 'ups_battery_health',   label: 'Battery Health %',        tip: '% state-of-health; slow decay (faster if battery faulted)' },
    { key: 'ups_energy_kwh',       label: 'Energy Output (kWh)',     tip: 'Derived: cumulative ∫(output_load% × 3kW) dt per tick', derived: true },
    { key: 'ups_battery_voltage',  label: 'Battery Voltage (V)',     tip: 'Derived: sags with discharge — 220V normal, ~200V on-battery, ~186V low', derived: true },
    { key: 'ups_output_voltage',   label: 'Output Voltage (V)',      tip: 'Derived: regulated 220V (stable)', derived: true },
    { key: 'ups_output_current',   label: 'Output Current (A)',      tip: 'Derived: (output_load% × 3000VA) / 220V', derived: true },
    { key: 'ups_output_power',     label: 'Output Power (W)',        tip: 'Derived: (output_load% × 3000VA) × PF 0.9', derived: true },
    { key: 'ups_input_current',    label: 'Input Current (A)',       tip: 'Derived: output_power / efficiency(0.92) / input_voltage', derived: true },
    { key: 'ups_input_power',      label: 'Input Power (W)',         tip: 'Derived: output_power / efficiency(0.92)', derived: true },
  ]},
  { gid: 'pdu', title: 'PDU / Floor PDU', rows: [
    { key: 'pdu_load',            label: 'PDU Load %',              tip: '±3% walk; 0.4% spike >80%' },
    { key: 'pdu_voltage',         label: 'Input Voltage',           tip: '±2V walk; 0.3% spike outside 205–235V' },
    { key: 'pdu_power_factor',    label: 'Power Factor',            tip: '±0.02 walk; 0.3% dip <0.70' },
    { key: 'pdu_phase_imbalance', label: 'Phase Imbalance %',       tip: '±1% walk; 0.3% spike >20%' },
    { key: 'pdu_outlet_status',   label: 'Outlet Power Status',     tip: 'on/off — 0.1% flip; recovers 30%' },
    { key: 'pdu_breaker_status',  label: 'Circuit Breaker Status',  tip: 'ok/tripped — 0.1% trip; recovers 25%' },
    { key: 'pdu_outlet_failure',  label: 'Outlet Hardware Failure', tip: 'ok/failed — 0.1%; recovers 25%' },
    { key: 'pdu_smoke',           label: 'Smoke Detection',         tip: 'no/yes — 0.01% chance; clears 5%' },
    { key: 'pdu_outlet_current',  label: 'Outlet Current',          tip: '±1A walk; 0.3% spike >20A' },
    { key: 'pdu_ground_fault',    label: 'Ground Fault Status',     tip: 'no/yes — 0.05% chance; clears 20%' },
    { key: 'pdu_frequency',       label: 'Input Frequency',         tip: '±0.05 Hz walk; 0.2% spike outside 49.5–50.5 Hz' },
    { key: 'pdu_temperature',     label: 'Ambient Temperature',     tip: '±0.3 °C walk, clamped 15–45 °C' },
    { key: 'pdu_humidity',        label: 'Ambient Humidity',        tip: '±1 %RH walk, clamped 10–90 %' },
    { key: 'pdu_energy_kwh',      label: 'Energy Output (kWh)',     tip: 'Derived: cumulative ∫(Real Power kW) dt per tick', derived: true },
  ]},
  { gid: 'net', title: 'Router / Firewall', rows: [
    { key: 'bgp_sessions', label: 'BGP Session State', tip: 'established → idle (0.5%) → established (15%)' },
  ]},
  ...BACNET_METRIC_GROUPS,
]

type NumMeta   = { kind: 'num'; absMin: number; absMax: number; step: number; decimals: number; suffix: string; defMin: number; defMax: number }
type StateMeta = { kind: 'state'; options: string[] }
type LimitRow  = { key: string; label: string } & (NumMeta | StateMeta)
type LimitGroup = { gid: string; title: string; rows: LimitRow[] }

// ── EV2 + chiller-plant limit specs ───────────────────────────────────────────
// Numeric points clamp the walk; binary points force off/on (force alarms).
// Keys MUST match those registered in core/device_state_store.py.

// [key, label, suffix, absMin, absMax, step, decimals]
type PNum = [string, string, string, number, number, number, number]

const PLANT_LIMIT_SPEC: Record<string, { title: string; num: PNum[]; bin: [string, string][] }> = {
  ev2: { title: 'Verdigris EV2', num: [
    ['Panel_Total_kW', 'Panel Total kW', ' kW', 0, 200, 1, 0],
    ['Voltage_PhA', 'Voltage Ph A', 'V', 200, 260, 1, 1],
    ['Voltage_PhB', 'Voltage Ph B', 'V', 200, 260, 1, 1],
    ['Voltage_PhC', 'Voltage Ph C', 'V', 200, 260, 1, 1],
    ['Current_PhA', 'Current Ph A', 'A', 0, 200, 1, 1],
    ['Current_PhB', 'Current Ph B', 'A', 0, 200, 1, 1],
    ['Current_PhC', 'Current Ph C', 'A', 0, 200, 1, 1],
    ['Line_Frequency', 'Line Frequency', 'Hz', 45, 65, 0.05, 2],
    ['Panel_PF', 'Power Factor', '', 0, 1, 0.01, 2],
    ['Voltage_THD', 'Voltage THD', '%', 0, 50, 0.5, 1],
    ['Current_THD', 'Current THD', '%', 0, 50, 0.5, 1],
  ], bin: [
    ['Alarm_Overcurrent', 'Alarm: Overcurrent'], ['Alarm_VoltageImbalance', 'Alarm: V Imbalance'],
    ['Alarm_HighTHD', 'Alarm: High THD'], ['Alarm_PhaseLoss', 'Alarm: Phase Loss'],
    ['Alarm_SensorFault', 'Alarm: Sensor Fault'],
  ]},
  crah: { title: 'CRAH', num: [
    ['Supply_Air_Temp', 'Supply Air Temp', '°C', 0, 40, 0.5, 1],
    ['Return_Air_Temp', 'Return Air Temp', '°C', 0, 50, 0.5, 1],
    ['Setpoint', 'Setpoint', '°C', 10, 30, 0.5, 1],
    ['Fan_Speed', 'Fan Speed', '%', 0, 100, 1, 0],
    ['CHW_Valve', 'CHW Valve', '%', 0, 100, 1, 0],
    ['Cooling_Capacity', 'Cooling Capacity', '%', 0, 100, 1, 0],
    ['Supply_Humidity', 'Supply Humidity', '%', 0, 100, 1, 0],
    ['Airflow', 'Airflow', '%', 0, 100, 1, 0],
    ['Fan_Power', 'Fan Power', ' kW', 0, 50, 0.1, 2],
    ['Run_Hours', 'Run Hours', ' h', 0, 100000, 100, 0],
  ], bin: [
    ['Unit_Running', 'Unit Running'], ['Alarm_HighTemp', 'Alarm: High Temp'],
    ['Alarm_AirflowLoss', 'Alarm: Airflow Loss'], ['Filter_Dirty', 'Filter Dirty'],
  ]},
  chiller: { title: 'Chiller', num: [
    ['CHW_Supply_Temp', 'CHW Supply Temp', '°C', 0, 20, 0.5, 1],
    ['CHW_Return_Temp', 'CHW Return Temp', '°C', 0, 25, 0.5, 1],
    ['CHW_Setpoint', 'CHW Setpoint', '°C', 4, 12, 0.5, 1],
    ['CHW_Flow', 'CHW Flow', ' L/s', 0, 50, 0.5, 1],
    ['Cond_Supply_Temp', 'Cond Supply Temp', '°C', 0, 50, 0.5, 1],
    ['Cond_Return_Temp', 'Cond Return Temp', '°C', 0, 50, 0.5, 1],
    ['Compressor_Load', 'Compressor Load', '%', 0, 100, 1, 0],
    ['Active_Power', 'Active Power', ' kW', 0, 600, 1, 0],
    ['Cooling_Capacity', 'Cooling Capacity', ' kW', 0, 1000, 1, 0],
    ['COP', 'COP', '', 0, 10, 0.1, 2],
    ['Evap_Pressure', 'Evap Pressure', ' kPa', 0, 800, 1, 0],
    ['Cond_Pressure', 'Cond Pressure', ' kPa', 0, 1500, 1, 0],
    ['Run_Hours', 'Run Hours', ' h', 0, 100000, 100, 0],
  ], bin: [
    ['Chiller_Running', 'Chiller Running'], ['Alarm_HighPressure', 'Alarm: High Pressure'],
    ['Alarm_LowEvapTemp', 'Alarm: Low Evap Temp'], ['Alarm_FlowLoss', 'Alarm: Flow Loss'],
  ]},
  pump: { title: 'Pump', num: [
    ['Speed', 'Speed', '%', 0, 100, 1, 0],
    ['Flow', 'Flow', ' L/s', 0, 50, 0.5, 1],
    ['Discharge_Pressure', 'Discharge Pressure', ' kPa', 0, 800, 1, 0],
    ['Suction_Pressure', 'Suction Pressure', ' kPa', 0, 400, 1, 0],
    ['Diff_Pressure', 'Diff Pressure', ' kPa', 0, 600, 1, 0],
    ['Motor_Power', 'Motor Power', ' kW', 0, 100, 0.1, 2],
    ['Motor_Temp', 'Motor Temp', '°C', 0, 120, 0.5, 1],
    ['VFD_Frequency', 'VFD Frequency', ' Hz', 0, 60, 0.5, 1],
    ['Run_Hours', 'Run Hours', ' h', 0, 100000, 100, 0],
  ], bin: [
    ['Run_Status', 'Run Status'], ['Alarm_Fault', 'Alarm: Fault'], ['Alarm_LowFlow', 'Alarm: Low Flow'],
  ]},
  cooling_tower: { title: 'Cooling Tower', num: [
    ['Fan_Speed', 'Fan Speed', '%', 0, 100, 1, 0],
    ['Basin_Temp', 'Basin Temp', '°C', 0, 50, 0.5, 1],
    ['Cond_Water_In', 'Cond Water In', '°C', 0, 50, 0.5, 1],
    ['Cond_Water_Out', 'Cond Water Out', '°C', 0, 50, 0.5, 1],
    ['Fan_Power', 'Fan Power', ' kW', 0, 50, 0.1, 2],
    ['Basin_Level', 'Basin Level', '%', 0, 100, 1, 0],
    ['Makeup_Flow', 'Makeup Flow', ' L/min', 0, 20, 0.5, 1],
    ['Vibration', 'Vibration', ' mm/s', 0, 10, 0.1, 2],
    ['Run_Hours', 'Run Hours', ' h', 0, 100000, 100, 0],
  ], bin: [
    ['Fan_Status', 'Fan Status'], ['Alarm_HighVibration', 'Alarm: High Vibration'],
    ['Alarm_LowBasin', 'Alarm: Low Basin'],
  ]},
  valve: { title: 'Valve', num: [
    ['Position', 'Position', '%', 0, 100, 1, 0],
    ['Commanded_Position', 'Commanded Position', '%', 0, 100, 1, 0],
    ['Actuator_Temp', 'Actuator Temp', '°C', 0, 100, 0.5, 1],
  ], bin: [
    ['Status_Modulating', 'Status Modulating'], ['Alarm_ActuatorFault', 'Alarm: Actuator Fault'],
  ]},
  cdu: { title: 'CDU (Direct-to-Chip)', num: [
    ['TCS_Supply_Temp', 'TCS Supply Temp', '°C', 0, 60, 0.5, 1],
    ['TCS_Return_Temp', 'TCS Return Temp', '°C', 0, 70, 0.5, 1],
    ['TCS_Setpoint', 'TCS Setpoint', '°C', 20, 45, 0.5, 1],
    ['TCS_Flow', 'TCS Flow', ' L/s', 0, 50, 0.5, 1],
    ['Facility_CHW_Valve', 'Facility CHW Valve', '%', 0, 100, 1, 0],
    ['Facility_CHW_Flow', 'Facility CHW Flow', ' L/s', 0, 50, 0.5, 1],
    ['TCS_Loop_Pressure', 'TCS Loop Pressure', ' kPa', 0, 400, 1, 0],
    ['Heat_Load', 'Heat Load', ' kW', 0, 1200, 1, 0],
    ['Pump_Power', 'Pump Power', ' kW', 0, 50, 0.1, 2],
    ['Pump_Speed', 'Pump Speed', '%', 0, 100, 1, 0],
    ['Approach_Temp', 'Approach Temp', '°C', 0, 15, 0.1, 1],
    ['Filter_DP', 'Filter ΔP', ' kPa', 0, 120, 1, 0],
    ['Run_Hours', 'Run Hours', ' h', 0, 100000, 100, 0],
  ], bin: [
    ['Unit_Running', 'Unit Running'], ['Alarm_Leak', 'Alarm: Leak'],
    ['Alarm_HighSupplyTemp', 'Alarm: High Supply Temp'], ['Alarm_PumpFault', 'Alarm: Pump Fault'],
    ['Alarm_LowFlow', 'Alarm: Low Flow'],
  ]},
}

const BACNET_LIMIT_GROUPS: LimitGroup[] = Object.entries(PLANT_LIMIT_SPEC).map(([gid, s]) => ({
  gid,
  title: s.title,
  rows: [
    ...s.num.map(([key, label, suffix, absMin, absMax, step, decimals]): LimitRow => ({
      key: `${gid}:${key}`, label, kind: 'num', absMin, absMax, step, decimals, suffix, defMin: absMin, defMax: absMax,
    })),
    ...s.bin.map(([key, label]): LimitRow => ({
      key: `${gid}:${key}`, label, kind: 'state', options: ['off', 'on'],
    })),
  ],
}))

const LIMIT_GROUPS: LimitGroup[] = [
  { gid: 'all', title: 'All Devices', rows: [
    { key: 'cpu_usage',  label: 'CPU Usage %',             kind: 'num', absMin: 0,   absMax: 100, step: 1,   decimals: 0, suffix: '%',   defMin: 0,   defMax: 100 },
    { key: 'memory_pct', label: 'Memory Used %',           kind: 'num', absMin: 0,   absMax: 100, step: 1,   decimals: 0, suffix: '%',   defMin: 0,   defMax: 100 },
    { key: 'disk_pct',   label: 'Disk Used %',             kind: 'num', absMin: 0,   absMax: 100, step: 1,   decimals: 0, suffix: '%',   defMin: 0,   defMax: 100 },
    { key: 'cpu_temp',   label: 'CPU Temperature',         kind: 'num', absMin: 20,  absMax: 95,  step: 0.5, decimals: 1, suffix: '°C',  defMin: 20,  defMax: 95  },
    { key: 'inlet_temp', label: 'Chassis Inlet Temp (servers)', kind: 'num', absMin: 15, absMax: 55, step: 0.5, decimals: 1, suffix: '°C', defMin: 15, defMax: 55 },
  ]},
  { gid: 'server', title: 'Server Devices', rows: [
    { key: 'fan_rpm',    label: 'Chassis Fan Speed',       kind: 'num', absMin: 0,  absMax: 20000, step: 100, decimals: 0, suffix: 'RPM', defMin: 0, defMax: 12000 },
  ]},
  { gid: 'sensor', title: 'Sensor Devices', rows: [
    { key: 'sensor_ambient_temp', label: 'Inlet / Ambient Temp',    kind: 'num',   absMin: 15, absMax: 35, step: 0.5, decimals: 1, suffix: '°C', defMin: 15,  defMax: 35  },
    { key: 'humidity',            label: 'Ambient Humidity',        kind: 'num',   absMin: 10, absMax: 90, step: 1,   decimals: 1, suffix: '%',   defMin: 10,  defMax: 90  },
    { key: 'airflow',             label: 'Airflow Speed (NetBotz)', kind: 'num',   absMin: 0,  absMax: 5,  step: 0.1, decimals: 2, suffix: 'm/s', defMin: 0.2, defMax: 4.0 },
    { key: 'mid_temp',            label: 'Mid-Rack Temperature',    kind: 'num',   absMin: 15, absMax: 55, step: 0.5, decimals: 1, suffix: '°C',  defMin: 15,  defMax: 55  },
    { key: 'outlet_temp',         label: 'Exhaust Temperature',     kind: 'num',   absMin: 15, absMax: 65, step: 0.5, decimals: 1, suffix: '°C',  defMin: 15,  defMax: 65  },
    { key: 'water_detection',     label: 'Water Detection (CC2)',   kind: 'state', options: ['dry', 'wet'] },
  ]},
  { gid: 'ups', title: 'UPS Devices', rows: [
    { key: 'ups_output_load',     label: 'Output Load %',          kind: 'num',   absMin: 0,   absMax: 100, step: 1,   decimals: 1, suffix: '%',  defMin: 0,    defMax: 100  },
    { key: 'ups_input_voltage',   label: 'Input Voltage',          kind: 'num',   absMin: 200, absMax: 260, step: 1,   decimals: 1, suffix: 'V',  defMin: 200,  defMax: 240  },
    { key: 'ups_input_frequency', label: 'Input Frequency',        kind: 'num',   absMin: 47,  absMax: 53,  step: 0.1, decimals: 2, suffix: 'Hz', defMin: 49.5, defMax: 50.5 },
    { key: 'ups_status',          label: 'UPS Power Status',       kind: 'state', options: ['normal', 'on_battery', 'low_battery'] },
    { key: 'ups_battery_status',  label: 'Battery Hardware Status',kind: 'state', options: ['normal', 'failure', 'disconnected'] },
    { key: 'ups_fan_status',      label: 'Cooling Fan Status',     kind: 'state', options: ['ok', 'failure'] },
    { key: 'ups_charger_status',  label: 'Battery Charger Status', kind: 'state', options: ['ok', 'failure'] },
    { key: 'ups_rectifier_status',label: 'Rectifier Status',       kind: 'state', options: ['ok', 'failure'] },
    { key: 'ups_phase_status',    label: 'Input Phase Status',     kind: 'state', options: ['ok', 'failure'] },
    { key: 'ups_battery_health',  label: 'Battery Health %',       kind: 'num',   absMin: 0,   absMax: 100, step: 1,   decimals: 1, suffix: '%',  defMin: 0,    defMax: 100  },
    { key: 'ups_bypass_status',   label: 'Bypass Path Status',     kind: 'state', options: ['off', 'on'] },
  ]},
  { gid: 'pdu', title: 'PDU Devices', rows: [
    { key: 'pdu_load',           label: 'PDU Load %',              kind: 'num',   absMin: 0,   absMax: 100, step: 1,   decimals: 1, suffix: '%', defMin: 0,    defMax: 100 },
    { key: 'pdu_voltage',        label: 'Input Voltage',           kind: 'num',   absMin: 190, absMax: 250, step: 1,   decimals: 1, suffix: 'V', defMin: 205,  defMax: 235 },
    { key: 'pdu_outlet_current', label: 'Outlet Current',          kind: 'num',   absMin: 0,   absMax: 30,  step: 1,   decimals: 1, suffix: 'A', defMin: 0,    defMax: 20  },
    { key: 'pdu_outlet_status',  label: 'Outlet Power Status',     kind: 'state', options: ['on', 'off'] },
    { key: 'pdu_breaker_status', label: 'Circuit Breaker Status',  kind: 'state', options: ['ok', 'tripped'] },
    { key: 'pdu_outlet_failure', label: 'Outlet Hardware Failure', kind: 'state', options: ['ok', 'failed'] },
    { key: 'pdu_smoke',          label: 'Smoke Detection',         kind: 'state', options: ['no', 'yes'] },
    { key: 'pdu_ground_fault',   label: 'Ground Fault Status',     kind: 'state', options: ['no', 'yes'] },
    { key: 'pdu_frequency',      label: 'Input Frequency',         kind: 'num',   absMin: 47,  absMax: 53,  step: 0.1, decimals: 2, suffix: 'Hz', defMin: 49.5, defMax: 50.5 },
    { key: 'pdu_temperature',    label: 'Ambient Temperature',     kind: 'num',   absMin: 15,  absMax: 45,  step: 0.5, decimals: 1, suffix: '°C', defMin: 15,   defMax: 45   },
    { key: 'pdu_humidity',       label: 'Ambient Humidity',        kind: 'num',   absMin: 10,  absMax: 90,  step: 1,   decimals: 1, suffix: '%',  defMin: 10,   defMax: 90   },
  ]},
  { gid: 'net', title: 'Router / Firewall', rows: [
    { key: 'bgp_sessions', label: 'BGP Session State', kind: 'state', options: ['established', 'idle'] },
  ]},
  ...BACNET_LIMIT_GROUPS,
]

// ── Types ────────────────────────────────────────────────────────────────────

interface LimitState { enabled: boolean; min: number; max: number; lock: string; options: string[] }
interface TickSettings {
  running: boolean; paused: boolean; interval: number
  metric_flags: Record<string, boolean>
  metric_limits: Record<string, { enabled: boolean; min?: number; max?: number; lock?: string; options?: string[] }>
}


const IconCheck = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
)
const IconSpin = () => (
  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
    style={{ animation: 'spin 0.8s linear infinite' }}>
    <path d="M21 12a9 9 0 1 1-9-9" />
  </svg>
)

const INPUT_BASE: React.CSSProperties = {
  background: 'var(--bg-card)', border: '1px solid var(--border)',
  borderRadius: 3, color: 'var(--text)', fontSize: 10,
  fontFamily: 'Consolas, monospace', padding: '2px 5px', outline: 'none', textAlign: 'right',
}

// ── Main component ───────────────────────────────────────────────────────────

export default function TickPanel() {
  const [running,   setRunning]  = useState(false)
  const [paused,    setPaused]   = useState(false)
  const [enabled,   setEnabled]  = useState(false)
  const [interval,  setIv]       = useState(30)
  const [flags,     setFlags]    = useState<Record<string, boolean>>({})
  const [limits,    setLimits]   = useState<Record<string, LimitState>>({})
  const [tab,       setTab]      = useState<'metrics' | 'limits'>('metrics')
  const [busy,      setBusy]     = useState(false)
  const [flash,     setFlash]    = useState(false)
  const [ivFocused, setIvFocused] = useState(false)

  const load = useCallback((s: TickSettings) => {
    setRunning(s.running); setPaused(s.paused); setEnabled(s.running && !s.paused); setIv(s.interval)
    setFlags({ ...s.metric_flags })
    const ls: Record<string, LimitState> = {}
    for (const grp of LIMIT_GROUPS) {
      for (const row of grp.rows) {
        const a = s.metric_limits[row.key]
        if (row.kind === 'num') {
          ls[row.key] = { enabled: a?.enabled ?? false, min: a?.min ?? row.defMin, max: a?.max ?? row.defMax, lock: '', options: [] }
        } else {
          ls[row.key] = { enabled: a?.enabled ?? false, min: 0, max: 100, lock: a?.lock ?? row.options[0], options: a?.options ?? row.options }
        }
      }
    }
    setLimits(ls)
  }, [])

  const fetchSettings = useCallback(() => {
    api.tickSettings().then(d => load(d as TickSettings)).catch(() => {})
  }, [load])

  useEffect(() => { fetchSettings() }, [fetchSettings])

  async function handleApply() {
    setBusy(true)
    try {
      await api.applyTickSettings({
        paused:        !enabled,
        interval,
        metric_flags:  flags,
        metric_limits: Object.fromEntries(
          Object.entries(limits).map(([k, v]) => [k, { enabled: v.enabled, min: v.min, max: v.max, lock: v.lock }])
        ),
      })
      fetchSettings()
      setFlash(true); setTimeout(() => setFlash(false), 1800)
    } catch { /* ignore */ }
    finally { setBusy(false) }
  }

  const tickerOn = running && !paused

  // badge
  let badgeCls: string, badgeDot: string, badgeTxt: string
  if (running && !paused) { badgeCls = 'running'; badgeDot = 'green';  badgeTxt = 'Running' }
  else if (paused)         { badgeCls = 'ready';   badgeDot = 'yellow'; badgeTxt = 'Paused'  }
  else                     { badgeCls = 'stopped'; badgeDot = 'grey';   badgeTxt = 'Idle'    }

  function setFlag(key: string) {
    setFlags(f => ({ ...f, [key]: !(f[key] ?? true) }))
  }
  function setAll(v: boolean) {
    const all: Record<string, boolean> = {}
    METRIC_GROUPS.forEach(g => g.rows.forEach(r => { all[r.key] = v }))
    setFlags(all)
  }
  function toggleLimit(key: string) {
    setLimits(prev => ({ ...prev, [key]: { ...prev[key], enabled: !prev[key].enabled } }))
  }
  function setLimitField(key: string, field: keyof LimitState, val: number | string | boolean) {
    setLimits(prev => ({ ...prev, [key]: { ...prev[key], [field]: val } }))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

      {/* Header */}
      <div className="panel-header">
        <span className="title">Metrics Tick</span>
        <span className={`badge ${badgeCls}`}>
          <span className={`status-dot ${badgeDot}`} />
          {badgeTxt}
        </span>
      </div>

      {/* Tab switcher */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)' }}>
        {([['metrics', 'Metrics'], ['limits', 'Limits']] as const).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            style={{
              flex: 1,
              borderRadius: 0,
              border: 'none',
              borderRight: '1px solid var(--border)',
              borderBottom: tab === id ? '2px solid var(--accent)' : '2px solid transparent',
              background: tab === id ? 'var(--bg-selected)' : 'transparent',
              color: tab === id ? 'var(--text)' : 'var(--text-muted)',
              padding: '5px 4px',
              fontSize: 10,
              cursor: 'pointer',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Scrollable content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 10px', display: 'flex', flexDirection: 'column', gap: 12 }}>

        {tab === 'metrics' && <>

          {/* Ticker control */}
          <div className="group-box" style={{ marginTop: 6 }}>
            <span className="group-box-label">Ticker Control</span>

            <div className="field-row-split" style={{ marginBottom: 8 }}>
              <span className="label">Enabled</span>
              <label className="toggle">
                <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} disabled={!running} />
                <span className="toggle-slider" />
              </label>
            </div>

            <div className="field-row-split">
              <span className="label">Interval</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <input
                  type="number" min={1} max={3600} value={interval}
                  onChange={e => setIv(Math.max(1, parseInt(e.target.value) || 1))}
                  onFocus={() => setIvFocused(true)}
                  onBlur={() => setIvFocused(false)}
                  style={{
                    width: 70,
                    background: '#0d1117',
                    border: `1px solid ${ivFocused ? '#58a6ff' : 'var(--border)'}`,
                    borderRadius: 4,
                    color: ivFocused ? '#58a6ff' : 'var(--text)',
                    fontSize: 13,
                    fontFamily: 'monospace',
                    fontWeight: 700,
                    padding: '4px 8px',
                    outline: 'none',
                    transition: 'border-color 0.15s, color 0.15s',
                  }}
                />
                <span style={{ fontSize: 9, fontFamily: 'monospace', color: 'var(--text-muted)', opacity: 0.5 }}>s · 1–3600</span>
              </div>
            </div>
          </div>

          {/* Metric groups */}
          {METRIC_GROUPS.map(grp => (
            <div key={grp.gid} className="group-box">
              <span className="group-box-label">{grp.title}</span>
              {grp.rows.map(row => (
                <label
                  key={row.key}
                  title={row.tip}
                  style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '2.5px 0', cursor: 'pointer' }}
                >
                  <input type="checkbox" checked={flags[row.key] ?? true} onChange={() => setFlag(row.key)} style={{ cursor: 'pointer' }} />
                  <span style={{ fontSize: 10, color: (flags[row.key] ?? true) ? 'var(--text)' : 'var(--text-muted)' }}>
                    {row.label}
                    {row.derived && <span style={{ marginLeft: 3, fontSize: 8, color: '#58a6ff', fontStyle: 'normal' }}>ⓘ</span>}
                  </span>
                </label>
              ))}
            </div>
          ))}

          {/* All / None */}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6, paddingBottom: 4 }}>
            {[['All', true], ['None', false]].map(([lbl, v]) => (
              <button key={String(lbl)} onClick={() => setAll(v as boolean)} style={{
                fontSize: 9, padding: '3px 11px', borderRadius: 4,
                background: 'transparent', border: '1px solid var(--border)',
                color: 'var(--text-muted)', cursor: 'pointer',
              }}>{lbl}</button>
            ))}
          </div>

        </>}

        {tab === 'limits' && <>

          {/* Hint */}
          <div style={{
            fontSize: 9, color: 'var(--text-muted)', padding: '5px 8px', marginTop: 4,
            background: 'var(--bg-card)', border: '1px solid var(--border)',
            borderRadius: 4, lineHeight: 1.6,
          }}>
            <span style={{ color: 'var(--accent)' }}>ⓘ</span>
            {' '}Check a row to constrain it. Numeric: clamps the walk output. State: forces a fixed value.
          </div>

          {LIMIT_GROUPS.map(grp => (
            <div key={grp.gid} className="group-box">
              <span className="group-box-label">{grp.title}</span>
              {grp.rows.map(row => {
                const lim = limits[row.key]
                if (!lim) return null
                return (
                  <div key={row.key} style={{
                    display: 'flex', alignItems: 'center', gap: 6, padding: '3px 0',
                    opacity: lim.enabled ? 1 : 0.52, transition: 'opacity 0.15s',
                  }}>
                    <input type="checkbox" checked={lim.enabled} onChange={() => toggleLimit(row.key)} style={{ cursor: 'pointer' }} />

                    <span style={{ fontSize: 10, color: 'var(--text)', width: 78, flexShrink: 0, fontFamily: 'Consolas, monospace' }}>
                      {row.label}
                    </span>

                    {row.kind === 'num' ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 3, flex: 1, minWidth: 0 }}>
                        <input
                          type="number" min={row.absMin} max={row.absMax} step={row.step}
                          value={lim.min} disabled={!lim.enabled}
                          onChange={e => setLimitField(row.key, 'min', parseFloat(e.target.value) || 0)}
                          style={{ ...INPUT_BASE, width: 46, opacity: lim.enabled ? 1 : 0.5 }}
                        />
                        <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>→</span>
                        <input
                          type="number" min={row.absMin} max={row.absMax} step={row.step}
                          value={lim.max} disabled={!lim.enabled}
                          onChange={e => setLimitField(row.key, 'max', parseFloat(e.target.value) || 0)}
                          style={{ ...INPUT_BASE, width: 46, opacity: lim.enabled ? 1 : 0.5 }}
                        />
                        <span style={{ fontSize: 9, color: 'var(--text-muted)', flexShrink: 0 }}>{row.suffix}</span>
                      </div>
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flex: 1, minWidth: 0 }}>
                        <span style={{ fontSize: 9, color: 'var(--text-muted)', flexShrink: 0 }}>force</span>
                        <select
                          value={lim.lock} disabled={!lim.enabled}
                          onChange={e => setLimitField(row.key, 'lock', e.target.value)}
                          style={{
                            flex: 1, minWidth: 0,
                            background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 3,
                            color: 'var(--text)', fontSize: 10, fontFamily: 'Consolas, monospace',
                            padding: '2px 5px', outline: 'none', cursor: lim.enabled ? 'pointer' : 'default',
                            opacity: lim.enabled ? 1 : 0.5,
                          }}
                        >
                          {(lim.options.length > 0 ? lim.options : (row as StateMeta).options).map(opt => (
                            <option key={opt} value={opt}>{opt}</option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </>}

      </div>

      {/* Pinned footer: Apply sticks to the bottom */}
      <div style={{ flexShrink: 0, padding: '8px 10px 12px',
                    borderTop: '1px solid var(--border)' }}>
        <div className="snmp-actions">
          <button
            className={`btn-action ${flash ? 'btn-stop' : 'btn-start'}`}
            onClick={handleApply}
            disabled={busy}
            style={{ flex: 1 }}
          >
            {busy ? <IconSpin /> : <IconCheck />}
            <span>{busy ? 'Applying…' : flash ? 'Applied' : 'Apply'}</span>
          </button>
        </div>
      </div>

    </div>
  )
}
