import { memo, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Handle, Position, NodeToolbar, type NodeProps } from '@xyflow/react'
import { useStore } from '../../store/useStore'
import { nodeColor } from '../../theme'

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
  standby?: boolean
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
  // This chiller latched out on a high head-pressure trip → anchor a warning callout
  // to THIS node (not a global banner) so the operator sees which unit and can reset.
  // Primitive selectors keep it a per-node re-render.
  const tripped     = useStore(s => s.chillerTrips.some(c => c.device === id))
  const tripDegraded = useStore(s => !!s.chillerTrips.find(c => c.device === id)?.degraded)
  const resetTrip   = useStore(s => s.resetChillerTrip)
  const col  = nodeColor(d.device_type)
  const icon = TYPE_ICON[d.device_type]  || '□'
  const poweredOff = d.power_state === 'Off'
  // Staged-OFF cooling unit (N+1 spare) — healthy but idle, so fade it. Live per-node
  // selector (stable boolean) so the fade tracks failover/staging without re-fetching
  // the graph. A tripped or faulted unit stays prominent (its red styling wins).
  const standby = useStore(s => s.plantStandby.includes(id)) && !tripped && !faulted
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
    <>
    {tripped && (
      <NodeToolbar isVisible position={Position.Top} offset={12}>
        <div style={{
          position: 'relative',
          width: 172,
          background: tripDegraded ? 'var(--crit-solid-bg)' : 'var(--warn-solid-bg)',
          border: `1px solid ${tripDegraded ? 'var(--crit)' : 'var(--warn-strong)'}`,
          borderRadius: 6, padding: '8px 10px',
          color: tripDegraded ? 'var(--crit-solid-fg)' : 'var(--warn-solid-fg)', fontSize: 11, lineHeight: 1.35,
          boxShadow: '0 4px 14px rgba(0,0,0,0.55)',
          display: 'flex', flexDirection: 'column', gap: 7,
        }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
            <span style={{ fontSize: 13, lineHeight: '15px' }}>⚠</span>
            <span>High head-pressure trip{tripDegraded ? ' — cooling reduced' : ' — on standby'}</span>
          </div>
          <button onClick={() => resetTrip(id)} style={{
            alignSelf: 'flex-start',
            background: tripDegraded ? 'var(--crit)' : 'var(--warn-strong)', border: 'none',
            borderRadius: 4, color: tripDegraded ? 'var(--on-solid)' : 'var(--on-warn-strong)',
            padding: '3px 12px', fontSize: 11, fontWeight: 600, cursor: 'pointer',
          }}>Reset</button>
          {/* caret pointing down at the node */}
          <div style={{
            position: 'absolute', bottom: -6, left: '50%', marginLeft: -6,
            width: 0, height: 0, borderLeft: '6px solid transparent',
            borderRight: '6px solid transparent',
            borderTop: `6px solid ${tripDegraded ? 'var(--crit)' : 'var(--warn-strong)'}`,
          }} />
        </div>
      </NodeToolbar>
    )}
    <div
      style={{
        background: col,
        border: `2px solid ${selected ? 'var(--on-solid)' : col}`,
        borderRadius: 5,
        padding: '4px 8px',
        minWidth: 68,
        textAlign: 'center',
        cursor: 'pointer',
        boxShadow: selected ? `0 0 0 2px var(--on-solid)4` : `0 2px 6px rgba(0,0,0,0.5)`,
        transition: 'box-shadow 0.15s, opacity 0.3s, filter 0.3s',
        position: 'relative',
        opacity: poweredOff ? 0.35 : (standby ? 0.5 : 1),
        filter: poweredOff ? 'grayscale(0.7)' : (standby ? 'grayscale(0.55)' : undefined),
        // An injected CONDITION — or a latched chiller trip — outranks the normal type
        // colour: the node turns red and blinks until it's cleared / reset.
        ...(faulted || tripped ? { background: 'var(--red)', borderColor: 'var(--red)' } : null),
      }}
      className={faulted || tripped ? 'node-faulted' : undefined}
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
        color: 'var(--on-solid)',
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
            background: d.cpu_usage > 80 ? 'var(--crit)' : 'var(--ok)',
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
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          borderRadius: 6,
          padding: '8px 10px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.8)',
          minWidth: 200,
          pointerEvents: 'none',
          fontFamily: 'system-ui, -apple-system, sans-serif',
        }}>
          <div style={{
            fontWeight: 700, color: 'var(--text)', fontSize: 12,
            marginBottom: 6, paddingBottom: 4,
            borderBottom: '1px solid var(--border)',
          }}>
            {d.name}
          </div>
          {rows.map(([k, v]) => (
            <div key={k} style={{
              display: 'flex', justifyContent: 'space-between',
              gap: 16, lineHeight: 1.85, fontSize: 10.5,
            }}>
              <span style={{ color: 'var(--text-muted)' }}>{k}:</span>
              <span style={{
                color: 'var(--text)',
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
    </>
  )
}

export default memo(DeviceNode)
