// Maps telemetry metric keys and OpenConfig / gNMI paths to human-readable labels.
//
// Two lookup tables are maintained:
//   FLAT_METRIC_LABELS  — flat dotted keys from the metrics / energy_metrics DB
//                         tables (e.g. `interface.bytes_received`, `cooling.cop`)
//   PATH_LABELS         — deep OpenConfig / gNMI paths as emitted by network
//                         agents (e.g. `interfaces/interface/state/counters/in-octets`)
//
// getReadablePath() checks FLAT_METRIC_LABELS first (exact match on the raw key),
// then falls back to PATH_LABELS with full path normalisation, then title-cases the
// leaf segment as a last resort — so every metric always shows a clean label.

// ── Flat metric-key labels (metrics + energy_metrics tables) ─────────────────

const FLAT_METRIC_LABELS: Record<string, string> = {
  // interface
  'interface.address':                        'Interface IP Address',
  'interface.admin_status':                   'Admin Status',
  'interface.operational_status':             'Operational Status',
  'interface.speed_mbps':                     'Speed (Mbps)',
  'interface.bytes_received':                 'Bytes Received',
  'interface.bytes_sent':                     'Bytes Sent',
  'interface.bytes_received_hc':              'Bytes Received (HC)',
  'interface.bytes_sent_hc':                  'Bytes Sent (HC)',
  'interface.packets_received_unicast':       'Unicast Packets Received',
  'interface.packets_sent_unicast':           'Unicast Packets Sent',
  'interface.errors_received':                'Receive Errors',
  'interface.errors_sent':                    'Transmit Errors',
  'interface.discards_received':              'Receive Discards',
  'interface.discards_sent':                  'Transmit Discards',

  // system
  'system.uptime_centiseconds':               'Uptime (centiseconds)',
  'system.cpu_utilization_percent':           'CPU Utilization (%)',
  'system.memory_total_bytes':                'Memory Total (bytes)',
  'system.memory_used_bytes':                 'Memory Used (bytes)',
  'system.memory_utilization_percent':        'Memory Utilization (%)',

  // server
  'server.cpu_per_core_percent':              'CPU Per-Core (%)',
  'server.cpu_user_percent':                  'CPU User Time (%)',
  'server.cpu_system_percent':                'CPU System Time (%)',
  'server.cpu_idle_percent':                  'CPU Idle (%)',
  'server.cpu_percent':                       'CPU Utilization (%)',
  'server.memory_total_kb':                   'Memory Total (KB)',
  'server.memory_available_kb':               'Memory Available (KB)',
  'server.memory_cached_kb':                  'Memory Cached (KB)',
  'server.memory_buffer_kb':                  'Memory Buffers (KB)',
  'server.memory_used_bytes':                 'Memory Used (bytes)',
  'server.memory_used_percent':               'Memory Used (%)',
  'server.storage_size_kb':                   'Storage Total (KB)',
  'server.storage_used_kb':                   'Storage Used (KB)',
  'server.storage_available_kb':              'Storage Available (KB)',
  'server.disk_total_bytes':                  'Disk Total (bytes)',
  'server.disk_used_bytes':                   'Disk Used (bytes)',
  'server.disk_used_percent':                 'Disk Used (%)',
  'server.network_rx_mbps':                   'Network RX (Mbps)',
  'server.network_tx_mbps':                   'Network TX (Mbps)',
  'server.alarm_count':                       'Active Alarms',

  // bmc
  'bmc.chassis_power_state':                  'Chassis Power State',
  'bmc.power_draw_w':                         'Power Draw (W)',
  'bmc.psu_output_w':                         'PSU Output (W)',
  'bmc.temp_c':                               'Temperature (°C)',
  'bmc.fan_rpm':                              'Fan Speed (RPM)',
  'bmc.info':                                 'BMC Info',

  // environment / sensors / UPS
  'environment.temperature_c':               'Temperature (°C)',
  'environment.temperature_alarm':            'Temperature Alarm',
  'environment.humidity_percent':             'Humidity (%)',
  'environment.dew_point_c':                  'Dew Point (°C)',
  'environment.airflow_cfm':                  'Airflow (CFM)',
  'environment.ups_battery_status':           'UPS Battery Status',
  'environment.ups_battery_status_ex':        'UPS Battery Status (Extended)',
  'environment.ups_battery_voltage_v':        'UPS Battery Voltage (V)',
  'environment.ups_charge_percent':           'UPS Battery Charge (%)',
  'environment.ups_input_voltage_v':          'UPS Input Voltage (V)',
  'environment.ups_output_voltage_v':         'UPS Output Voltage (V)',
  'environment.ups_output_current_a':         'UPS Output Current (A)',
  'environment.ups_output_load_percent':      'UPS Output Load (%)',
  'environment.ups_seconds_on_battery':       'UPS Seconds on Battery',
  'environment.ups_minutes_remaining':        'UPS Runtime Remaining (min)',
  'environment.ups_fan_status':               'UPS Fan Status',
  'environment.ups_charger_status':           'UPS Charger Status',
  'environment.ups_rectifier_status':         'UPS Rectifier Status',
  'environment.ups_phase_status':             'UPS Phase Status',

  // energy / PDU
  'energy.active_power_kw':                  'Active Power (kW)',
  'energy.energy_kwh':                        'Energy (kWh)',
  'energy.voltage_v':                         'Voltage (V)',
  'energy.current_a':                         'Current (A)',
  'energy.frequency_hz':                      'Frequency (Hz)',
  'energy.power_factor':                      'Power Factor',
  'energy.voltage_thd_percent':               'Voltage THD (%)',
  'energy.current_thd_percent':               'Current THD (%)',
  'energy.harmonic_current_percent':          'Harmonic Current (%)',
  'energy.alarm_overcurrent':                 'Alarm: Overcurrent',
  'energy.alarm_voltage_imbalance':           'Alarm: Voltage Imbalance',
  'energy.alarm_high_thd':                    'Alarm: High THD',
  'energy.alarm_phase_loss':                  'Alarm: Phase Loss',
  'energy.alarm_sensor_fault':                'Alarm: Sensor Fault',
  'energy.circuit_current_a':                 'Circuit Current (A)',
  'energy.circuit_power_kw':                  'Circuit Power (kW)',
  'energy.circuit_energy_kwh':                'Circuit Energy (kWh)',
  'energy.circuit_power_factor':              'Circuit Power Factor',
  'energy.circuit_current_thd_percent':       'Circuit Current THD (%)',

  // cooling — chiller
  'cooling.chw_supply_temp_c':               'CHW Supply Temp (°C)',
  'cooling.chw_return_temp_c':               'CHW Return Temp (°C)',
  'cooling.chw_setpoint_c':                  'CHW Setpoint (°C)',
  'cooling.chw_flow_lps':                    'CHW Flow (L/s)',
  'cooling.cond_supply_temp_c':              'Condenser Supply Temp (°C)',
  'cooling.cond_return_temp_c':              'Condenser Return Temp (°C)',
  'cooling.compressor_load_pct':             'Compressor Load (%)',
  'cooling.active_power_kw':                 'Active Power (kW)',
  'cooling.cooling_capacity_kw':             'Cooling Capacity (kW)',
  'cooling.cop':                             'Coefficient of Performance (COP)',
  'cooling.evap_pressure_kpa':               'Evaporator Pressure (kPa)',
  'cooling.cond_pressure_kpa':               'Condenser Pressure (kPa)',
  'cooling.run_hours_h':                     'Run Hours (h)',
  'cooling.chiller_running':                 'Chiller Running',
  'cooling.alarm_high_pressure':             'Alarm: High Pressure',
  'cooling.alarm_low_evap_temp':             'Alarm: Low Evaporator Temp',
  'cooling.alarm_flow_loss':                 'Alarm: Flow Loss',

  // cooling — pump
  'cooling.pump_speed_pct':                  'Pump Speed (%)',
  'cooling.pump_flow_lps':                   'Pump Flow (L/s)',
  'cooling.discharge_pressure_kpa':          'Discharge Pressure (kPa)',
  'cooling.suction_pressure_kpa':            'Suction Pressure (kPa)',
  'cooling.diff_pressure_kpa':               'Differential Pressure (kPa)',
  'cooling.motor_power_kw':                  'Motor Power (kW)',
  'cooling.motor_temp_c':                    'Motor Temp (°C)',
  'cooling.vfd_frequency_hz':                'VFD Frequency (Hz)',
  'cooling.pump_running':                    'Pump Running',
  'cooling.alarm_fault':                     'Alarm: Fault',
  'cooling.alarm_low_flow':                  'Alarm: Low Flow',

  // cooling — cooling tower
  'cooling.fan_speed_pct':                   'Fan Speed (%)',
  'cooling.basin_temp_c':                    'Basin Temp (°C)',
  'cooling.cond_water_in_c':                 'Condenser Water In (°C)',
  'cooling.cond_water_out_c':                'Condenser Water Out (°C)',
  'cooling.fan_power_kw':                    'Fan Power (kW)',
  'cooling.basin_level_pct':                 'Basin Level (%)',
  'cooling.makeup_flow_lpm':                 'Makeup Flow (L/min)',
  'cooling.vibration_mms':                   'Vibration (mm/s)',
  'cooling.fan_running':                     'Fan Running',
  'cooling.alarm_high_vibration':            'Alarm: High Vibration',
  'cooling.alarm_low_basin':                 'Alarm: Low Basin Level',

  // cooling — valve
  'cooling.valve_position_pct':              'Valve Position (%)',
  'cooling.valve_commanded_position_pct':    'Valve Commanded Position (%)',
  'cooling.actuator_temp_c':                 'Actuator Temp (°C)',
  'cooling.valve_modulating':                'Valve Modulating',
  'cooling.alarm_actuator_fault':            'Alarm: Actuator Fault',

  // cooling — CDU (Coolant Distribution Unit)
  'cooling.tcs_supply_temp_c':              'TCS Supply Temp (°C)',
  'cooling.tcs_return_temp_c':              'TCS Return Temp (°C)',
  'cooling.tcs_setpoint_c':                 'TCS Setpoint (°C)',
  'cooling.tcs_flow_lps':                   'TCS Flow (L/s)',
  'cooling.facility_chw_valve_pct':         'Facility CHW Valve (%)',
  'cooling.facility_chw_flow_lps':          'Facility CHW Flow (L/s)',
  'cooling.heat_load_kw':                   'Heat Load (kW)',
  'cooling.pump_power_kw':                  'Pump Power (kW)',
  'cooling.approach_temp_c':                'Approach Temp (°C)',
  'cooling.filter_dp_kpa':                  'Filter Differential Pressure (kPa)',
  'cooling.cdu_running':                    'CDU Running',
  'cooling.alarm_leak':                     'Alarm: Leak Detected',
  'cooling.alarm_high_supply_temp':         'Alarm: High Supply Temp',
  'cooling.alarm_pump_fault':               'Alarm: Pump Fault',

  // cooling — CRAH
  'cooling.supply_air_temp_c':              'Supply Air Temp (°C)',
  'cooling.return_air_temp_c':              'Return Air Temp (°C)',
  'cooling.crah_setpoint_c':                'CRAH Setpoint (°C)',
  'cooling.chw_valve_pct':                  'CHW Valve Position (%)',
  'cooling.cooling_capacity_pct':           'Cooling Capacity (%)',
  'cooling.supply_humidity_pct':            'Supply Humidity (%)',
  'cooling.airflow_pct':                    'Airflow (%)',
  'cooling.crah_running':                   'CRAH Running',
  'cooling.alarm_high_temp':                'Alarm: High Temp',
  'cooling.alarm_airflow_loss':             'Alarm: Airflow Loss',
  'cooling.alarm_filter_dirty':             'Alarm: Filter Dirty',
}

