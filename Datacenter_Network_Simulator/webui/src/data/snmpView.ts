// What every surface should SAY about a device's SNMP reachability — device list
// column, canvas tooltip, device-info modal. One helper so the three cannot
// disagree with each other or with the simulator.
//
// It joins two halves that refresh at DIFFERENT rates, which is the whole point:
//   * static, from the device payload — does it have an agent at all, and on which
//     addresses (snmp_agent / snmp_ips). Changes only with the topology, and
//     /devices + /topology/graph are fetched only on topology change.
//   * live, from /snmp/status — the port actually bound and the endpoints being
//     served, polled every 4 s.
// Baking the live port into the device payload froze it at page-load time: start
// the sim afterwards and every tooltip still read "161 (not serving)" while the
// simulator was answering on 1611.
//
// Two things are easy to get wrong on the static side too:
//   * snmp_port is the CONFIGURED intent (static, defaults 161). The sim serves on
//     whatever port was chosen at start — 1611 is normal, since 161 is privileged.
//   * Having a port is not having an agent. BACnet/Modbus plant gear (chiller,
//     pump, cooling tower, valve) and passive panels (RPP) carry no SNMP card, yet
//     still hold snmp_port=161 in the record.

import { PASSIVE_DEVICE_TYPES } from './deviceConstants'

export interface SnmpViewable {
  device_type: string
  snmp_port?: number
  snmp_agent?: boolean
  snmp_ips?: string[]
  mgmt_ip?: string
  ip_address?: string
}

// The live half, derived from SnmpStatus.active_endpoints (already gated on
// is_ready() server-side — non-empty means a socket really answers there).
export interface SnmpServing {
  port: number | null
  ips: Set<string>
}

export const SNMP_NOT_SERVING: SnmpServing = { port: null, ips: new Set() }

export function buildSnmpServing(endpoints: string[] | undefined): SnmpServing {
  if (!endpoints || endpoints.length === 0) return SNMP_NOT_SERVING
  const ips = new Set<string>()
  let port: number | null = null
  for (const e of endpoints) {
    const cut = e.lastIndexOf(':')
    if (cut < 0) continue
    ips.add(e.slice(0, cut))
    if (port === null) port = Number(e.slice(cut + 1)) || null
  }
  return { port, ips }
}

// PASSIVE_DEVICE_TYPES is the fallback only — a payload from a server that
// predates snmp_agent knows nothing about BACnet plant gear.
export function hasSnmpAgent(d: SnmpViewable): boolean {
  if (d.snmp_agent !== undefined) return d.snmp_agent
  return !PASSIVE_DEVICE_TYPES.has(d.device_type)
}

// Addresses to test against the served set. snmp_ips is the server's own answer
// (NOS agent + BMC); the mgmt/prod fallback only matters for an older payload.
function agentIps(d: SnmpViewable): string[] {
  if (d.snmp_ips && d.snmp_ips.length) return d.snmp_ips
  return [d.mgmt_ip || '', d.ip_address || ''].filter(Boolean)
}

export interface SnmpCell {
  text: string       // compact form, for the device-list column
  label: string      // row label for tooltip / info modal
  value: string      // row value for tooltip / info modal
  title: string      // hover explanation
  live: boolean      // an agent is answering right now
}

export function snmpCell(d: SnmpViewable, serving: SnmpServing = SNMP_NOT_SERVING): SnmpCell {
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
  if (serving.port !== null && agentIps(d).some(ip => serving.ips.has(ip))) {
    return {
      text: String(serving.port),
      label: 'SNMP Port',
      value: String(serving.port),
      title: `Serving on UDP ${serving.port}`,
      live: true,
    }
  }
  // Has an agent, but nothing is bound for it: simulator stopped, or the device
  // was hot-added after it started, so no dataset is being served yet.
  return {
    text: String(configured),
    label: 'SNMP Port',
    value: `${configured} (not serving)`,
    title: serving.port === null
      ? `SNMP simulator not running — ${configured} is the configured port`
      : `Not served by the running simulator (started before this device existed) — ${configured} is the configured port`,
    live: false,
  }
}

// Sort on what the cell shows, not the configured field behind it. No agent sorts
// below every real port.
export function snmpSortValue(d: SnmpViewable, serving: SnmpServing = SNMP_NOT_SERVING): number {
  if (!hasSnmpAgent(d)) return -1
  const cell = snmpCell(d, serving)
  return Number(cell.text) || 0
}
