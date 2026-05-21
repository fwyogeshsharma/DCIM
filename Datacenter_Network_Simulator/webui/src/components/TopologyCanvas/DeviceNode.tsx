import { memo } from 'react'
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
  activeLayer?: string
  cpu_usage?: number
  memory_used?: number
  [key: string]: unknown
}

function DeviceNode({ data, selected }: NodeProps) {
  const d = data as DeviceNodeData
  const col = TYPE_COLOR[d.device_type] || '#555'
  const icon = TYPE_ICON[d.device_type] || '□'

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
        // 'all' — show both when both exist
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
    </div>
  )
}

export default memo(DeviceNode)