// ── OpenConfig / gNMI deep-path labels ────────────────────────────────────────

const PATH_LABELS: Record<string, string> = {
  // ── openconfig-interfaces ──────────────────────────────────────────────────
  'interfaces/interface/state/name': 'Interface name',
  'interfaces/interface/state/type': 'Interface type',
  'interfaces/interface/state/mtu': 'MTU',
  'interfaces/interface/state/admin-status': 'Admin status',
  'interfaces/interface/state/oper-status': 'Operational status',
  'interfaces/interface/state/counters/in-octets': 'Bytes received (HC)',
  'interfaces/interface/state/counters/out-octets': 'Bytes sent (HC)',
  'interfaces/interface/state/counters/in-unicast-pkts': 'Packets received unicast',
  'interfaces/interface/state/counters/out-unicast-pkts': 'Packets sent unicast',
  'interfaces/interface/state/counters/in-errors': 'Errors received',
  'interfaces/interface/state/counters/out-errors': 'Errors sent',
  'interfaces/interface/state/counters/in-discards': 'Discards received',
  'interfaces/interface/state/counters/out-discards': 'Discards sent',
  'interfaces/interface/state/counters/last-clear': 'Counter last-clear time',
  'interfaces/interface/ethernet/state/mac-address': 'MAC address',
  'interfaces/interface/ethernet/state/hw-mac-address': 'Hardware MAC address',
  'interfaces/interface/ethernet/state/port-speed': 'Speed',
  'interfaces/interface/ethernet/state/duplex-mode': 'Duplex mode',
  'interfaces/interface/subinterfaces/subinterface/state/admin-status': 'Subinterface admin status',
  'interfaces/interface/subinterfaces/subinterface/state/oper-status': 'Subinterface oper status',
  'interfaces/interface/subinterfaces/subinterface/ipv4/addresses/address/state/ip': 'IPv4 address',
  'interfaces/interface/subinterfaces/subinterface/ipv4/addresses/address/state/prefix-length': 'IPv4 prefix length',
  'interfaces/interface/subinterfaces/subinterface/ipv4/addresses/address/state/origin': 'IPv4 address origin',

  // ── openconfig-platform (temperature) ──────────────────────────────────────
  'components/component/state/name': 'Component name',
  'components/component/state/type': 'Component type',
  'components/component/state/description': 'Component description',
  'components/component/state/temperature/instant': 'Temperature current (°C)',
  'components/component/state/temperature/avg': 'Temperature average',
  'components/component/state/temperature/min': 'Temperature minimum',
  'components/component/state/temperature/max': 'Temperature maximum',
  'components/component/state/temperature/alarm-status': 'Temperature alarm active',
  'components/component/state/temperature/alarm-severity': 'Temperature alarm severity',
  'components/component/state/temperature/alarm-threshold': 'Temperature alarm threshold (°C)',

  // ── openconfig-system ──────────────────────────────────────────────────────
  'system/state/hostname': 'Hostname',
  'system/state/domain-name': 'Domain name',
  'system/state/boot-time': 'Boot time',
  'system/state/uptime': 'Uptime',
  'system/state/software-version': 'Software version',
  'system/state/os-name': 'OS name',
  'system/memory/state/physical': 'Memory total bytes',
  'system/memory/state/reserved': 'Memory used bytes',
  'system/memory/state/free': 'Memory free bytes',
  'system/memory/state/utilized': 'Memory utilization (%)',
  'system/cpus/cpu/state/total/instant': 'CPU utilization current (%)',
  'system/cpus/cpu/state/total/avg': 'CPU utilization average (%)',
  'system/cpus/cpu/state/total/min': 'CPU utilization minimum (%)',
  'system/cpus/cpu/state/total/max': 'CPU utilization maximum (%)',

  // ── openconfig-lldp ────────────────────────────────────────────────────────
  'lldp/state/enabled': 'LLDP enabled',
  'lldp/state/hello-timer': 'LLDP hello timer',
  'lldp/state/hold-multiplier': 'LLDP hold multiplier',
  'lldp/interfaces/interface/neighbors/neighbor/state/id': 'Neighbor id',
  'lldp/interfaces/interface/neighbors/neighbor/state/chassis-id': 'Neighbor chassis-id',
  'lldp/interfaces/interface/neighbors/neighbor/state/chassis-id-type': 'Neighbor chassis-id type',
  'lldp/interfaces/interface/neighbors/neighbor/state/port-id': 'Neighbor port-id',
  'lldp/interfaces/interface/neighbors/neighbor/state/port-id-type': 'Neighbor port-id type',
  'lldp/interfaces/interface/neighbors/neighbor/state/port-description': 'Neighbor port description',
  'lldp/interfaces/interface/neighbors/neighbor/state/system-name': 'Neighbor system name',
  'lldp/interfaces/interface/neighbors/neighbor/state/system-description': 'Neighbor system description',
  'lldp/interfaces/interface/neighbors/neighbor/state/management-address': 'Neighbor management address',
  'lldp/interfaces/interface/neighbors/neighbor/state/management-address-type': 'Neighbor management address type',

  // ── openconfig-network-instance — VLANs / FDB (switch) ─────────────────────
  'network-instances/network-instance/vlans/vlan/state/vlan-id': 'VLAN id',
  'network-instances/network-instance/vlans/vlan/state/name': 'VLAN name',
  'network-instances/network-instance/vlans/vlan/state/status': 'VLAN status',
  'network-instances/network-instance/fdb/mac-table/entries/entry/state/mac-address': 'FDB MAC address',
  'network-instances/network-instance/fdb/mac-table/entries/entry/state/vlan': 'FDB VLAN',
  'network-instances/network-instance/fdb/mac-table/entries/entry/state/entry-type': 'FDB entry type',
  'network-instances/network-instance/fdb/mac-table/entries/entry/state/age': 'FDB age',
  'network-instances/network-instance/fdb/mac-table/entries/entry/interface/interface-ref/state/interface': 'FDB entry interface',
  'network-instances/network-instance/fdb/state/mac-aging-time': 'MAC aging time',
  'network-instances/network-instance/fdb/state/mac-learning': 'MAC learning enabled',

  // ── openconfig-network-instance — BGP (router) ─────────────────────────────
  'network-instances/network-instance/protocols/protocol/bgp/global/state/as': 'Local AS',
  'network-instances/network-instance/protocols/protocol/bgp/global/state/router-id': 'BGP router id',
  'network-instances/network-instance/protocols/protocol/bgp/global/state/total-paths': 'Total paths',
  'network-instances/network-instance/protocols/protocol/bgp/global/state/total-prefixes': 'Total prefixes',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/neighbor-address': 'BGP neighbor address',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/peer-as': 'Neighbor peer AS',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/local-as': 'Neighbor local AS',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/session-state': 'Neighbor session state',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/established-transitions': 'Neighbor established transitions',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/messages/received': 'Neighbor messages received',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/messages/sent': 'Neighbor messages sent',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/timers/state/hold-time': 'Neighbor hold-time',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/timers/state/keepalive-interval': 'Neighbor keepalive interval',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/timers/state/negotiated-hold-time': 'Neighbor negotiated hold-time',

  // ── openconfig-network-instance — OSPF (router) ────────────────────────────
  'network-instances/network-instance/protocols/protocol/ospfv2/global/state/router-id': 'OSPF router id',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/state/identifier': 'Area id',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/state/network-type': 'Interface network type',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/state/metric': 'Interface metric',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/neighbors/neighbor/state/neighbor-id': 'OSPF neighbor id',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/neighbors/neighbor/state/neighbor-address': 'OSPF neighbor address',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/neighbors/neighbor/state/adjacency-state': 'Neighbor adjacency state',

  // ── openconfig-network-instance — AFT (IPv4 routing table) ─────────────────
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/state/prefix': 'Route prefix',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/state/origin-protocol': 'Route origin protocol',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/state/metric': 'Route metric',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/next-hops/next-hop/state/index': 'Next-hop index',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/next-hops/next-hop/state/ip-address': 'Next-hop IP address',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/next-hops/next-hop/state/weight': 'Next-hop weight',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/next-hops/next-hop/state/recurse': 'Next-hop recurse',
}

