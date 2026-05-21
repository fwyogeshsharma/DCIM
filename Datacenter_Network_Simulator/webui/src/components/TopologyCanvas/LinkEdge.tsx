import { memo, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react'
import { useStore } from '../../store/useStore'

const LAYER_COLOR: Record<string, string> = {
  production: '#4a9eff',
  management: '#3fb950',
  power:      '#f59e0b',
}

export interface LinkEdgeData {
  layer: string
  broken: boolean
  src_iface?: number
  dst_iface?: number
  [key: string]: unknown
}

function LinkEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY,
          sourcePosition, targetPosition, data, selected, source, target } = props
  const d      = (data ?? {}) as LinkEdgeData
  const broken = Boolean(d.broken)
  const layer  = String(d.layer || 'production')
  const color  = broken ? '#f85149' : (LAYER_COLOR[layer] || '#4a9eff')

  const graphDevices = useStore(s => s.graphDevices)
  const srcName = graphDevices.find(dev => dev.id === source)?.name || source
  const dstName = graphDevices.find(dev => dev.id === target)?.name || target

  const [tipPos, setTipPos] = useState<{ x: number; y: number } | null>(null)
  const onEnter = useCallback((e: React.MouseEvent<SVGPathElement>) =>
    setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onMove  = useCallback((e: React.MouseEvent<SVGPathElement>) =>
    setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onLeave = useCallback(() => setTipPos(null), [])

  const [edgePath] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  })

  const statusColor = broken ? '#f85149' : '#3fb950'
  const statusText  = broken ? 'Broken'  : 'Active'

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: color,
          strokeWidth: selected ? 2.5 : 1.5,
          strokeDasharray: broken ? '5,3' : undefined,
          opacity: broken ? 0.6 : (layer === 'management' ? 0.7 : 1),
        }}
      />

      {/* Wide invisible hit area for reliable hover detection */}
      <path
        d={edgePath}
        stroke="transparent"
        strokeWidth={20}
        fill="none"
        onMouseEnter={onEnter}
        onMouseMove={onMove}
        onMouseLeave={onLeave}
      />

      {broken && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%,-50%) translate(${(sourceX + targetX) / 2}px,${(sourceY + targetY) / 2}px)`,
              background: '#7f1d1d',
              color: '#fca5a5',
              fontSize: 8,
              padding: '1px 4px',
              borderRadius: 3,
              pointerEvents: 'none',
            }}
            className="nodrag nopan"
          >
            DOWN
          </div>
        </EdgeLabelRenderer>
      )}

      {tipPos && createPortal(
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
          minWidth: 180,
          pointerEvents: 'none',
          fontFamily: 'system-ui, -apple-system, sans-serif',
        }}>
          <div style={{
            fontWeight: 700, color: '#e6edf3', fontSize: 12,
            marginBottom: 6, paddingBottom: 4,
            borderBottom: '1px solid #1e3048',
          }}>
            {srcName} ↔ {dstName}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10.5, lineHeight: 1.85 }}>
            <span style={{ color: statusColor, fontSize: 14, lineHeight: 1 }}>●</span>
            <span style={{ color: '#8b949e' }}>Status:</span>
            <span style={{ color: statusColor }}>{statusText}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 10.5, lineHeight: 1.85 }}>
            <span style={{ color: '#8b949e' }}>Layer:</span>
            <span style={{ color: '#e6edf3', textTransform: 'capitalize' }}>{layer}</span>
          </div>
          {(d.src_iface != null || d.dst_iface != null) && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 10.5, lineHeight: 1.85 }}>
              <span style={{ color: '#8b949e' }}>Interfaces:</span>
              <span style={{ color: '#e6edf3', fontFamily: 'Consolas, monospace' }}>
                {d.src_iface ?? '?'} ↔ {d.dst_iface ?? '?'}
              </span>
            </div>
          )}
        </div>,
        document.body
      )}
    </>
  )
}

export default memo(LinkEdge)
