import type { ReactNode } from 'react'

// Shared "Targets" group-box used by every protocol panel (SNMP, gNMI,
// sFlow, BACnet, Redfish) so the pre-start target list looks identical
// everywhere. Panels compute their own per-type rows + total and pass them
// in; this component owns the markup, spacing and Total styling.

export interface TargetRow {
  label: string
  value: number | string
}

export function StatRow({ label, value, labelColor, valueColor }: {
  label: string; value: number | string
  labelColor?: string; valueColor?: string
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, lineHeight: 1.85 }}>
      <span style={{ color: labelColor ?? 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: valueColor ?? 'var(--text)' }}>{value}</span>
    </div>
  )
}

export default function TargetsBox({ rows, total, footer }: {
  rows: TargetRow[]
  total: number
  footer?: ReactNode
}) {
  return (
    <div className="group-box" style={{ marginTop: 6 }}>
      <span className="group-box-label">Targets</span>
      {rows.map(r => (
        <StatRow key={r.label} label={r.label} value={r.value} />
      ))}
      <StatRow label="Total:" value={total} labelColor="#06b6d4" valueColor="#06b6d4" />
      {footer}
    </div>
  )
}