/** Normalize a telemetry path so it can be matched against PATH_LABELS. */
function normalizePath(raw: string): string {
  return raw
    .trim()
    .replace(/\[[^\]]*\]/g, '')   // drop list keys: interface[name=Ethernet1] → interface
    .replace(/[a-z0-9_-]+:/gi, '') // drop YANG module prefixes: openconfig-if-ip:ipv4 → ipv4
    .replace(/\./g, '/')          // dotted form → slashed form
    .replace(/\/+/g, '/')         // collapse repeated slashes
    .replace(/^\/|\/$/g, '')      // trim leading/trailing slash
    .toLowerCase()
}

/** Title-case the final segment of a path as a readable fallback. */
function prettifyLeaf(normalized: string): string {
  const leaf = normalized.split('/').pop() || normalized
  return leaf.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/**
 * Convert a metric key or OpenConfig / gNMI path into a human-readable label.
 *
 * Resolution order:
 *  1. Exact match in FLAT_METRIC_LABELS (handles `interface.bytes_received` etc.)
 *  2. Exact match in PATH_LABELS after full path normalisation
 *  3. Title-cased leaf segment as a safe fallback
 */
export function getReadablePath(path: string, key?: string): string {
  // 1. Flat metric-key lookup (exact, case-insensitive)
  const flatKey = key ? `${path}.${key}` : path
  const flatLabel = FLAT_METRIC_LABELS[flatKey.toLowerCase()] ?? FLAT_METRIC_LABELS[path.toLowerCase()]
  if (flatLabel) return flatLabel

  // 2. OpenConfig / gNMI deep-path lookup
  // The backend sometimes carries the leaf inside `path` and sometimes splits it
  // out into `key`, so try the combined path first, then `path` on its own.
  const candidates = key ? [`${path}/${key}`, path] : [path]
  for (const candidate of candidates) {
    if (!candidate) continue
    const label = PATH_LABELS[normalizePath(candidate)]
    if (label) return label
  }

  // 3. Title-case leaf fallback
  const best = candidates.find(Boolean)
  return best ? prettifyLeaf(normalizePath(best)) : (key || path)
}
