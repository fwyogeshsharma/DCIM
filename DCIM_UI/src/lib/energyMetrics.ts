import type { EnergyReading } from './types'

// ── Known metric_name / scope strings ────────────────────────────────────────
// Centralised so a producer (DCS) naming mismatch is a one-line fix rather than
// a hunt through components. If the UI shows blanks where data should be, verify
// these against the actual energy_metrics rows.
export const ENERGY = {
  ACTIVE_POWER_KW:     'energy.active_power_kw',     // panel total active power
  ENERGY_KWH:          'energy.energy_kwh',          // panel cumulative energy
  CIRCUIT_CURRENT_A:   'energy.circuit_current_a',
  CIRCUIT_POWER_KW:    'energy.circuit_power_kw',
  CIRCUIT_ENERGY_KWH:  'energy.circuit_energy_kwh',
  VOLTAGE_V:           'energy.voltage_v',
  CURRENT_A:           'energy.current_a',
  BATTERY_PERCENT:     'energy.battery_percent',     // UPS state of charge
  RUNTIME_MIN:         'energy.runtime_min',          // UPS estimated runtime
} as const

// attributes.scope values that classify what a meter covers, for PUE.
export const SCOPE = {
  FACILITY: 'facility',  // mains / total facility draw
  IT:       'it',        // IT load (servers, network)
  COOLING:  'cooling',   // CRAC / chillers / non-IT
} as const

// Normalise a raw scope string to a known bucket (case/spacing tolerant).
export function normalizeScope(raw: string | null | undefined): string | null {
  if (!raw) return null
  const s = raw.trim().toLowerCase()
  if (s === SCOPE.FACILITY || s === 'total' || s === 'mains') return SCOPE.FACILITY
  if (s === SCOPE.IT || s === 'it_load' || s === 'load') return SCOPE.IT
  if (s === SCOPE.COOLING || s === 'crac' || s === 'hvac' || s === 'chiller') return SCOPE.COOLING
  return s
}

// Infer a meter's scope from its hostname when no explicit attributes.scope is
// set. Energy meters are named by what they feed: *-RPP* / PDU = IT racks,
// CHILLER/COOL/CRAH/tower/pump = cooling plant, FAC/MAINS/MSB = facility/house.
// Used as a fallback so PUE bifurcation works even before meters are tagged.
export function scopeFromHostname(hostname: string | null | undefined): string | null {
  const h = (hostname || '').toLowerCase()
  if (!h) return null
  if (/chiller|cooling|\bcool\b|cool-|crah|crac|tower|\bpump\b|cond|hvac|\bchw\b|\bcw\b/.test(h)) return SCOPE.COOLING
  if (/\brpp\b|rpp\d|-rpp|\bpdu\b|busway|\brack\b|\bit\b|server/.test(h)) return SCOPE.IT
  if (/\bfac\b|fac-|-fac|facility|\bmains\b|\bmsb\b|utility|incomer|house|\baux\b/.test(h)) return SCOPE.FACILITY
  return null
}

// Resolve a meter's scope: explicit attributes.scope wins, else infer from the
// hostname. Returns a normalised bucket ('facility' | 'it' | 'cooling' | …) or null.
export function resolveScope(rawScope: string | null | undefined, hostname: string | null | undefined): string | null {
  return normalizeScope(rawScope) ?? scopeFromHostname(hostname)
}

// Presentation metadata for energy_metrics. Each metric_name is one physical
// quantity; we map the known Verdigris/BACnet names to a short label + unit, and
// fall back to prettifying the raw name (strip "energy." prefix, drop the unit
// suffix token) for anything not yet enumerated.

interface MetricMeta { label: string; unit: string }

const METRIC_META: Record<string, MetricMeta> = {
  'energy.active_power_kw':          { label: 'Active Power',     unit: 'kW' },
  'energy.reactive_power_kvar':      { label: 'Reactive Power',   unit: 'kVAR' },
  'energy.apparent_power_kva':       { label: 'Apparent Power',   unit: 'kVA' },
  'energy.power_factor':             { label: 'Power Factor',     unit: '' },
  'energy.frequency_hz':             { label: 'Frequency',        unit: 'Hz' },
  'energy.voltage_v':                { label: 'Voltage',          unit: 'V' },
  'energy.current_a':                { label: 'Current',          unit: 'A' },
  'energy.energy_kwh':               { label: 'Energy',           unit: 'kWh' },
  'energy.harmonic_current_percent': { label: 'Harmonic Current', unit: '%' },
  'energy.thd_percent':              { label: 'THD',              unit: '%' },
  'energy.circuit_current_a':        { label: 'Current',          unit: 'A' },
  'energy.circuit_power_kw':         { label: 'Power',            unit: 'kW' },
  'energy.circuit_energy_kwh':       { label: 'Energy',           unit: 'kWh' },
}

const UNIT_SUFFIXES: Record<string, string> = {
  kw: 'kW', kwh: 'kWh', kvar: 'kVAR', kva: 'kVA', v: 'V', a: 'A',
  hz: 'Hz', percent: '%', w: 'W', wh: 'Wh',
}

export function energyMetricMeta(metricName: string): MetricMeta {
  const known = METRIC_META[metricName]
  if (known) return known

  // Fallback: "energy.foo_bar_kw" → label "Foo Bar", unit "kW".
  const stripped = metricName.replace(/^energy\./, '')
  const parts = stripped.split('_')
  const last = parts[parts.length - 1]?.toLowerCase() ?? ''
  let unit = ''
  let words = parts
  if (UNIT_SUFFIXES[last]) {
    unit = UNIT_SUFFIXES[last]
    words = parts.slice(0, -1)
  }
  const label = words
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ') || stripped
  return { label, unit }
}

// Format a value with sensible precision for its unit.
export function formatEnergyValue(value: number, unit: string): string {
  const v = unit === 'A' || unit === '%' || unit === ''
    ? value.toFixed(2)
    : unit === 'V' || unit === 'Hz'
      ? value.toFixed(1)
      : value.toFixed(2)
  return unit ? `${v} ${unit}` : v
}

// Alarm / status metrics are booleans encoded as a number: 0 = clear (false),
// anything > 0 = active (true). Detected by an `alarm` token anywhere in the
// metric name (e.g. energy.alarm_high_thd, energy.alarm_overcurrent,
// energy.alarm_phase_loss, energy.alarm_sensor_fault, energy.alarm_voltage_imbalance).
export function isAlarmMetric(metricName: string): boolean {
  return /(^|[._])alarm([._]|$)/i.test(metricName)
}

// Render an alarm metric's numeric value as a boolean string.
export function formatAlarmValue(value: number): 'True' | 'False' {
  return value > 0 ? 'True' : 'False'
}

export type EnergyCategory = 'panel' | 'phase' | 'circuit' | 'harmonic'

export function classifyReading(r: EnergyReading): EnergyCategory {
  if (r.circuit) return 'circuit'
  if (r.phase) return 'phase'
  if (/^H\d+$/i.test(r.tag)) return 'harmonic'
  return 'panel'
}
