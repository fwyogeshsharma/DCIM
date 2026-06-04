import type { EnergyReading } from './types'

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

export type EnergyCategory = 'panel' | 'phase' | 'circuit' | 'harmonic'

export function classifyReading(r: EnergyReading): EnergyCategory {
  if (r.circuit) return 'circuit'
  if (r.phase) return 'phase'
  if (/^H\d+$/i.test(r.tag)) return 'harmonic'
  return 'panel'
}
