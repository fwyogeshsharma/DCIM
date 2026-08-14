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

// PUE colour band: efficient (<1.4) green, typical (1.4–2.0) amber, poor (>2.0) red
function pueColor(pue: number): string {
  if (pue <= 0) return 'var(--text-dim)'
  if (pue < 1.4) return 'var(--ok)'
  if (pue < 2.0) return 'var(--warn)'
  return 'var(--crit)'
}

function PowerReadout() {
  const [p, setP] = useState<PowerSummary | null>(null)
  useEffect(() => {
    let alive = true
    const load = () => api.powerSummary()
      .then(d => { if (alive) setP(d as PowerSummary) })
      .catch(() => {})
    load()
    const t = setInterval(load, 3000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (!p || p.facility_watts <= 0) return null
  const PUE_SOURCE: Record<PueSource, string> = {
    native:   ' (switchgear + UPS output — Green Grid Category 1)',
    meters:   ' (from EV2 meter readings)',
    computed: ' (estimated — no meter hierarchy)',
  }
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 10, whiteSpace: 'nowrap' }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}
        title={`Power Usage Effectiveness = facility ÷ IT${p.source ? PUE_SOURCE[p.source] : ''}`}>
        <span style={{ color: 'var(--text-muted)' }}>PUE</span>
        <span style={{ color: pueColor(p.pue), fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
          {p.source === 'computed' ? '~' : ''}{p.pue > 0 ? p.pue.toFixed(2) : '—'}
        </span>
      </span>
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
