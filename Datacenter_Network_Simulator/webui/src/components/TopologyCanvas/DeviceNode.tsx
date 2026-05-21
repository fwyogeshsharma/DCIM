import { memo, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Handle, Position, type NodeProps } from '@xyflow/react'

const TYPE_COLOR: Record<string, string> = {
  router:        '#c0621a',
  switch:        '#1a7a3c',
  server:        '#1a5faa',
  firewall:      '#9b1c1c',
  load_balancer: '#6b21a8',
  oob_switch:    '#0e7490',
  pdu:           '#b45309',
  floor_pdu:     '#b45309',
  ups:           '#a16207',
  sensor:        '#374151',
}

const TYPE_ICON: Record<string, string> = {
  router:        '⬡',
  switch:        '◈',
  server:        '▣',
  firewall:      '⬔',
  load_balancer: '◎',
  oob_switch:    '◉',
  pdu:           '⚡',
  floor_pdu:     '⚡',
  ups:           '🔋',
  sensor:        '◦',
}

export interface DeviceNodeData {
  name: string
  device_type: string
  vendor: string
  ip_address: string
  mgmt_ip?: string
  os_name?: string
  os_version?: string
  snmp_port?: number
  gnmi_port?: number
  activeLayer?: string
  cpu_usage?: number
  memory_used?: number
  [key: string]: unknown
}

function DeviceNode({ data, selected, dragging }: NodeProps) {
  const d = data as DeviceNodeData
  const col  = TYPE_COLOR[d.device_type] || '#555'
  const icon = TYPE_ICON[d.device_type]  || '□'
  const [tipPos, setTipPos] = useState<{ x: number; y: number } | null>(null)

  const onEnter   = useCallback((e: React.MouseEvent) => setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onMove    = useCallback((e: React.MouseEvent) => setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onLeave   = useCallback(() => setTipPos(null), [])
  const onContext = useCallback(() => setTipPos(null), [])

  const rows: [string, string][] = [
    ['Type',   d.device_type.replace(/_/g, ' ')],
    ['Vendor', d.vendor],
  ]
  if (d.os_name)    rows.push(['OS',      d.os_name])
  if (d.os_version) rows.push(['Version', d.os_version])
  if (d.ip_address) rows.push(['Prod IP', d.ip_address])
  if (d.mgmt_ip)    rows.push(['Mgmt IP', d.mgmt_ip])
  rows.push(
    ['SNMP Port', String(d.snmp_port ?? 161)],
    ['gNMI Port', String(d.gnmi_port ?? 57400)],
  )

  return (
    <div
      style={{
        background: col,
        border: `2px solid ${selected ? '#fff' : col}`,
        borderRadius: 5,
        padding: '4px 8px',
        minWidth: 68,
        textAlign: 'center',
        cursor: 'pointer',
        boxShadow: selected ? `0 0 0 2px #fff4` : `0 2px 6px rgba(0,0,0,0.5)`,
        transition: 'box-shadow 0.15s',
        position: 'relative',
      }}
      onMouseEnter={onEnter}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      onContextMenu={onContext}
    >
      <Handle type="target" position={Position.Top}    style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Left}   style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right}  style={{ opacity: 0 }} />

      <div style={{ fontSize: 13, lineHeight: 1 }}>{icon}</div>
      <div style={{
        fontSize: 9,
        fontWeight: 700,
        color: '#fff',
        marginTop: 2,
        maxWidth: 72,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>
        {d.name}
      </div>
      {(() => {
        const layer = d.activeLayer || 'all'
        const prod  = d.ip_address
        const mgmt  = d.mgmt_ip || ''
        if (layer === 'production') {
          return <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.7)' }}>{prod}</div>
        }
        if (layer === 'management' || layer === 'power') {
          return <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.7)' }}>{mgmt || prod}</div>
        }
        if (prod && mgmt) {
          return (
            <>
              <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.7)' }}>{prod}</div>
              <div style={{ fontSize: 7, color: 'rgba(255,255,255,0.5)' }}>{mgmt}</div>
            </>
          )
        }
        return <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.7)' }}>{prod || mgmt}</div>
      })()}

      {typeof d.cpu_usage === 'number' && d.cpu_usage > 0 && (
        <div style={{
          position: 'absolute', bottom: -3, left: 2, right: 2,
          height: 3, background: 'rgba(0,0,0,0.4)', borderRadius: 1,
        }}>
          <div style={{
            height: '100%',
            width: `${Math.min(100, d.cpu_usage)}%`,
            background: d.cpu_usage > 80 ? '#f85149' : '#3fb950',
            borderRadius: 1,
          }} />
        </div>
      )}

      {tipPos && !dragging && createPortal(
        <div style={{
          position: 'fixed',
          left: tipPos.x,
          top: tipPos.y,
          zIndex: 9999,
          background: '#0d1525',
          border: '1px solid #1e3048',
          borderRadius: 6,
          padding: '8px 10px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.8)',
          minWidth: 200,
          pointerEvents: 'none',
          fontFamily: 'system-ui, -apple-system, sans-serif',
        }}>
          <div style={{
            fontWeight: 700, color: '#e6edf3', fontSize: 12,
            marginBottom: 6, paddingBottom: 4,
            borderBottom: '1px solid #1e3048',
          }}>
            {d.name}
          </div>
          {rows.map(([k, v]) => (
            <div key={k} style={{
              display: 'flex', justifyContent: 'space-between',
              gap: 16, lineHeight: 1.85, fontSize: 10.5,
            }}>
              <span style={{ color: '#8b949e' }}>{k}:</span>
              <span style={{
                color: '#e6edf3',
                fontFamily: (k.includes('IP') || k.includes('Port'))
                  ? 'Consolas, monospace' : undefined,
              }}>
                {v}
              </span>
            </div>
          ))}
        </div>,
        document.body
      )}
    </div>
  )
}

export default memo(DeviceNode)
