import { memo, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { useStore } from '../../store/useStore'

const TYPE_COLOR: Record<string, string> = {
  router:        'var(--node-router)',
  switch:        'var(--node-switch)',
  server:        'var(--node-server)',
  firewall:      'var(--node-firewall)',
  load_balancer: 'var(--node-lb)',
  oob_switch:    'var(--node-oob)',
  pdu:           'var(--node-pdu)',
  floor_pdu:     'var(--node-floor-pdu)',
  rpp:           'var(--node-rpp)',
  generator:     'var(--node-generator)',
  ups:           'var(--node-ups)',
  utility_feed:  'var(--node-utility)',
  switchgear:    'var(--node-switchgear)',
  ats:           'var(--node-ats)',
  mcc:           'var(--node-mcc)',
  mpp:           'var(--node-mpp)',
  sensor:        'var(--node-sensor)',
}

// Types whose fill is light enough that a white label drops below 4.5:1.
const DARK_LABEL = new Set([
  'ups', 'ats', 'mpp', 'pdu', 'floor_pdu',
  'energy_monitor', 'crah', 'pump', 'valve',
])

const TYPE_ICON: Record<string, string> = {
  router:        '⬡',
  switch:        '◈',
  server:        '▣',
  firewall:      '⬔',
  load_balancer: '◎',
  oob_switch:    '◉',
  pdu:           '⚡',
  floor_pdu:     '⚡',
  rpp:           '⚡',
  generator:     '⚙',
  ups:           '🔋',
  utility_feed:  '🏛',
  switchgear:    '▦',
  ats:           '⇄',
  mcc:           '⛭',
  mpp:           '❄',
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
  bacnet_instance?: number
  activeLayer?: string
  cpu_usage?: number
  memory_used?: number
  power_state?: string
  [key: string]: unknown
}

function DeviceNode({ id, data, selected, dragging }: NodeProps) {
  const d = data as DeviceNodeData
  // Per-node selector: only the node whose fault state changed re-renders,
  // rather than rebuilding every node on the canvas.
  // Joined string, not the array: a fresh [] each poll would fail reference
  // equality and re-render every node on every tick.
  const faultKeys = useStore(s => (s.faulted[id] || []).join(', '))
  const faulted = faultKeys.length > 0
  const col  = TYPE_COLOR[d.device_type] || '#4d4f52'
  const darkLabel = DARK_LABEL.has(d.device_type)
  const ink       = darkLabel ? 'var(--node-ink-dark)'   : '#fff'
  const subInk    = darkLabel ? 'rgba(23,19,10,0.78)'    : 'rgba(255,255,255,0.7)'
  const subInkDim = darkLabel ? 'rgba(23,19,10,0.55)'    : 'rgba(255,255,255,0.5)'
  const icon = TYPE_ICON[d.device_type]  || '□'
  const poweredOff = d.power_state === 'Off'
  const [tipPos, setTipPos] = useState<{ x: number; y: number } | null>(null)

  const onEnter   = useCallback((e: React.MouseEvent) => setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onMove    = useCallback((e: React.MouseEvent) => setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onLeave   = useCallback(() => setTipPos(null), [])
  const onContext = useCallback(() => setTipPos(null), [])

  const rows: [string, string][] = [
    ['Type',   d.device_type.replace(/_/g, ' ')],
    ['Vendor', d.vendor],
  ]
  if (poweredOff) rows.push(['Power', 'Off (Redfish)'])
  if (faulted)    rows.push(['Condition', faultKeys])
  if (d.os_name)    rows.push(['OS',      d.os_name])
  if (d.os_version) rows.push(['Version', d.os_version])
  if (d.ip_address) rows.push(['Prod IP', d.ip_address])
  if (d.mgmt_ip)    rows.push(['Mgmt IP', d.mgmt_ip])
  rows.push(['SNMP Port', String(d.snmp_port ?? 161)])
  if (['router', 'switch'].includes(d.device_type))
    rows.push(['gNMI Port', String(d.gnmi_port ?? 57400)])
  if (typeof d.bacnet_instance === 'number')
    rows.push(['BACnet Instance', String(d.bacnet_instance)])

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
        transition: 'box-shadow 0.15s, opacity 0.3s, filter 0.3s',
        position: 'relative',
        opacity: poweredOff ? 0.35 : 1,
        filter: poweredOff ? 'grayscale(0.7)' : undefined,
        // An injected CONDITION outranks the normal type colour: the node turns
        // red and blinks until the condition is returned to normal.
        ...(faulted ? { background: 'var(--red)', borderColor: 'var(--red)' } : null),
      }}
      className={faulted ? 'node-faulted' : undefined}
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
        color: ink,
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
          return <div style={{ fontSize: 8, color: subInk }}>{prod}</div>
        }
        if (layer === 'management' || layer === 'power') {
          return <div style={{ fontSize: 8, color: subInk }}>{mgmt || prod}</div>
        }
        if (prod && mgmt) {
          return (
            <>
              <div style={{ fontSize: 8, color: subInk }}>{prod}</div>
              <div style={{ fontSize: 7, color: subInkDim }}>{mgmt}</div>
            </>
          )
        }
        return <div style={{ fontSize: 8, color: subInk }}>{prod || mgmt}</div>
      })()}

      {typeof d.cpu_usage === 'number' && d.cpu_usage > 0 && (
        <div style={{
          position: 'absolute', bottom: -3, left: 2, right: 2,
          height: 3, background: 'rgba(0,0,0,0.4)', borderRadius: 1,
        }}>
          <div style={{
            height: '100%',
            width: `${Math.min(100, d.cpu_usage)}%`,
            background: d.cpu_usage > 80 ? '#d95d52' : '#5fb37e',
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
          background: '#191b1f',
          border: '1px solid #2c3037',
          borderRadius: 6,
          padding: '8px 10px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.8)',
          minWidth: 200,
          pointerEvents: 'none',
          fontFamily: 'system-ui, -apple-system, sans-serif',
        }}>
          <div style={{
            fontWeight: 700, color: '#dcdbd7', fontSize: 12,
            marginBottom: 6, paddingBottom: 4,
            borderBottom: '1px solid #2c3037',
          }}>
            {d.name}
          </div>
          {rows.map(([k, v]) => (
            <div key={k} style={{
              display: 'flex', justifyContent: 'space-between',
              gap: 16, lineHeight: 1.85, fontSize: 10.5,
            }}>
              <span style={{ color: '#8b8d8c' }}>{k}:</span>
              <span style={{
                color: '#dcdbd7',
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
