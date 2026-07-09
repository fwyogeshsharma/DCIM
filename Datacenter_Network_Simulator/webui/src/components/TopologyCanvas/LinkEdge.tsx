import { memo, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react'

const LAYER_COLOR: Record<string, string> = {
  production: '#4a9eff',
  management: '#3fb950',
  power:      '#f59e0b',
  cooling:    '#22d3ee',   // chilled-/condenser-water flow
}

export const COOL_COLD = '#22d3ee'   // chilled-water supply / cooled condenser return
export const COOL_HOT  = '#fb923c'   // warm return / hot condenser water

// Classify a cooling edge as hot (warm return / condenser-hot) vs cold
// (chilled supply / cooled return) from the device names at its endpoints.
export function isHotFlow(src: string, dst: string): boolean {
  const s = src.toUpperCase(), t = dst.toUpperCase()
  if (s.startsWith('CRAH')) return true            // CHW return: CRAH → CHWR
  if (s.includes('CHWR') || t.includes('CHWR')) return true
  if (s.includes('CWP') || t.includes('CWP')) return true   // condenser-water pump (hot loop)
  if (/^CT\d/.test(t)) return true                 // to a cooling tower (CT1/CT2…) = hot
  if (s.startsWith('VCW') || t.startsWith('VCW')) return true   // condenser-water (VCW) valve
  if (t.startsWith('CDU') && s.startsWith('SRV')) return true   // cold-plate return: server → CDU (hot)
  return false                                     // chilled supply / cooled return = cold
}

export interface LinkEdgeData {
  layer: string
  broken: boolean
  src_iface?: number
  dst_iface?: number
  srcName?: string
  dstName?: string
  flow?: 'hot' | 'cold'
  [key: string]: unknown
}

function LinkEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY,
          sourcePosition, targetPosition, data, selected, source, target,
          markerEnd } = props
  const d      = (data ?? {}) as LinkEdgeData
  const broken = Boolean(d.broken)
  const layer  = String(d.layer || 'production')

  // Name + hot/cold classification are precomputed in linksToEdges and carried
  // in edge data — so this edge does NOT subscribe to the device store and will
  // not re-render on every metrics tick.
  const srcName = d.srcName || source
  const dstName = d.dstName || target

  const isCool = layer === 'cooling'
  const hot    = isCool && d.flow === 'hot'
  const color  = broken ? '#f85149'
               : isCool  ? (hot ? COOL_HOT : COOL_COLD)
               : (LAYER_COLOR[layer] || '#4a9eff')

  const [tipPos, setTipPos] = useState<{ x: number; y: number } | null>(null)
  const onEnter = useCallback((e: React.MouseEvent<SVGPathElement>) =>
    setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onMove  = useCallback((e: React.MouseEvent<SVGPathElement>) =>
    setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onLeave = useCallback(() => setTipPos(null), [])

  // Cooling supply & return run between the same node pair — offset each
  // perpendicular (opposite signs by direction) so both draw as parallel
  // lines instead of overlapping into one.
  let sx = sourceX, sy = sourceY, tx = targetX, ty = targetY
  if (isCool) {
    const off = source < target ? 8 : -8
    const dx = targetX - sourceX, dy = targetY - sourceY
    const len = Math.hypot(dx, dy) || 1
    const ox = -dy / len * off, oy = dx / len * off
    sx += ox; sy += oy; tx += ox; ty += oy
  }
  const [edgePath] = getBezierPath({
    sourceX: sx, sourceY: sy, sourcePosition,
    targetX: tx, targetY: ty, targetPosition,
  })

  const statusColor = broken ? '#f85149' : '#3fb950'
  const statusText  = broken ? 'Broken'  : 'Active'

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: color,
          strokeWidth: selected ? 2.5 : (isCool ? 2.4 : 1.5),
          strokeDasharray: broken ? '5,3' : (isCool ? '7,5' : undefined),
          // Animate dashes along source→target = water flow direction.
          animation: (isCool && !broken) ? 'flow-dash 0.7s linear infinite' : undefined,
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
