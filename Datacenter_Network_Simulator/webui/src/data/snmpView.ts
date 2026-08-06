// What every surface should SAY about a device's SNMP reachability — device list
// column, canvas tooltip, device-info modal. One helper so the three cannot
// disagree with each other or with the server.
//
// Two things are easy to print wrong:
//   * snmp_port is the CONFIGURED intent (static, defaults 161). The simulator
//     serves on whatever port was chosen at start — 1611 is normal, since 161 is
//     privileged. Echoing 161 points an operator at a closed socket.
//   * Having a port is not having an agent. BACnet/Modbus plant gear (chiller,
//     pump, cooling tower, valve) and passive panels (RPP) carry no SNMP card,
//     yet still hold snmp_port=161 in the record.
//
// Both are answered by the server (snmp_agent / snmp_effective_port, from
// api/routers/_snmp_view.py) — this module only formats them.

import { PASSIVE_DEVICE_TYPES } from './deviceConstants'

export interface SnmpViewable {
  device_type: string
  snmp_port?: number
  snmp_agent?: boolean
  snmp_effective_port?: number | null
}

// PASSIVE_DEVICE_TYPES is the fallback only — a payload from a server that
// predates snmp_agent knows nothing about BACnet plant gear.
export function hasSnmpAgent(d: SnmpViewable): boolean {
  if (d.snmp_agent !== undefined) return d.snmp_agent
  return !PASSIVE_DEVICE_TYPES.has(d.device_type)
}

export interface SnmpCell {
  text: string       // compact form, for the device-list column
  label: string      // row label for tooltip / info modal
  value: string      // row value for tooltip / info modal
  title: string      // hover explanation
  live: boolean      // an agent is answering right now
}

export function snmpCell(d: SnmpViewable): SnmpCell {
  const configured = d.snmp_port ?? 161
  if (!hasSnmpAgent(d)) {
    return {
      text: '—',
      label: 'SNMP',
      value: 'No agent',
      title: 'No SNMP agent — BACnet/Modbus or passive device',
      live: false,
    }
  }
  if (d.snmp_effective_port) {
    return {
      text: String(d.snmp_effective_port),
      label: 'SNMP Port',
      value: String(d.snmp_effective_port),
      title: `Serving on UDP ${d.snmp_effective_port}`,
      live: true,
    }
  }
  return {
    text: String(configured),
    label: 'SNMP Port',
    value: `${configured} (not serving)`,
    title: `SNMP simulator not serving this device — ${configured} is the configured port`,
    live: false,
  }
}

// Sort on what the cell shows, not the configured field behind it. No agent sorts
// below every real port.
export function snmpSortValue(d: SnmpViewable): number {
  if (!hasSnmpAgent(d)) return -1
  return d.snmp_effective_port || d.snmp_port || 0
}
