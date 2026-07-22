import { useState, useEffect } from 'react'
import { useStore } from '../store/useStore'
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
  if (pue < 1.4) return '#5fb37e'
  if (pue < 2.0) return '#cbb04a'
  return '#d95d52'
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

const DEVICE_TYPE_COLOR: Record<string, string> = {
  router:        'var(--node-router)',
  switch:        'var(--node-switch)',
  server:        'var(--node-server)',
  firewall:      'var(--node-firewall)',
  load_balancer: 'var(--node-lb)',
  oob_switch:    'var(--node-oob)',
  pdu:           'var(--node-pdu)',
  floor_pdu:     'var(--node-pdu)',
  rpp:           '#6a4a63',
  generator:     '#3d3620',
  ups:           'var(--node-ups)',
  sensor:        'var(--node-sensor)',
  energy_monitor:'#3d6470',
  crah:          '#5c8592',
  chiller:       '#33535d',
  pump:          '#c78a2d',
  cooling_tower: '#2c454e',
  valve:         '#3a2a3d',
  cdu:           '#e0a33c',
}

const DEVICE_TYPE_SHORT: Record<string, string> = {
  router:        'RTR',
  switch:        'SW',
  server:        'SRV',
  firewall:      'FW',
  load_balancer: 'LB',
  oob_switch:    'OOB',
  pdu:           'PDU',
  floor_pdu:     'fPDU',
  rpp:           'RPP',
  generator:     'GEN',
  ups:           'UPS',
  sensor:        'SNS',
  energy_monitor:'EV2',
  crah:          'CRAH',
  chiller:       'CHLR',
  pump:          'PMP',
  cooling_tower: 'CT',
  valve:         'VLV',
  cdu:           'CDU',
}

export default function StatusBar() {
  const { devices } = useStore()

  const typeCounts: Record<string, number> = {}
  for (const d of devices) typeCounts[d.device_type] = (typeCounts[d.device_type] || 0) + 1

  return (
    <div style={{
      height: 26,
      background: 'linear-gradient(180deg, #0e0f11 0%, #0e0f11 100%)',
      borderTop: '1px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 10px',
      gap: 12,
      flexShrink: 0,
      fontSize: 10,
      color: 'var(--text-muted)',
    }}>
      {/* ── Left: live power + PUE ─────────────────────────── */}
      <PowerReadout />
      <span style={{ flex: 1 }} />
      {Object.entries(typeCounts).map(([t, n]) => (
        <span key={t} style={{
          display: 'flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap',
        }} title={t}>
          <span style={{
            width: 7, height: 7, borderRadius: 2,
            background: DEVICE_TYPE_COLOR[t] || '#4d4f52',
          }} />
          <span style={{ color: 'var(--text-muted)' }}>{DEVICE_TYPE_SHORT[t] || t}</span>
          <span style={{
            color: 'var(--text)', fontWeight: 600,
            fontVariantNumeric: 'tabular-nums',
          }}>{n}</span>
        </span>
      ))}
    </div>
  )
}
