import { useState, useEffect } from 'react'
import { api } from '../api/client'

// source: which measurement plane the backend used, best first.
//   native   — the gear's own meters (switchgear / UPS output / MCC)
//   meters   — EV2 sub-meter hierarchy
//   computed — internal sums; no usable metering
type PueSource = 'native' | 'meters' | 'computed'
type PowerSummary = {
  it_watts: number; cooling_watts: number; facility_watts: number
  pue: number; source?: PueSource
}

function fmtKW(w: number): string {
  const kw = w / 1000
  return kw >= 1000 ? `${(kw / 1000).toFixed(2)} MW` : `${kw.toFixed(1)} kW`
}

type PlantHealth = {
  degraded?: boolean
  degraded_dcs?: string[]
  tripped?: { name: string; dc?: string }[]
}

// PUE colour band: efficient (<1.4) green, typical (1.4–2.0) amber, poor (>2.0) red
//
// `degraded` overrides the band to a dim, uncoloured reading. PUE is a ratio, not a
// health metric, and it moves the WRONG way during a cooling failure: the live
// campaign measured a total loss of every chiller taking PUE from 1.426 to 1.284,
// because the compressors stopped drawing while server fans ramped into the hot
// hall. Both movements shrink facility ÷ IT. So the worse the plant is doing, the
// greener this number goes — which is exactly when an operator must not read it as
// good news. It is not recoloured RED either: the value is arithmetically correct,
// it simply stops meaning efficiency once the plant is not keeping up. The chip
// beside it carries the actual condition.
function pueColor(pue: number, degraded: boolean): string {
  if (degraded) return 'var(--text-dim)'
  if (pue <= 0) return 'var(--text-dim)'
  if (pue < 1.4) return 'var(--ok)'
  if (pue < 2.0) return 'var(--warn)'
  return 'var(--crit)'
}

/**
 * Plant condition, sitting next to PUE because PUE alone is misleading during a
 * cooling failure — it improves as the plant stops working.
 *
 * Three states, worst first:
 *   DEGRADED   cooling is not keeping up in at least one DC — the honest headline
 *   n TRIPPED  chillers latched out on head pressure; they do NOT self-clear and
 *              need a manual reset, so this stays up until someone acts
 *   PLANT OK   deliberately shown rather than rendering nothing. An empty space
 *              is ambiguous: it reads the same whether the plant is healthy or
 *              the endpoint is failing, and "no news" is the wrong default for
 *              the only health signal on the bar.
 */
function PlantHealthChip({ degraded, dcs, tripped }: {
  degraded: boolean
  dcs: string[]
  tripped: { name: string; dc?: string }[]
}) {
  let label = 'PLANT OK'
  let color = 'var(--ok)'
  let title = 'Cooling is keeping up in every datacenter'

  if (tripped.length > 0) {
    label = `${tripped.length} TRIPPED`
    color = 'var(--warn)'
    title = 'Chillers latched out on high head pressure — a latched cutout does '
      + 'not self-clear and needs a manual reset: '
      + tripped.map(t => t.name).join(', ')
  }
  // Degraded outranks trips: a tripped machine whose load the rest of the plant
  // still carries is a warning, but cooling actually falling behind is the thing
  // that ends in hot servers.
  if (degraded) {
    label = dcs.length ? `COOLING DEGRADED · ${dcs.join(' ')}` : 'COOLING DEGRADED'
    color = 'var(--crit)'
    title = 'Cooling is not keeping up' + (dcs.length ? ` in ${dcs.join(', ')}` : '')
      + '. Server inlet temperatures are rising. PUE will FALL while this is true — '
      + 'it is a ratio, not a health metric.'
  }

  return (
    <span title={title}
      style={{
        display: 'flex', alignItems: 'center', gap: 4,
        color, fontWeight: 700, letterSpacing: 0.2,
      }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0,
      }} />
      {label}
    </span>
  )
}

function PowerReadout() {
  const [p, setP] = useState<PowerSummary | null>(null)
  const [h, setH] = useState<PlantHealth | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => {
      api.powerSummary()
        .then(d => { if (alive) setP(d as PowerSummary) })
        .catch(() => {})
      // Plant condition on the SAME cadence as the power figures, so the health
      // chip can never lag the number it qualifies.
      api.chillerTrips()
        .then(d => { if (alive) setH(d as PlantHealth) })
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 3000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (!p || p.facility_watts <= 0) return null
  const degradedDcs = h?.degraded_dcs ?? []
  const tripped = h?.tripped ?? []
  const degraded = !!h?.degraded
  const PUE_SOURCE: Record<PueSource, string> = {
    native:   ' (switchgear + UPS output — Green Grid Category 1)',
    meters:   ' (from EV2 meter readings)',
    computed: ' (estimated — no meter hierarchy)',
  }
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 10, whiteSpace: 'nowrap' }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}
        title={degraded
          ? 'Cooling is DEGRADED — PUE is not a measure of efficiency right now. '
            + 'A plant that has stopped cooling draws less power, so this ratio '
            + 'FALLS during a failure. Read the plant condition, not this number.'
          : `Power Usage Effectiveness = facility ÷ IT${p.source ? PUE_SOURCE[p.source] : ''}`}>
        <span style={{ color: 'var(--text-muted)' }}>PUE</span>
        <span style={{ color: pueColor(p.pue, degraded), fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
          {p.source === 'computed' ? '~' : ''}{p.pue > 0 ? p.pue.toFixed(2) : '—'}
        </span>
      </span>
      <PlantHealthChip degraded={degraded} dcs={degradedDcs} tripped={tripped} />
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }} title="Live IT load">
        <span style={{ color: 'var(--text-muted)' }}>IT</span>
        <span style={{ color: 'var(--text)', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{fmtKW(p.it_watts)}</span>
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }} title="Facility = IT + cooling">
        <span style={{ color: 'var(--text-muted)' }}>Facility</span>
        <span style={{ color: 'var(--text)', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{fmtKW(p.facility_watts)}</span>
      </span>
    </span>
  )
}
export default function StatusBar() {
  return (
    <div style={{
      height: 26,
      background: 'linear-gradient(180deg, var(--chrome-a) 0%, var(--chrome-b) 100%)',
      borderTop: '1px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 10px',
      gap: 12,
      flexShrink: 0,
      fontSize: 10,
      color: 'var(--text-muted)',
    }}>
      {/* Live power + PUE. The per-device-type counts that used to fill the
          rest of this bar were removed — the same numbers are on the Live
          Metrics page, per type, with the readings attached. */}
      <PowerReadout />
    </div>
  )
}
