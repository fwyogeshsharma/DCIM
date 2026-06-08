// Derive CPU% / RAM% utilisation from the latest raw metric readings.
//
// Two producers feed the metrics table with different metric names:
//   • Servers (UCD-SNMP): server.cpu_*_percent + server.memory_*_kb
//       - CPU%  = 100 − cpu_idle_percent (fallback: user + system)
//       - RAM%  = (memory_total_kb − memory_available_kb) / memory_total_kb
//   • Network gear (gNMI / openconfig-system): system.* — already gives
//     *_utilization_percent directly (with total/used bytes as a fallback).
//
// Centralised here so the inventory list (backend mirrors this in SQL), the
// device detail panel, and any future consumer agree on the derivation.

// Metric names that feed CPU/RAM derivation — kept in sync with the SQL in
// DCIM_Aggregator inventory `/devices` so list + detail show the same numbers.
export const HEALTH_METRIC_NAMES = [
  'system.cpu_utilization_percent',
  'system.memory_utilization_percent',
  'system.memory_total_bytes',
  'system.memory_used_bytes',
  'server.cpu_idle_percent',
  'server.cpu_user_percent',
  'server.cpu_system_percent',
  'server.memory_total_kb',
  'server.memory_available_kb',
] as const

const clampPct = (v: number) => Math.min(100, Math.max(0, v))

// CPU utilisation %, or null when no CPU metric is present for the device.
export function deriveCpuPct(m: Record<string, number>): number | null {
  // Network devices report utilisation directly.
  if (m['system.cpu_utilization_percent'] != null) return clampPct(m['system.cpu_utilization_percent'])
  // Servers: busy = 100 − idle.
  if (m['server.cpu_idle_percent'] != null) return clampPct(100 - m['server.cpu_idle_percent'])
  // Servers without idle: sum the active buckets we have.
  if (m['server.cpu_user_percent'] != null || m['server.cpu_system_percent'] != null)
    return clampPct((m['server.cpu_user_percent'] ?? 0) + (m['server.cpu_system_percent'] ?? 0))
  // Legacy generic metric.
  if (m['cpu_usage'] != null) return clampPct(m['cpu_usage'])
  return null
}

// Total installed RAM in GB, or null when no memory-total metric is present.
// system.memory_total_bytes (gNMI) ÷ 1024³, else server.memory_total_kb ÷ 1024².
export function deriveRamTotalGb(m: Record<string, number>): number | null {
  const tb = m['system.memory_total_bytes']
  if (tb != null && tb > 0) return tb / (1024 ** 3)
  const tk = m['server.memory_total_kb']
  if (tk != null && tk > 0) return tk / (1024 ** 2)
  return null
}

// RAM utilisation %, or null when no memory metric is present for the device.
export function deriveRamPct(m: Record<string, number>): number | null {
  // Network devices report utilisation directly.
  if (m['system.memory_utilization_percent'] != null) return clampPct(m['system.memory_utilization_percent'])
  // gNMI total/used bytes.
  const tb = m['system.memory_total_bytes']
  const ub = m['system.memory_used_bytes']
  if (tb != null && tb > 0 && ub != null) return clampPct((ub / tb) * 100)
  // Servers (UCD-SNMP): (total − available) / total.
  const tk = m['server.memory_total_kb']
  const ak = m['server.memory_available_kb']
  if (tk != null && tk > 0 && ak != null) return clampPct(((tk - ak) / tk) * 100)
  // Legacy generic metric.
  if (m['memory_usage'] != null) return clampPct(m['memory_usage'])
  return null
}
