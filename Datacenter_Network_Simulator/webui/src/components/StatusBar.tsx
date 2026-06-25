import { useState, useEffect } from 'react'
import { useStore } from '../store/useStore'
import { api } from '../api/client'

type PowerSummary = {
  it_watts: number; cooling_watts: number; facility_watts: number
  pue: number; source?: 'meters' | 'computed'
}

function fmtKW(w: number): string {
  const kw = w / 1000
  return kw >= 1000 ? `${(kw / 1000).toFixed(2)} MW` : `${kw.toFixed(1)} kW`
}

// PUE colour band: efficient (<1.4) green, typical (1.4–2.0) amber, poor (>2.0) red
function pueColor(pue: number): string {
  if (pue <= 0) return 'var(--text-dim)'
  if (pue < 1.4) return '#3fb950'
  if (pue < 2.0) return '#d29922'
  return '#f85149'
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
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 10, whiteSpace: 'nowrap' }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}
        title={`Power Usage Effectiveness = facility ÷ IT${p.source === 'computed' ? ' (estimated — no meter hierarchy)' : ' (from EV2 meter readings)'}`}>
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
  rpp:           '#7c2d6e',
  generator:     '#713f12',
  ups:           'var(--node-ups)',
  sensor:        'var(--node-sensor)',
  energy_monitor:'#0e7490',
  crah:          '#0891b2',
  chiller:       '#155e75',
  pump:          '#1e6ec8',
  cooling_tower: '#164e63',
  valve:         '#4a044e',
  cdu:           '#2563eb',
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
      background: 'linear-gradient(180deg, #08101a 0%, #060b13 100%)',
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
            background: DEVICE_TYPE_COLOR[t] || '#555',
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
